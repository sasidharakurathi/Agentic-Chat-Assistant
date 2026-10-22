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


#: How a declared action runs (Phase 9.1, plan D7): `read` at once; `confirm`
#: once the person asking confirms (only for a statement pinned to their own
#: rows); `approver` once one of the assistant's approvers decides.
ActionTier = Literal["read", "confirm", "approver"]
ActionParamType = Literal["string", "integer", "number", "boolean", "date"]

_ACTION_NAME_RE = r"^[a-z][a-z0-9_]{0,39}$"


class ActionParam(_Strict):
    """One value the model fills in. The platform's own values (who is
    asking) are never parameters: they are the `:asker_*` slots."""

    name: Annotated[str, Field(pattern=_ACTION_NAME_RE)]
    type: ActionParamType = "string"
    description: str = Field(default="", max_length=300)
    required: bool = True
    #: The longest text accepted (strings only).
    max_length: int = Field(default=500, ge=1, le=4000)
    #: When set, the only values accepted.
    choices: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=50
    )

    @field_validator("name")
    @classmethod
    def _not_a_slot(cls, v: str) -> str:
        if v.startswith("asker_"):
            raise ValueError(f"{v!r}: names starting with asker_ are filled by the platform")
        return v


class DeclaredAction(_Strict):
    """A named SQL statement on a database (Phase 9.1, plan D8): the builder
    writes it, an admin approves it with the version, and the model only
    fills its parameters. Checked when saved (`agent.actions.check_template`)."""

    name: Annotated[str, Field(pattern=_ACTION_NAME_RE)]
    #: What people read on the request card: "Request access to an app".
    title: str = Field(min_length=1, max_length=80)
    #: What the model reads: when to use it.
    description: str = Field(default="", max_length=1000)
    sql: str = Field(min_length=1, max_length=10_000)
    params: list[ActionParam] = Field(default_factory=list, max_length=20)
    tier: ActionTier = "approver"

    @model_validator(mode="after")
    def _checked(self) -> DeclaredAction:
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValueError(f"action {self.name!r}: two parameters share a name")
        from app.agent.actions import check_template

        check_template(self)
        return self


class DatabaseRef(Scoped):
    connection_id: str
    nl2sql: bool = True
    expose_write: bool = False
    #: Named statements on this database (Phase 9.1). A write here needs the
    #: connection's own write permission, not `expose_write`, which is about
    #: SQL the model writes itself.
    actions: list[DeclaredAction] = Field(default_factory=list, max_length=30)


HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class HttpAction(_Strict):
    """A named call to a company system (Phase 9.3): one of the assistant's
    HTTP connections, a method and a path with `{param}` slots. Like a SQL
    action, the builder writes it, an admin approves it with the version,
    and the model only fills its parameters. Checked when saved
    (`agent.http_actions.check_http_action`)."""

    name: Annotated[str, Field(pattern=_ACTION_NAME_RE)]
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    connection_id: Annotated[str, Field(pattern=r"^[0-9a-fA-F-]{8,64}$")]
    method: HttpMethod = "POST"
    path: str = Field(min_length=1, max_length=500)
    params: list[ActionParam] = Field(default_factory=list, max_length=20)
    tier: ActionTier = "approver"
    #: The fields of a JSON answer the model reads (`id`, `data.status`);
    #: none means all of it.
    fields: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _checked(self) -> HttpAction:
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValueError(f"action {self.name!r}: two parameters share a name")
        from app.agent.http_actions import check_http_action

        check_http_action(self)
        return self


class WebSearchTool(Scoped):
    enabled: bool = False
    max_uses: int = Field(default=5, ge=1, le=50)
    allowed_domains: list[str] = Field(default_factory=list)


class HttpRequestTool(Scoped):
    enabled: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    approval: ApprovalMode = "require"
    #: Declared calls to the assistant's HTTP connections (Phase 9.3). With
    #: no allowed sites, these are all the model can send.
    actions: list[HttpAction] = Field(default_factory=list, max_length=30)


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


class HandoffConfig(_Strict):
    """Getting a person (Phase 9.5): from the chat, or by the assistant when
    it can't help. The hand-off emails `contact_email` a link to the
    conversation in the app (never its content); with `action` set, it
    opens a ticket instead through that declared HTTP action, sending the
    conversation's `title` and `summary` (and the person's `note`) to the
    parameters of those names it declares. A `confirm` action runs only from
    the person's own click (their confirmation); the assistant's own
    hand-off emails instead. An `approver` action can't be chosen
    (`services.graph_refs`). Kept on the agent node."""

    enabled: bool = False
    #: Who is told, the IT owner say. Required once it is on.
    contact_email: str = Field(default="", max_length=254)
    #: The name of an HTTP action on the HTTP request tool (Phase 9.3) that
    #: opens the ticket; empty for email only.
    action: str = Field(default="", pattern=r"^([a-z][a-z0-9_]{0,39})?$")

    @field_validator("contact_email")
    @classmethod
    def _an_address(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        from pydantic import validate_email
        from pydantic_core import PydanticCustomError

        try:
            return validate_email(v)[1]
        except PydanticCustomError as exc:
            raise ValueError(f"{v!r} isn't an email address") from exc

    @model_validator(mode="after")
    def _someone_to_tell(self) -> HandoffConfig:
        if self.enabled and not self.contact_email:
            raise ValueError("say who is told about a hand-off: an email address")
        return self


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
    #: Getting a person (Phase 9.5).
    handoff: HandoffConfig = Field(default_factory=HandoffConfig)

    @model_validator(mode="after")
    def _normalize_refs(self) -> AssistantConfig:
        conn_ids = [d.connection_id for d in self.databases]
        if len(conn_ids) != len(set(conn_ids)):
            raise ValueError("duplicate database connection_id in databases[]")
        mcp_ids = [m.id for m in self.mcp_servers]
        if len(mcp_ids) != len(set(mcp_ids)):
            raise ValueError("duplicate id in mcp_servers[]")
        # One tool per action, so a name is the assistant's, not the database's.
        action_names = [a.name for d in self.databases for a in d.actions] + [
            a.name for a in self.tools.http_request.actions
        ]
        if len(action_names) != len(set(action_names)):
            raise ValueError("two actions share a name")
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
    "ActionParam",
    "ActionParamType",
    "ActionTier",
    "ApprovalMode",
    "ApprovalPolicy",
    "AssistantConfig",
    "BuiltinToolKey",
    "DatabaseRef",
    "DeclaredAction",
    "EffortLevel",
    "Guardrails",
    "HandoffConfig",
    "HttpAction",
    "HttpMethod",
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
