"""AI assist for builders (Phase 5): suggestions, never saves.

- `POST /assistants/{id}/prompt:generate` (task 5.5) writes a draft system
  prompt and rules from a description of what the assistant is for.
- `POST /assistants/{id}/pipeline:recommend` (task 5.6) recommends a whole
  starter pipeline for it, from what the assistant already has;
  `POST /pipeline:recommend` does the same before the assistant exists
  (the guided setup).

The builder reviews each and applies it through the normal draft save.
Editors only on an existing assistant: they can spend money.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Literal

import anthropic
from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import claude_api
from app.api.deps import ActiveMembership, EditableAssistantCtx, SessionDep, per_user_limit
from app.api.errors import AppError
from app.assist import pipeline as pipeline_assist
from app.assist import prompt as prompt_assist
from app.assist.structured import AssistFailed, Spend
from app.config import settings
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.graph.validate import ValidationResult, validate_graph
from app.logging import get_logger
from app.models.integration import DbConnection, McpServer
from app.models.rag import DataSource
from app.models.usage import UsageEvent, UsageKind
from app.schemas.assist import (
    PipelineCapability,
    PipelineRecommendIn,
    PipelineSuggestion,
    PromptGenerateIn,
    PromptSuggestion,
)
from app.schemas.assistant_config import AssistantConfig, default_config
from app.services import assistants as assistants_svc
from app.services import budgets

router = APIRouter(tags=["assist"])
log = get_logger(__name__)

#: The helpers call a model: limited per caller (task 5.8).
ASSIST = per_user_limit("assist", "rate_limit_assist")

_WHAT = {"prompt": "write this prompt", "pipeline": "recommend a pipeline"}


class _ModelTrouble(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "model_unavailable"


def _failure(exc: Exception, what: Literal["prompt", "pipeline"]) -> AppError:
    """What the builder is told when the model couldn't help: the SDK's
    typed errors, most specific first; never the raw text."""
    if isinstance(exc, AssistFailed) and exc.reason == "refused":
        return AppError(
            f"The model declined to {_WHAT[what]}. Rephrase the description and try again.",
            code=f"{what}_refused",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if isinstance(exc, AssistFailed):
        return AppError(
            "The model's answer was cut off or malformed. Try again.",
            code=f"{what}_unreadable",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return AppError(
            "The platform's model credentials were rejected. An administrator needs to check them.",
            code="model_auth_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    if isinstance(exc, anthropic.RateLimitError):
        return _ModelTrouble("The model is receiving too many requests. Try again in a minute.")
    # By status, not class: the SDK has several for 5xx (529 is its own
    # `OverloadedError`, not an `InternalServerError`).
    if isinstance(exc, anthropic.APIConnectionError) or (
        isinstance(exc, anthropic.APIStatusError)
        and exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
    ):
        return _ModelTrouble("The model is unavailable right now. Try again in a minute.")
    return AppError(
        f"The model couldn't {_WHAT[what]}.",
        code="model_error",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


async def _record(
    session: AsyncSession, org_id: uuid.UUID, assistant_id: uuid.UUID | None, spend: Spend
) -> None:
    """A model call: on the ledger like any other spend (task 1.8)."""
    session.add(
        UsageEvent(
            org_id=org_id,
            assistant_id=assistant_id,
            kind=UsageKind.llm,
            model=spend.model,
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            cost_usd=spend.cost_usd,
        )
    )
    await session.commit()


async def _within_budget(
    session: AsyncSession, org_id: uuid.UUID, assistant_id: uuid.UUID | None
) -> None:
    """On the real model the helpers spend, so not once a budget is used up
    (task 5.7). The free templates always run."""
    if not claude_api.real_model_allowed():
        return
    over = (await budgets.gate(session, org_id, assistant_id)).exceeded
    if over is not None:
        raise AppError(
            over.exceeded_message(),
            code="budget_exceeded",
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
        )


# ── the system prompt (5.5) ──────────────────────────────────


def _ids(values: Iterable[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for value in values:
        try:
            out.append(uuid.UUID(value))
        except ValueError:
            continue
    return out


async def _wiring(
    session: AsyncSession, assistant_id: uuid.UUID, config: AssistantConfig
) -> prompt_assist.Wiring:
    """The names of the databases and MCP servers the draft actually uses
    (not every one registered on the assistant), for the prompt."""
    db_ids = _ids(d.connection_id for d in config.databases)
    mcp_ids = _ids(s.id for s in config.mcp_servers)
    databases = await session.execute(
        select(DbConnection.name, DbConnection.engine)
        .where(DbConnection.assistant_id == assistant_id, DbConnection.id.in_(db_ids))
        .order_by(DbConnection.name)
    )
    servers = await session.scalars(
        select(McpServer.name)
        .where(McpServer.assistant_id == assistant_id, McpServer.id.in_(mcp_ids))
        .order_by(McpServer.name)
    )
    return prompt_assist.Wiring(
        databases=[f"{name} ({engine.value})" for name, engine in databases.all()],
        mcp_servers=list(servers.all()),
    )


@router.post(
    "/assistants/{assistant_id}/prompt:generate",
    response_model=PromptSuggestion,
    dependencies=[ASSIST],
)
async def generate_prompt(
    body: PromptGenerateIn, ctx: EditableAssistantCtx, session: SessionDep
) -> PromptSuggestion:
    assistant = ctx.assistant
    config = AssistantConfig.model_validate(assistant.draft_config or {})
    wiring = await _wiring(session, assistant.id, config)
    await _within_budget(session, assistant.org_id, assistant.id)
    try:
        draft = await prompt_assist.generate(
            config,
            name=assistant.name,
            description=body.description,
            current=body.current_prompt,
            wiring=wiring,
        )
    except AssistFailed as exc:
        log.warning("prompt_generate_failed", reason=exc.reason)
        await _record(session, assistant.org_id, assistant.id, exc.spend)  # billed all the same
        raise _failure(exc, "prompt") from None
    except anthropic.APIError as exc:
        log.warning("prompt_generate_failed", error=type(exc).__name__)
        raise _failure(exc, "prompt") from None
    if draft.spend is not None:
        await _record(session, assistant.org_id, assistant.id, draft.spend)
    return PromptSuggestion(
        system_prompt=draft.system_prompt,
        rules=draft.rules,
        source=draft.source,
        model=draft.spend.model if draft.spend else None,
        cost_usd=draft.spend.cost_usd if draft.spend else 0.0,
    )


# ── the starter pipeline (5.6) ───────────────────────────────


async def _inventory(session: AsyncSession, assistant_id: uuid.UUID) -> pipeline_assist.Inventory:
    """Everything registered on the assistant that a pipeline could use."""
    sources = await session.scalars(
        select(DataSource.name).where(DataSource.assistant_id == assistant_id)
    )
    databases = await session.execute(
        select(DbConnection.id, DbConnection.name, DbConnection.engine)
        .where(DbConnection.assistant_id == assistant_id)
        .order_by(DbConnection.name)
    )
    servers = await session.scalars(
        select(McpServer).where(McpServer.assistant_id == assistant_id).order_by(McpServer.name)
    )
    return pipeline_assist.Inventory(
        data_sources=list(sources.all()),
        databases=[
            pipeline_assist.Named(id=str(i), name=n, detail=e.value) for i, n, e in databases.all()
        ],
        mcp_servers=[
            pipeline_assist.Named(
                id=str(s.id),
                name=s.name,
                detail=", ".join(str(t["name"]) for t in s.tools or [] if t.get("name"))[:500],
            )
            for s in servers.all()
        ],
        offline=settings.rag_offline,
    )


async def _recommend(
    session: AsyncSession,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID | None,
    base: AssistantConfig,
    *,
    name: str,
    description: str,
    inventory: pipeline_assist.Inventory,
) -> pipeline_assist.Recommendation:
    await _within_budget(session, org_id, assistant_id)
    try:
        rec = await pipeline_assist.recommend(
            base, name=name, description=description, inventory=inventory
        )
    except AssistFailed as exc:
        log.warning("pipeline_recommend_failed", reason=exc.reason)
        await _record(session, org_id, assistant_id, exc.spend)  # billed all the same
        raise _failure(exc, "pipeline") from None
    except anthropic.APIError as exc:
        log.warning("pipeline_recommend_failed", error=type(exc).__name__)
        raise _failure(exc, "pipeline") from None
    if rec.spend is not None:
        await _record(session, org_id, assistant_id, rec.spend)
    return rec


def _suggestion(
    rec: pipeline_assist.Recommendation,
    graph: Graph,
    validation: ValidationResult,
    changes: list[str],
) -> PipelineSuggestion:
    return PipelineSuggestion(
        config=rec.config,
        graph=graph,
        validation=validation,
        capabilities=[
            PipelineCapability(key=c.key, label=c.label, why=c.why) for c in rec.capabilities
        ],
        changes=changes,
        source=rec.source,
        model=rec.spend.model if rec.spend else None,
        cost_usd=rec.spend.cost_usd if rec.spend else 0.0,
    )


@router.post(
    "/assistants/{assistant_id}/pipeline:recommend",
    response_model=PipelineSuggestion,
    dependencies=[ASSIST],
)
async def recommend_pipeline(
    body: PipelineRecommendIn, ctx: EditableAssistantCtx, session: SessionDep
) -> PipelineSuggestion:
    """A starter pipeline for this assistant, from what it already has."""
    assistant = ctx.assistant
    base = AssistantConfig.model_validate(assistant.draft_config or {})
    inventory = await _inventory(session, assistant.id)
    rec = await _recommend(
        session,
        assistant.org_id,
        assistant.id,
        base,
        name=assistant.name,
        description=body.description,
        inventory=inventory,
    )
    # Laid out against the canvas as it is, so nodes already there stay put.
    graph = project_config(rec.config, existing_graph=assistants_svc.load_graph(assistant))
    validation = await assistants_svc.full_validation(session, assistant, graph)
    return _suggestion(rec, graph, validation, pipeline_assist.changes(base, rec.config, inventory))


@router.post("/pipeline:recommend", response_model=PipelineSuggestion, dependencies=[ASSIST])
async def recommend_starter(
    body: PipelineRecommendIn, m: ActiveMembership, session: SessionDep
) -> PipelineSuggestion:
    """A starter pipeline before the assistant exists (the guided setup):
    anyone who may create an assistant may ask. Nothing is registered yet,
    so it can't wire in databases or MCP servers."""
    rec = await _recommend(
        session,
        m.org_id,
        None,
        default_config(),
        name=body.name,
        description=body.description,
        inventory=pipeline_assist.Inventory(offline=settings.rag_offline),
    )
    graph = project_config(rec.config)
    return _suggestion(rec, graph, validate_graph(graph), [])


__all__ = ["router"]
