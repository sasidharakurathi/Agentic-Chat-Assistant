"""What an assistant remembers about someone (task 5.2).

The memory tool writes files per assistant *and* per person. These routes
let that person see and clear them: by default the signed-in user's own
memory. An embedding app's end users (`external_user_ref`) have no login
here, so their memory is managed by whoever can edit the assistant, the
same people who can already read their conversations.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import delete, select

from app.agent.caps_memory import normalize_path, owner_key
from app.api.deps import AssistantContext, AssistantCtx, SessionDep
from app.api.errors import BadRequest, Forbidden
from app.models.memory import MemoryFile
from app.schemas.memory import MemoryCleared, MemoryFileOut

router = APIRouter(tags=["memory"])

ExternalRef = Annotated[
    str | None,
    Query(max_length=200, description="An embedding app's end user; assistant editors only."),
]


def _owner(ctx: AssistantContext, external_user_ref: str | None) -> str:
    if external_user_ref is not None and not ctx.can_edit:
        raise Forbidden(
            "Only people who can edit this assistant can manage its end users' memory",
            code="not_assistant_editor",
        )
    return owner_key(
        external_user_ref=external_user_ref,
        user_id=ctx.membership.user_id,
        conversation_id=ctx.assistant.id,  # never used: there is always a user
    )


@router.get("/assistants/{assistant_id}/memories", response_model=list[MemoryFileOut])
async def list_memories(
    ctx: AssistantCtx, session: SessionDep, external_user_ref: ExternalRef = None
) -> list[MemoryFileOut]:
    rows = await session.scalars(
        select(MemoryFile)
        .where(
            MemoryFile.assistant_id == ctx.assistant.id,
            MemoryFile.owner_key == _owner(ctx, external_user_ref),
        )
        .order_by(MemoryFile.path)
    )
    return [MemoryFileOut.model_validate(r) for r in rows.all()]


@router.delete("/assistants/{assistant_id}/memories", response_model=MemoryCleared)
async def clear_memories(
    ctx: AssistantCtx,
    session: SessionDep,
    external_user_ref: ExternalRef = None,
    path: Annotated[str | None, Query(max_length=255)] = None,
) -> MemoryCleared:
    """Forget everything, or one file (`path`)."""
    stmt = delete(MemoryFile).where(
        MemoryFile.assistant_id == ctx.assistant.id,
        MemoryFile.owner_key == _owner(ctx, external_user_ref),
    )
    if path is not None:
        try:
            stmt = stmt.where(MemoryFile.path == normalize_path(path))
        except ValueError as exc:
            raise BadRequest(str(exc), code="invalid_memory_path") from None
    result = await session.execute(stmt)
    await session.commit()
    return MemoryCleared(deleted=int(result.rowcount or 0))  # type: ignore[attr-defined]


__all__ = ["router"]
