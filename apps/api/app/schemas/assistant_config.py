"""``AssistantConfig`` — the single executable contract the agent runtime consumes.

The visual graph (``app.graph``) and the form panels are two authoring surfaces
that both produce an instance of this model. It is deliberately strict
(``extra="forbid"``) so config drift surfaces as a validation error.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.models import ALLOWED_MODELS, DEFAULT_MODEL_BY_ROLE

SCHEMA_VERSION = 1

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
ApprovalMode = Literal["auto", "require", "deny"]
BuiltinToolKey = Literal["web_search", "http_request", "calculator", "datetime"]
SubagentRole = Literal["retrieval", "sql", "research"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# ── Models ───────────────────────────────────────────────────


class ThinkingConfig(_Strict):
    type: Literal["adaptive", "disabled"] = "adaptive"


class ModelSpec(_Strict):
    model: str
    effort: EffortLevel = "high"
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    max_turns: int | None = Field(default=None, ge=1, le=200)
    max_budget_usd: float | None = Field(default=None, gt=0)

    @field_validator("model")
    @classmethod
    def _known_model(cls, v: str) -> str:
        if v not in ALLOWED_MODELS:
            raise ValueError(f"unknown model {v!r}; allowed: {sorted(ALLOWED_MODELS)}")
        return v


def _default_model(role: str) -> ModelSpec:
    return ModelSpec(model=DEFAULT_MODEL_BY_ROLE[role])


class ModelRoles(_Strict):
    router: ModelSpec = Field(default_factory=lambda: _default_model("router"))
    main: ModelSpec = Field(default_factory=lambda: _default_model("main"))
    subagent: ModelSpec = Field(default_factory=lambda: _default_model("subagent"))
    judge: ModelSpec = Field(default_factory=lambda: _default_model("judge"))


# ── Guardrails ───────────────────────────────────────────────


class Guardrails(_Strict):
    rules: list[str] = Field(default_factory=list)
    pii_redaction: bool = True
    injection_scan: bool = True
    refusal_fallback: bool = True
    untrusted_content_notice: bool = True


# ── Who may use a capability (task 5.10) ─────────────────────


class Scoped(_Strict):
    """Who may use a capability, from how it is wired on the canvas: to
    the main agent (`agent`), and/or to particular subagents (`subagents`).
    A capability wired only to the sql subagent is that subagent's alone:
    the main agent can't call it. A subagent can also use what is wired to
    the main agent, as far as its role's own tools go."""

    agent: bool = True
    subagents: list[SubagentRole] = Field(default_factory=list)

    @field_validator("subagents")
    @classmethod
    def _sorted_roles(cls, v: list[SubagentRole]) -> list[SubagentRole]:
        return sorted(set(v))

    def usable_by(self, caller: str | None) -> bool:
        """`caller`: None for the main agent, else a subagent's role."""
        return self.agent or (caller is not None and caller in self.subagents)


# ── RAG ──────────────────────────────────────────────────────


class RagChunking(_Strict):
    strategy: Literal["recursive"] = "recursive"
    max_tokens: int = Field(default=800, ge=128, le=4000)
    overlap: float = Field(default=0.15, ge=0.0, le=0.5)


class RagRetrieval(_Strict):
    hybrid: bool = True
    top_k_dense: int = Field(default=40, ge=1, le=500)
    top_k_sparse: int = Field(default=40, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    rerank_top_n: int = Field(default=8, ge=1, le=100)
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)
    max_queries: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _rerank_within_candidates(self) -> RagRetrieval:
        ceiling = min(self.top_k_dense, self.top_k_sparse if self.hybrid else self.top_k_dense)
        if self.rerank_top_n > ceiling:
            raise ValueError(
                f"rerank_top_n ({self.rerank_top_n}) exceeds the candidate pool ({ceiling})"
            )
        return self


class RagConfig(Scoped):
    enabled: bool = False
    embedder: str = "voyage-3-large"
    reranker: str = "voyage-rerank-2.5"
    chunking: RagChunking = Field(default_factory=RagChunking)
    contextual_retrieval: bool = True
    retrieval: RagRetrieval = Field(default_factory=RagRetrieval)
    citations: bool = True
    # Empty = search every ready data source on the assistant. Stored sorted.
    source_ids: list[str] = Field(default_factory=list)

    @field_validator("source_ids")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


# ── Databases / tools / MCP ──────────────────────────────────


class DatabaseRef(Scoped):
    connection_id: str
    nl2sql: bool = True
    expose_write: bool = False


class WebSearchTool(Scoped):
    enabled: bool = False
    max_uses: int = Field(default=5, ge=1, le=50)
    allowed_domains: list[str] = Field(default_factory=list)


class HttpRequestTool(Scoped):
    enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    approval: ApprovalMode = "require"


class ToggleTool(Scoped):
    # A tool is "on" iff enabled here — which the graph represents as node presence.
    enabled: bool = False


_TOOL_NAME_RE = r"^[A-Za-z0-9_-]{1,64}$"


class McpServerRef(Scoped):
    """One registered MCP server, as this assistant version uses it (tasks 4.6 / 4.7).

    `tools` is the allowlist: only these, and only if the server still lists
    them, are offered to the model. Empty means none; nothing from a server
    is usable until someone chooses it.

    Approval, most specific first: `tool_approvals[tool]`, then `approval`
    for the server, then the assistant's `approval_policy.mcp_default`
    (see `approvals.mcp_mode`). `None` means "not set here".
    """

    id: Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{8,64}$")]
    tools: list[Annotated[str, Field(pattern=_TOOL_NAME_RE)]] = Field(default_factory=list)
    approval: ApprovalMode | None = None
    tool_approvals: dict[Annotated[str, Field(pattern=_TOOL_NAME_RE)], ApprovalMode] = Field(
        default_factory=dict
    )

    @field_validator("tools")
    @classmethod
    def _sorted_unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))

    @field_validator("tool_approvals")
    @classmethod
    def _sorted(cls, v: dict[str, ApprovalMode]) -> dict[str, ApprovalMode]:
        return dict(sorted(v.items()))


