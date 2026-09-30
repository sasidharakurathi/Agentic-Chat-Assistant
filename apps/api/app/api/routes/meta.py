from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter

from app.agent import claude_api
from app.agent.models import ALLOWED_MODELS, DEFAULT_MODEL_BY_ROLE
from app.config import settings
from app.graph.nodes import NodeType
from app.graph.validate import ALLOWED_EDGES, SINGLETON_TYPES
from app.schemas.assistant_config import config_json_schema, default_config

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/config-schema")
async def get_config_schema() -> dict[str, Any]:
    """JSON Schema for ``AssistantConfig`` + a fresh default instance, so the
    frontend can render / seed the config panels."""
    return {
        "schema": config_json_schema(),
        "default": default_config().model_dump(mode="json"),
        "allowed_models": sorted(ALLOWED_MODELS),
        "default_model_by_role": DEFAULT_MODEL_BY_ROLE,
        # Offline mode (RAG_OFFLINE=1) switches web search off instance-wide,
        # whatever an assistant's config says; the panels say so.
        "offline": settings.rag_offline,
        # Whether the AI helpers (e.g. writing a system prompt) call the real
        # model and bill for it, or use their free stand-ins (task 5.5).
        "real_model": claude_api.real_model_allowed(),
    }


@router.get("/graph-schema")
async def get_graph_schema() -> dict[str, Any]:
    """The canvas's wiring rules, served from the same constants the validator
    uses.

    The alternative was a copy of the edge allow-list in TypeScript, which
    would disagree with the backend the first time either side changed — and
    the failure mode is silent (the canvas lets you draw an edge the API then
    rejects, or refuses one it would have accepted). One source of truth
    instead.
    """
    return {
        "node_types": sorted(get_args(NodeType)),
        "allowed_edges": sorted([source, target] for source, target in ALLOWED_EDGES),
        "singleton_types": sorted(SINGLETON_TYPES),
    }
