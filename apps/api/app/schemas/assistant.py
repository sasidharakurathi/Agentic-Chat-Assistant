from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.graph.nodes import Graph
from app.graph.validate import ValidationResult
from app.models.assistant import AssistantStatus
from app.schemas.assistant_config import AssistantConfig
from app.schemas.common import ORMModel
from app.services.diff import DiffEntry, GraphDiff


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class AssistantMetaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: AssistantStatus | None = None


class AssistantSummary(ORMModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    description: str
    status: AssistantStatus
    created_by: uuid.UUID | None
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AssistantDetail(AssistantSummary):
    draft_graph: Graph
    draft_config: AssistantConfig
    draft_validation: ValidationResult


class DraftGraphSaveResult(BaseModel):
    graph: Graph
    validation: ValidationResult
    # The recompiled config — present only when the graph validates.
    config: AssistantConfig | None = None


class DraftConfigSaveResult(BaseModel):
    config: AssistantConfig
    graph: Graph


class GraphCompileResult(BaseModel):
    config: AssistantConfig


class PublishRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class VersionSummary(ORMModel):
    id: uuid.UUID
    version_number: int
    note: str
    created_by: uuid.UUID | None
    created_at: datetime


class VersionDetail(VersionSummary):
    graph: Graph
    config: AssistantConfig


class VersionDiff(BaseModel):
    from_version: int
    to_version: int
    config_diff: list[DiffEntry]
    graph_diff: GraphDiff


__all__ = [
    "AssistantCreate",
    "AssistantDetail",
    "AssistantMetaUpdate",
    "AssistantSummary",
    "DraftConfigSaveResult",
    "DraftGraphSaveResult",
    "GraphCompileResult",
    "PublishRequest",
    "VersionDetail",
    "VersionDiff",
    "VersionSummary",
]
