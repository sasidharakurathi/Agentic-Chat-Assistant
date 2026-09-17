from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.agent.models import ALLOWED_MODELS, DEFAULT_MODEL_BY_ROLE
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
    }