class ToolsConfig(_Strict):
    web_search: WebSearchTool = Field(default_factory=WebSearchTool)
    http_request: HttpRequestTool = Field(default_factory=HttpRequestTool)
    calculator: ToggleTool = Field(default_factory=ToggleTool)
    datetime: ToggleTool = Field(default_factory=ToggleTool)


# ── Subagents / memory / approvals ───────────────────────────


class SubagentsConfig(_Strict):
    # Off by default: a subagent is only useful once a capability is attached
    # (a KB for retrieval, a DB for sql). The wizard turns these on in context.
    retrieval: bool = False
    sql: bool = False
    research: bool = False
    #: Per-role model settings (task 5.1). A role without one uses
    #: `models.subagent`, the shared subagent role.
    models: dict[SubagentRole, ModelSpec] = Field(default_factory=dict)


class MemoryConfig(_Strict):
    persist_history: bool = True
    summarize_after_tokens: int = Field(default=120_000, ge=8_000, le=900_000)
    memory_tool: bool = False
    auto_title: bool = True


class RouterConfig(_Strict):
    """Route each message's effort before its turn (task 5.10): the
    router's model (`models.router`) sorts it as simple, normal or hard, and
    the turn runs at low effort, the agent's own, or high. On when a router
    node is wired in."""

    enabled: bool = False


class ApprovalPolicy(_Strict):
    db_write: ApprovalMode = "require"
    db_ddl: ApprovalMode = "require"
    http_non_get: ApprovalMode = "require"
    mcp_default: ApprovalMode = "require"
    file_write: Literal["deny"] = "deny"
    shell: Literal["deny"] = "deny"


# ── Top level ────────────────────────────────────────────────

_UUID_RE = r"^[0-9a-fA-F-]{8,64}$"


class AssistantConfig(_Strict):
    schema_version: Literal[1] = 1
    models: ModelRoles = Field(default_factory=ModelRoles)
    system_prompt: str = Field(default="You are a helpful assistant.", max_length=100_000)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    rag: RagConfig = Field(default_factory=RagConfig)
    databases: list[DatabaseRef] = Field(default_factory=list)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp_servers: list[McpServerRef] = Field(default_factory=list)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    router: RouterConfig = Field(default_factory=RouterConfig)

    @model_validator(mode="after")
    def _normalize_refs(self) -> AssistantConfig:
        conn_ids = [d.connection_id for d in self.databases]
        if len(conn_ids) != len(set(conn_ids)):
            raise ValueError("duplicate database connection_id in databases[]")
        mcp_ids = [m.id for m in self.mcp_servers]
        if len(mcp_ids) != len(set(mcp_ids)):
            raise ValueError("duplicate id in mcp_servers[]")
        # Order of these reference lists is not semantic — canonicalize it so two
        # configs authored in different orders compare equal (and round-trip).
        self.databases.sort(key=lambda d: d.connection_id)
        self.mcp_servers.sort(key=lambda m: m.id)
        self._canonical_scopes()
        return self

    def scoped(self) -> list[Scoped]:
        """Every capability that says who may use it."""
        t = self.tools
        return [
            self.rag,
            *self.databases,
            t.web_search,
            t.http_request,
            t.calculator,
            t.datetime,
            *self.mcp_servers,
        ]

    def _canonical_scopes(self) -> None:
        """One way to say each scope, so a config and its graph round-trip:
        roles whose subagent is off are dropped, and a capability left with
        nobody goes back to the main agent (turning a subagent off hands its
        capabilities back, rather than making them vanish). A capability
        that is off has no scope."""
        on = {r for r in ("retrieval", "sql", "research") if getattr(self.subagents, r)}
        switched = [
            self.rag,
            self.tools.web_search,
            self.tools.http_request,
            self.tools.calculator,
            self.tools.datetime,
        ]
        for cap in self.scoped():
            if any(cap is x for x in switched) and not getattr(cap, "enabled", True):
                cap.agent, cap.subagents = True, []
                continue
            cap.subagents = [r for r in cap.subagents if r in on]
            if not cap.subagents:
                cap.agent = True

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def _ids_become_refs(cls, v: object) -> object:
        """Configs saved before task 4.6 held bare ids. They load as servers
        with no tools allowed, which is what they meant: nothing ran."""
        if isinstance(v, list):
            return [{"id": item} if isinstance(item, str) else item for item in v]
        return v


def default_config() -> AssistantConfig:
    return AssistantConfig()


def config_json_schema() -> dict:
    return AssistantConfig.model_json_schema()


__all__ = [
    "SCHEMA_VERSION",
    "ApprovalMode",
    "ApprovalPolicy",
    "AssistantConfig",
    "BuiltinToolKey",
    "DatabaseRef",
    "EffortLevel",
    "Guardrails",
    "HttpRequestTool",
    "MemoryConfig",
    "ModelRoles",
    "ModelSpec",
    "RagChunking",
    "RagConfig",
    "RagRetrieval",
    "RouterConfig",
    "Scoped",
    "SubagentRole",
    "SubagentsConfig",
    "ThinkingConfig",
    "ToggleTool",
    "ToolsConfig",
    "WebSearchTool",
    "config_json_schema",
    "default_config",
]
