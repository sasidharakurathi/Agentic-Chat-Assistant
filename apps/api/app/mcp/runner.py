"""The MCP runner: stdio servers, run somewhere else (task 4.4, plan §7.2).

A stdio MCP server is a program a builder chose: often `npx some-package`,
code nobody here has read. It must not run inside the API, which holds the
key that opens every stored credential, the database password and the model
API key. So it runs here, in a separate process (in Docker, a separate
container with no secrets, no capabilities, a read-only filesystem and no
route to the internet), and the API talks to it over MCP's own HTTP
transport.

    POST   /sessions                start a server; returns where to reach it
    *      /sessions/{id}/mcp       the server, over streamable HTTP
    GET    /sessions/{id}           is it still running, and if not, why
    DELETE /sessions/{id}           stop it
    GET    /healthz                 what this runner can enforce

`/sessions` needs the runner token (`MCP_RUNNER_TOKEN`), shared only with
the API. Each session gets its own random token, and its MCP endpoint
accepts nothing else, so one session cannot reach another's server.

**The bridge is transport-level.** The runner does not interpret MCP: it
moves messages between the HTTP transport and the child's stdin/stdout.
Whatever protocol revision the client and server agree on passes through,
and deciding which tools may be called stays with the API (the PreToolUse
gate), which knows the assistant's configuration.

**What a session runs under:**

- its own empty temporary directory as working directory and `HOME`,
  deleted when the session ends;
- an environment of the platform basics (`PATH` and a few others) plus the
  server's own variables, and nothing else: none of this process's
  environment leaks through;
- on POSIX, `app.mcp.jail`: resource limits, a new session, `no_new_privs`,
  and no root;
- a wall-clock limit and an idle timeout, enforced here on every platform.

Configured from its own environment variables, deliberately not from
`app.config`, which would read the platform's `.env` and its secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.streamable_http import StreamableHTTPServerTransport
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.mcp.limits import SandboxLimits
from app.security.redact import strip_secrets

STDERR_TAIL = 2_000
#: How long a finished session is kept so its end can still be read.
ENDED_KEPT_S = 60.0
POSIX = os.name == "posix"
JAIL = Path(__file__).with_name("jail.py")


@dataclass(frozen=True)
class RunnerConfig:
    token: str
    host: str = "127.0.0.1"
    port: int = 8100
    #: What the network around this runner allows, as deployed: "none" in
    #: the compose file (an internal-only network), "host" when run locally.
    #: Reported, not enforced here: the container network enforces it.
    network: str = "host"
    max_sessions: int = 32

    @classmethod
    def from_env(cls) -> RunnerConfig:
        token = os.environ.get("MCP_RUNNER_TOKEN", "")
        if len(token) < 16:  # noqa: PLR2004
            raise SystemExit("MCP_RUNNER_TOKEN must be set (at least 16 characters)")
        return cls(
            token=token,
            host=os.environ.get("MCP_RUNNER_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_RUNNER_PORT", "8100")),
            network=os.environ.get("MCP_RUNNER_NETWORK", "host"),
            max_sessions=int(os.environ.get("MCP_RUNNER_MAX_SESSIONS", "32")),
        )


def enforced(network: str) -> dict[str, Any]:
    """What a session is actually protected by on this runner, stated rather
    than assumed, so the API can show a partial sandbox as partial."""
    applied = ["clean environment", "private temp directory", "wall clock", "idle timeout"]
    if POSIX:
        applied += [
            "memory",
            "cpu time",
            "processes",
            "open files",
            "file size",
            "no core dumps",
            "new session",
        ]
        if sys.platform.startswith("linux"):
            applied.append("no new privileges")
    return {"platform": sys.platform, "network": network, "applied": applied}


class SessionRequest(BaseModel):
    command: str = Field(min_length=1, max_length=500)
    args: list[str] = Field(default_factory=list, max_length=50)
    env: dict[str, str] = Field(default_factory=dict)
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
    #: For logs only (the server's name).
    label: str = Field(default="", max_length=80)


@dataclass
class Session:
    id: str
    token: str
    label: str
    limits: SandboxLimits
    workdir: Path
    transport: StreamableHTTPServerTransport
    ready: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)
    started: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    ended_reason: str | None = None
    ended_at: float | None = None
    task: asyncio.Task[None] | None = None

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def stderr_tail(self) -> str:
        path = self.workdir / ".stderr.log"
        try:
            data = path.read_bytes()[-STDERR_TAIL:]
        except OSError:
            return ""
        # Servers print what they please, credentials included.
        return strip_secrets(data.decode("utf-8", errors="replace")).strip()


def child_environment(workdir: Path, env: dict[str, str]) -> dict[str, str]:
    """The server's own variables, with HOME and the temp dirs pointed at its
    private directory. The MCP client adds the platform basics (PATH and a
    few others) and nothing more."""
    private = str(workdir)
    return {**env, "HOME": private, "TMPDIR": private, "TEMP": private, "TMP": private}


def spawn_parameters(req: SessionRequest, workdir: Path) -> StdioServerParameters:
    env = child_environment(workdir, req.env)
    if POSIX:
        # By path and isolated (-I): the jail runs in the server's private
        # directory, where the platform's package is not, and must not be,
        # importable.
        return StdioServerParameters(
            command=sys.executable,
            args=["-I", str(JAIL), req.limits.model_dump_json(), "--", req.command, *req.args],
            env=env,
            cwd=workdir,
        )
    return StdioServerParameters(command=req.command, args=list(req.args), env=env, cwd=workdir)


class Runner:
    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.sessions: dict[str, Session] = {}

    # ── lifecycle ────────────────────────────────────────────

    async def start(self, req: SessionRequest) -> Session:
        workdir = Path(tempfile.mkdtemp(prefix="mcp-"))
        sid = secrets.token_hex(16)
        session = Session(
            id=sid,
            token=secrets.token_urlsafe(32),
            label=req.label,
            limits=req.limits,
            workdir=workdir,
            transport=StreamableHTTPServerTransport(mcp_session_id=sid),
        )
        self.sessions[sid] = session
        session.task = asyncio.create_task(self._run(session, spawn_parameters(req, workdir)))
        with anyio.move_on_after(10):
            await session.ready.wait()
        return session

    async def _run(self, session: Session, params: StdioServerParameters) -> None:
        reason = "the server exited"
        try:
            with (
                anyio.move_on_after(session.limits.wall_clock_s) as wall,
                (session.workdir / ".stderr.log").open("w", encoding="utf-8") as errlog,
            ):
                async with (
                    stdio_client(params, errlog=errlog) as (child_read, child_write),
                    session.transport.connect() as (http_read, http_write),
                    anyio.create_task_group() as tg,
                ):
                    session.ready.set()

                    async def pump(src: Any, dst: Any, ended: str) -> None:
                        nonlocal reason
                        async for message in src:
                            if isinstance(message, Exception):
                                continue
                            session.touch()
                            await dst.send(message)
                        # Whichever side finishes first ends the session,
                        # and says which side it was.
                        if not tg.cancel_scope.cancel_called:
                            reason = ended
                        tg.cancel_scope.cancel()

                    async def idle() -> None:
                        nonlocal reason
                        while True:
                            await anyio.sleep(1)
                            quiet = time.monotonic() - session.last_used
                            if quiet > session.limits.idle_timeout_s:
                                reason = f"idle for {session.limits.idle_timeout_s}s"
                                tg.cancel_scope.cancel()
                                return

                    tg.start_soon(pump, http_read, child_write, "the client closed the session")
                    tg.start_soon(pump, child_read, http_write, "the server exited")
                    tg.start_soon(idle)
            if wall.cancelled_caught:
                reason = f"reached its {session.limits.wall_clock_s}s time limit"
        except OSError as exc:
            reason = f"could not start: {exc.strerror or exc}"
        except Exception as exc:  # the bridge must never take the runner down
            reason = f"stopped: {type(exc).__name__}"
        finally:
            session.ended_reason = reason
            tail = session.stderr_tail()
            if tail and reason == "the server exited":
                session.ended_reason = f"{reason}: {tail.splitlines()[-1][:300]}"
            session.ended_at = time.monotonic()
            session.ready.set()
            session.done.set()
            with contextlib.suppress(OSError):
                shutil.rmtree(session.workdir, ignore_errors=True)

    async def stop(self, session: Session) -> None:
        if session.task is not None and not session.task.done():
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.task
        if session.ended_reason is None or session.ended_reason == "the server exited":
            session.ended_reason = "stopped"
        self.sessions.pop(session.id, None)

    async def stop_all(self) -> None:
        for session in list(self.sessions.values()):
            await self.stop(session)

    # ── HTTP ─────────────────────────────────────────────────

    def _authorized(self, request: Request) -> bool:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(given.encode(), self.config.token.encode())

    def _purge(self) -> None:
        """Forget sessions that ended a while ago. They stay briefly so the API
        can still ask why one stopped."""
        now = time.monotonic()
        for sid, s in list(self.sessions.items()):
            if s.ended_at is not None and now - s.ended_at > ENDED_KEPT_S:
                self.sessions.pop(sid, None)

    async def create(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self._purge()
        live = [s for s in self.sessions.values() if not s.done.is_set()]
        if len(live) >= self.config.max_sessions:
            return JSONResponse({"error": "too many running servers"}, status_code=429)
        try:
            req = SessionRequest.model_validate(await request.json())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)[:500]}, status_code=422)
        session = await self.start(req)
        if session.done.is_set():
            self.sessions.pop(session.id, None)
            return JSONResponse(
                {"error": session.ended_reason or "the server did not start"}, status_code=502
            )
        return JSONResponse(
            {
                "session_id": session.id,
                "path": f"/sessions/{session.id}/mcp",
                "token": session.token,
                "enforced": enforced(self.config.network),
            },
            status_code=201,
        )

    async def status(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session = self.sessions.get(request.path_params["sid"])
        if session is None:
            return JSONResponse({"error": "no such session"}, status_code=404)
        running = not session.done.is_set()
        return JSONResponse(
            {
                "running": running,
                "ended_reason": None if running else session.ended_reason,
                "stderr": session.stderr_tail() if running else "",
            }
        )

    async def delete(self, request: Request) -> Response:
        if not self._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session = self.sessions.get(request.path_params["sid"])
        if session is not None:
            await self.stop(session)
        return Response(status_code=204)

    async def health(self, _request: Request) -> Response:
        live = sum(1 for s in self.sessions.values() if not s.done.is_set())
        return JSONResponse({"ok": True, "sessions": live, **enforced(self.config.network)})


class McpEndpoint:
    """`/sessions/{id}/mcp`, handed to the session's own transport after its
    token checks out. Raw ASGI, because the transport speaks ASGI."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        session = self.runner.sessions.get(scope.get("path_params", {}).get("sid", ""))
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if session is None or not hmac.compare_digest(given.encode(), session.token.encode()):
            # One answer for "no such session" and "wrong token": a guess
            # learns nothing about which sessions exist.
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        if session.done.is_set():
            await JSONResponse(
                {"error": f"this server has stopped ({session.ended_reason})"}, status_code=410
            )(scope, receive, send)
            return
        session.touch()
        await session.transport.handle_request(scope, receive, send)


def build_app(config: RunnerConfig) -> Starlette:
    runner = Runner(config)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await runner.stop_all()

    app = Starlette(
        routes=[
            Route("/healthz", runner.health, methods=["GET"]),
            Route("/sessions", runner.create, methods=["POST"]),
            Route("/sessions/{sid}", runner.status, methods=["GET"]),
            Route("/sessions/{sid}", runner.delete, methods=["DELETE"]),
            Route("/sessions/{sid}/mcp", McpEndpoint(runner), methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )
    app.state.runner = runner
    return app


def main() -> None:
    config = RunnerConfig.from_env()
    uvicorn.run(build_app(config), host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
