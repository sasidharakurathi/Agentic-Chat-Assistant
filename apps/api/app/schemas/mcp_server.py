"""Request/response shapes for MCP servers (task 4.3).

The rule from database connections carries over: **a credential goes in and
never comes back out.** Header and environment values are write-only, sealed
in `secrets`, and the summary returns only their names.

The fields that *are* stored and shown in plain text (the command, its
arguments, the URL) refuse anything that looks like a credential, and say
where it belongs instead: an API key in `--token sk-…` or `?api_key=…` would
otherwise sit in the database unencrypted and appear on every screen.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import AfterValidator, BaseModel, Field, model_validator

from app.config import settings
from app.mcp.limits import SandboxLimits
from app.models.integration import McpServerStatus, McpTransport
from app.schemas.common import ApiModel, ORMModel
from app.security.redact import strip_secrets

Transport = Literal["stdio", "http", "sse"]

#: Also the prefix of the server's tool names (`mcp__<name>__<tool>`): no
#: underscores, so the name can never be confused with the separator, and
#: never `caps`, the platform's own server.
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
RESERVED_NAMES = frozenset({"caps"})
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
#: The transport owns these; a user-supplied one would break or redirect it.
RESERVED_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "upgrade", "content-type"}
)
#: Query parameters that carry a credential. Checked case-insensitively.
_CREDENTIAL_PARAMS = frozenset(
    {"key", "api_key", "apikey", "token", "access_token", "auth", "secret", "password"}
)
MAX_ENTRIES = 50
#: Variables the dynamic loader acts on (Linux, macOS).
_LOADER_ENV = ("LD_", "DYLD_")
MAX_VALUE = 8_000


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError(
            "name must start with a letter and use only lowercase letters, digits and "
            "hyphens (up to 40): it becomes part of every tool name this server provides"
        )
    if name in RESERVED_NAMES:
        raise ValueError(f"'{name}' is reserved for the platform's own tools")
    return name


def _looks_secret(value: str) -> bool:
    return strip_secrets(value) != value


def check_args(args: list[str]) -> list[str]:
    for arg in args:
        if _looks_secret(arg):
            raise ValueError(
                "an argument looks like a credential. Arguments are stored and shown in plain "
                "text; pass secrets as environment variables instead, which are encrypted"
            )
    return args


def check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("url must be an absolute https:// URL")
    if parts.scheme == "http" and not settings.mcp_allow_insecure_urls:
        raise ValueError(
            "url must use https: headers often carry credentials, and plain http sends "
            "them readable to anyone on the path"
        )
    if parts.username or parts.password:
        raise ValueError("put credentials in a header, not in the URL")
    bad = sorted({k for k, _ in parse_qsl(parts.query) if k.lower() in _CREDENTIAL_PARAMS})
    if bad or _looks_secret(url):
        raise ValueError(
            "the URL appears to carry a credential"
            + (f" ({', '.join(bad)})" if bad else "")
            + ". URLs are stored and shown in plain text; send it as a header instead, "
            "which is encrypted"
        )
    return url


def check_headers(headers: dict[str, str]) -> dict[str, str]:
    if len(headers) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} headers")
    for name, value in headers.items():
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"'{name}' is not a valid header name")
        if name.lower() in RESERVED_HEADERS:
            raise ValueError(f"the {name} header is set by the connection itself")
        if len(value) > MAX_VALUE or "\n" in value or "\r" in value:
            raise ValueError(f"the value of {name} is too long or spans lines")
    return headers


def check_env(env: dict[str, str]) -> dict[str, str]:
    if len(env) > MAX_ENTRIES:
        raise ValueError(f"at most {MAX_ENTRIES} environment variables")
    for key, value in env.items():
        if not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"'{key}' is not a valid environment variable name")
        if key.upper().startswith(_LOADER_ENV):
            # The dynamic loader reads these before any limit is applied:
            # `LD_PRELOAD` would run code inside the jail's own start-up.
            raise ValueError(f"'{key}' changes how programs are loaded and can't be set")
        if len(value) > MAX_VALUE or "\x00" in value:
            raise ValueError(f"the value of {key} is too long or contains a NUL byte")
    return env


Name = Annotated[str, AfterValidator(check_name)]
Args = Annotated[list[str], Field(max_length=MAX_ENTRIES), AfterValidator(check_args)]
Url = Annotated[str, Field(max_length=2048), AfterValidator(check_url)]
Headers = Annotated[dict[str, str], AfterValidator(check_headers)]
Env = Annotated[dict[str, str], AfterValidator(check_env)]


class McpServerCreate(BaseModel):
    name: Name
    transport: Transport
    #: stdio only: the executable (e.g. `npx`, `uvx`, a path). Never a shell line.
    command: str | None = Field(default=None, max_length=500)
    args: Args = Field(default_factory=list)
    #: http / sse only.
    url: Url | None = None
    #: Write-only, http / sse only. Sealed; never returned.
    headers: Headers = Field(default_factory=dict)
    #: Write-only, stdio only. Sealed; never returned.
    env: Env = Field(default_factory=dict)
    enabled: bool = True
    #: stdio only: resource limits (task 4.4). Omitted fields take defaults.
    sandbox: SandboxLimits | None = None

    @model_validator(mode="after")
    def _transport_needs_its_fields(self) -> McpServerCreate:
        if self.transport == "stdio":
            if not (self.command and self.command.strip()):
                raise ValueError("a stdio server needs a command")
            if self.url or self.headers:
                raise ValueError("a stdio server takes a command and environment, not a URL")
            self.command = self.command.strip()
        else:
            if not self.url:
                raise ValueError(f"an {self.transport} server needs a url")
            if self.command or self.args or self.env or self.sandbox:
                raise ValueError(f"an {self.transport} server takes a URL and headers")
        return self


class McpServerUpdate(BaseModel):
    """Omit a field to leave it alone. `headers` / `env`: send an object to
    replace them all, `{}` to clear them. The transport cannot change; delete
    and re-add the server instead."""

    name: Name | None = None
    command: str | None = Field(default=None, max_length=500)
    args: Args | None = None
    url: Url | None = None
    headers: Headers | None = None
    env: Env | None = None
    enabled: bool | None = None
    sandbox: SandboxLimits | None = None


class McpToolOut(ApiModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    #: From the server's `readOnlyHint`; None when it did not say.
    read_only: bool | None = None


class McpServerSummary(ORMModel):
    """Everything safe to show. Header and environment values are absent by
    construction: there is no field that could carry them."""

    id: uuid.UUID
    assistant_id: uuid.UUID
    name: str
    transport: McpTransport
    command: str | None
    args: list[str]
    url: str | None
    header_names: list[str]
    env_keys: list[str]
    enabled: bool
    #: The limits it runs under (stdio), defaults filled in.
    sandbox: SandboxLimits
    tools: list[McpToolOut]
    tools_discovered_at: datetime | None
    status: McpServerStatus
    last_checked_at: datetime | None
    error: str | None
    created_at: datetime


class McpCheckResult(ApiModel):
    """The outcome of a health check or a tool discovery (task 4.5).

    `ok: false` with a reason, not an HTTP error: a wrong header or a server
    that is down is a normal thing to find while setting one up, and a 500
    would make the form look broken instead of the server."""

    ok: bool
    error: str | None = None
    elapsed_ms: int = 0
    #: Discovery only: how many tools were stored.
    tool_count: int | None = None
    #: Discovery only: tools left out, and why.
    skipped: list[str] = Field(default_factory=list)


class McpPresetField(ApiModel):
    name: str
    hint: str


class McpPresetOut(ApiModel):
    """One entry of the catalog (task 4.8): what the Add form is filled with."""

    key: str
    name: str
    title: str
    description: str
    transport: Transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    #: Secrets the builder must supply; names and hints only, never values.
    env: list[McpPresetField] = Field(default_factory=list)
    headers: list[McpPresetField] = Field(default_factory=list)
    docs_url: str
    needs_network: bool = True


class McpRunnerStatus(ApiModel):
    """Where local-command servers run, and what protects them there."""

    reachable: bool
    #: Why not, when it isn't.
    error: str | None = None
    platform: str | None = None
    #: "none" when the runner has no route out (the Docker deployment).
    network: str | None = None
    #: The protections a session actually gets, in words.
    applied: list[str] = Field(default_factory=list)
    #: True when resource limits (memory, CPU, processes) are enforced.
    full_sandbox: bool = False


__all__ = [
    "NAME_RE",
    "McpCheckResult",
    "McpPresetOut",
    "McpRunnerStatus",
    "McpServerCreate",
    "McpServerSummary",
    "McpServerUpdate",
    "McpToolOut",
    "Transport",
    "check_url",
]
