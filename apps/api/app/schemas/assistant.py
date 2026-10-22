from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.graph.nodes import Graph
from app.graph.validate import ValidationResult
from app.models.assistant import AssistantStatus, VersionStatus
from app.schemas.assistant_config import AssistantConfig
from app.schemas.common import ApiModel, ORMModel
from app.schemas.listing import VersionListing
from app.services.diff import DiffEntry, GraphDiff


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class AssistantFromSample(BaseModel):
    """Start from a shipped sample (task 6.7)."""

    sample_id: str = Field(min_length=1, max_length=60)
    #: The sample's own name when left out.
    name: str | None = Field(default=None, min_length=1, max_length=200)


class SampleSummary(ApiModel):
    """A sample assistant, as the gallery shows it."""

    id: str
    name: str
    description: str
    needs: list[str]
    try_asking: list[str]
    #: Node types on its canvas, in drawing order: the gallery draws the
    #: line from these.
    node_types: list[str]
    documents: int
    eval_cases: int


class AssistantMetaUpdate(BaseModel):
    # Unknown fields are refused, `status` above all: it used to be settable
    # here, which let a builder publish without approval or reopen an
    # assistant an admin had paused (Phase 7b.1). A client still sending it
    # gets told, rather than having it silently dropped.
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


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
    #: Marked Official by an admin (Phase 8.5).
    official_at: datetime | None = None


class AssistantDetail(AssistantSummary):
    draft_graph: Graph
    draft_config: AssistantConfig
    draft_validation: ValidationResult
    #: Whether the caller may edit it: an admin, its creator or a co-editor
    #: (Phase 7b.4). The web app shows the builder's controls by this.
    can_edit: bool = False


class EditorOut(ORMModel):
    """A co-editor of an assistant (Phase 7b.4)."""

    user_id: uuid.UUID
    email: str
    name: str
    granted_by: uuid.UUID | None
    created_at: datetime


class EditorAdd(BaseModel):
    user_id: uuid.UUID


class DraftGraphSaveResult(ApiModel):
    graph: Graph
    validation: ValidationResult
    # The recompiled config — present only when the graph validates.
    config: AssistantConfig | None = None


class DraftConfigSaveResult(ApiModel):
    config: AssistantConfig
    graph: Graph
    #: Returned like the graph save's, so a Panels edit refreshes the error and
    #: warning badges instead of leaving the last canvas save's result showing.
    validation: ValidationResult


class GraphCompileResult(ApiModel):
    config: AssistantConfig


class PublishRequest(BaseModel):
    """Submitting the draft for review: what changed, for the reviewer."""

    note: str = Field(default="", max_length=1000)


class ApproveRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class RejectRequest(BaseModel):
    #: Required: a builder told only "no" cannot fix anything.
    note: str = Field(min_length=1, max_length=1000)


class ArchiveRequest(BaseModel):
    #: Why it was paused, for the audit log and the builder.
    reason: str = Field(min_length=1, max_length=500)


class VersionSummary(ORMModel):
    id: uuid.UUID
    version_number: int
    note: str
    #: Who submitted it, and when.
    created_by: uuid.UUID | None
    created_at: datetime
    status: VersionStatus
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    review_note: str = ""
    #: Identifies exactly what it was submitted with (Phase 7b.1); each run
    #: records the one it answered with.
    manifest_hash: str | None = None


class VersionDetail(VersionSummary):
    graph: Graph
    config: AssistantConfig
    #: Its knowledge (document by document) and a fingerprint of each
    #: connection, MCP server and tool, as they were when it was submitted.
    manifest: dict[str, Any] | None = None
    #: How Chat shows it and who it is for, as submitted (Phase 8.5).
    listing: VersionListing | None = None


class DriftItem(ApiModel):
    """One thing a version uses that has changed since it was submitted."""

    kind: Literal["database", "mcp_server", "mcp_tool", "http_connection", "document"]
    id: str
    name: str
    what: Literal["changed", "missing"]
    tool: str | None = None
    message: str


class VersionDrift(ApiModel):
    """What has changed since a version was submitted. On the live version,
    each of these is left out of its turns until a new version is approved;
    a version with any cannot be approved or rolled back to."""

    version: int
    manifest_hash: str | None
    drift: list[DriftItem]


class VersionDiff(ApiModel):
    from_version: int
    to_version: int
    config_diff: list[DiffEntry]
    graph_diff: GraphDiff


__all__ = [
    "ApproveRequest",
    "ArchiveRequest",
    "AssistantCreate",
    "AssistantDetail",
    "AssistantMetaUpdate",
    "AssistantSummary",
    "DraftConfigSaveResult",
    "DraftGraphSaveResult",
    "DriftItem",
    "EditorAdd",
    "EditorOut",
    "GraphCompileResult",
    "PublishRequest",
    "RejectRequest",
    "VersionDetail",
    "VersionDiff",
    "VersionDrift",
    "VersionSummary",
]
