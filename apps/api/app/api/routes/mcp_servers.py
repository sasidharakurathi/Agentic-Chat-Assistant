"""MCP server registrations (task 4.3, plan §8 "MCP & tools")."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    ActiveMembership,
    AssistantContext,
    AssistantCtx,
    ClientIP,
    EditableAssistantCtx,
    PageQuery,
    SessionDep,
    per_user_limit,
)
from app.api.errors import Forbidden
from app.mcp.presets import PRESETS
from app.models.enums import MemberRole
from app.schemas.common import Message, Page
from app.schemas.mcp_server import (
    McpCheckResult,
    McpPresetField,
    McpPresetOut,
    McpRunnerStatus,
    McpServerCreate,
    McpServerSummary,
    McpServerUpdate,
)
from app.services import mcp_discovery, mcp_runner
from app.services import mcp_servers as svc

router = APIRouter(tags=["mcp-servers"])

#: Reaches other systems or starts a job: limited per caller (task 5.8).
HEAVY = per_user_limit("heavy", "rate_limit_heavy")


@router.get("/assistants/{assistant_id}/mcp-servers", response_model=Page[McpServerSummary])
async def list_servers(
    ctx: AssistantCtx, session: SessionDep, page: PageQuery
) -> Page[McpServerSummary]:
    rows = await svc.page_for_assistant(
        session, ctx.assistant.id, limit=page.limit, cursor=page.cursor
    )
    return Page(items=[svc.to_summary(r) for r in rows.items], next_cursor=rows.next_cursor)


def _commands_are_for_admins(ctx: AssistantContext, transport: str) -> None:
    """A local-command server is a program the runner starts: `sh -c ...` is
    a valid one. Adding, changing and starting those is for the org's admins
    and owners (task 6.3). It used to be open to any member, since anyone
    can create an assistant and is then its editor. Remote servers, which
    run somewhere else, stay with the assistant's editors."""
    if transport == "stdio" and not ctx.membership.role.satisfies(MemberRole.admin):
        raise Forbidden(
            "Only an admin or owner can add or start a local-command MCP server: it runs a "
            "program on this server. A remote (URL) server doesn't need one.",
            code="insufficient_role",
        )


@router.post(
    "/assistants/{assistant_id}/mcp-servers",
    response_model=McpServerSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_server(
    body: McpServerCreate, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> McpServerSummary:
    _commands_are_for_admins(ctx, body.transport)
    server = await svc.create(
        session, assistant=ctx.assistant, body=body, user_id=ctx.membership.user_id, ip=ip
    )
    return svc.to_summary(server)


@router.get("/assistants/{assistant_id}/mcp-servers/{server_id}", response_model=McpServerSummary)
async def get_server(
    ctx: AssistantCtx, session: SessionDep, server_id: uuid.UUID
) -> McpServerSummary:
    server = await svc.get(session, assistant_id=ctx.assistant.id, server_id=server_id)
    return svc.to_summary(server)


@router.patch("/assistants/{assistant_id}/mcp-servers/{server_id}", response_model=McpServerSummary)
async def update_server(
    body: McpServerUpdate,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
    server_id: uuid.UUID,
) -> McpServerSummary:
    server = await svc.get(session, assistant_id=ctx.assistant.id, server_id=server_id)
    _commands_are_for_admins(ctx, server.transport.value)
    server = await svc.update(session, server, body, user_id=ctx.membership.user_id, ip=ip)
    return svc.to_summary(server)


@router.delete("/assistants/{assistant_id}/mcp-servers/{server_id}", response_model=Message)
async def delete_server(
    ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP, server_id: uuid.UUID
) -> Message:
    server = await svc.get(session, assistant_id=ctx.assistant.id, server_id=server_id)
    await svc.delete(session, server, user_id=ctx.membership.user_id, ip=ip)
    return Message(message="deleted")


@router.post(
    "/assistants/{assistant_id}/mcp-servers/{server_id}:health",
    dependencies=[HEAVY],
    response_model=McpCheckResult,
)
async def check_server(
    ctx: EditableAssistantCtx, session: SessionDep, server_id: uuid.UUID
) -> McpCheckResult:
    """Connect and complete the MCP handshake; record the outcome on the
    server. For a local command, this starts it in the runner."""
    server = await svc.get(session, assistant_id=ctx.assistant.id, server_id=server_id)
    _commands_are_for_admins(ctx, server.transport.value)
    return await mcp_discovery.check(session, server)


@router.post(
    "/assistants/{assistant_id}/mcp-servers/{server_id}:discover-tools",
    dependencies=[HEAVY],
    response_model=McpCheckResult,
)
async def discover_tools(
    ctx: EditableAssistantCtx, session: SessionDep, server_id: uuid.UUID
) -> McpCheckResult:
    """Connect, list the server's tools, and store them on the server."""
    server = await svc.get(session, assistant_id=ctx.assistant.id, server_id=server_id)
    _commands_are_for_admins(ctx, server.transport.value)
    return await mcp_discovery.discover(session, server)


@router.get("/mcp-presets", response_model=list[McpPresetOut])
async def list_presets(_m: ActiveMembership) -> list[McpPresetOut]:
    """A short catalog of well-known MCP servers, to fill the Add form with
    (task 4.8). Secrets are named, never supplied."""
    return [
        McpPresetOut(
            key=p.key,
            name=p.name,
            title=p.title,
            description=p.description,
            transport=p.transport,
            command=p.command,
            args=list(p.args),
            url=p.url,
            env=[McpPresetField(name=f.name, hint=f.hint) for f in p.env],
            headers=[McpPresetField(name=f.name, hint=f.hint) for f in p.headers],
            docs_url=p.docs_url,
            needs_network=p.needs_network,
        )
        for p in PRESETS
    ]


@router.get("/mcp-runner", response_model=McpRunnerStatus)
async def runner_status(_m: ActiveMembership) -> McpRunnerStatus:
    """Whether local-command (stdio) servers can run, and how well they are
    contained, so the MCP tab can say so instead of letting a partial
    sandbox pass as a full one."""
    try:
        health = await mcp_runner.health()
    except mcp_runner.RunnerUnavailable as exc:
        return McpRunnerStatus(reachable=False, error=str(exc))
    applied = [str(a) for a in health.get("applied", [])]
    return McpRunnerStatus(
        reachable=True,
        platform=health.get("platform"),
        network=health.get("network"),
        applied=applied,
        full_sandbox={"memory", "cpu time", "processes"} <= set(applied),
    )


__all__ = ["router"]
