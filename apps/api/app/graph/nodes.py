"""The graph authoring model.

Typed nodes + edges with positions. ``compile_graph`` turns a valid graph into an
``AssistantConfig``; ``project_config`` does the reverse. Edges are *declarative
wiring* ("this capability is available to that agent"), not execution order.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assistant_config import (
    ApprovalMode,
    ApprovalPolicy,
    BuiltinToolKey,
    Guardrails,
    MemoryConfig,
    ModelRoles,
    ModelSpec,
    RagChunking,
    RagRetrieval,
    SubagentRole,
)

NodeType = Literal[
    "input",
    "guardrail",
    "router",
    "knowledge_base",
    "data_source",
    "database",
    "tool",
    "mcp_server",
    "subagent",
    "agent",
    "memory",
    "output",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Position(_Strict):
    x: float = 0.0
    y: float = 0.0


# ── Per-node data payloads ───────────────────────────────────


class InputNodeData(_Strict):
    pass


class OutputNodeData(_Strict):
    citations: bool = True


class GuardrailNodeData(Guardrails):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class RouterNodeData(_Strict):
    model: ModelSpec


class KnowledgeBaseNodeData(_Strict):
    embedder: str = "voyage-3-large"
    reranker: str = "voyage-rerank-2.5"
    chunking: RagChunking = Field(default_factory=RagChunking)
    contextual_retrieval: bool = True
    retrieval: RagRetrieval = Field(default_factory=RagRetrieval)
    citations: bool = True


class DataSourceNodeData(_Strict):
    data_source_id: str


class DatabaseNodeData(_Strict):
    connection_id: str
    nl2sql: bool = True
    expose_write: bool = False


class ToolNodeData(_Strict):
    key: BuiltinToolKey
    config: dict[str, object] = Field(default_factory=dict)
    approval: ApprovalMode = "require"


class McpServerNodeData(_Strict):
    mcp_server_id: str
    tool_allowlist: list[str] = Field(default_factory=list)
    #: The server's rule for this version; None means the assistant default.
    approval: ApprovalMode | None = None
    #: Per-tool rules, over the server's (task 4.7).
    tool_approvals: dict[str, ApprovalMode] = Field(default_factory=dict)


class SubagentNodeData(_Strict):
    """`model` and `max_turns` are this role's own model settings
    (`subagents.models[role]`, task 5.1); both None keeps the shared subagent
    role (`models.subagent`). The limit matches `ModelSpec.max_turns`, so any
    config projects onto a valid node."""

    role: SubagentRole
    model: ModelSpec | None = None
    max_turns: int | None = Field(default=None, ge=1, le=200)


class AgentNodeData(_Strict):
    system_prompt: str = Field(default="You are a helpful assistant.", max_length=100_000)
    models: ModelRoles = Field(default_factory=ModelRoles)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)


class MemoryNodeData(MemoryConfig):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# ── Node wrappers (discriminated on ``type``) ────────────────


class _NodeBase(_Strict):
    id: str = Field(min_length=1, max_length=128)
    position: Position = Field(default_factory=Position)


class InputNode(_NodeBase):
    type: Literal["input"] = "input"
    data: InputNodeData = Field(default_factory=InputNodeData)


class OutputNode(_NodeBase):
    type: Literal["output"] = "output"
    data: OutputNodeData = Field(default_factory=OutputNodeData)


class GuardrailNode(_NodeBase):
    type: Literal["guardrail"] = "guardrail"
    data: GuardrailNodeData = Field(default_factory=GuardrailNodeData)


class RouterNode(_NodeBase):
    type: Literal["router"] = "router"
    data: RouterNodeData


class KnowledgeBaseNode(_NodeBase):
    type: Literal["knowledge_base"] = "knowledge_base"
    data: KnowledgeBaseNodeData = Field(default_factory=KnowledgeBaseNodeData)


class DataSourceNode(_NodeBase):
    type: Literal["data_source"] = "data_source"
    data: DataSourceNodeData


class DatabaseNode(_NodeBase):
    type: Literal["database"] = "database"
    data: DatabaseNodeData


class ToolNode(_NodeBase):
    type: Literal["tool"] = "tool"
    data: ToolNodeData


class McpServerNode(_NodeBase):
    type: Literal["mcp_server"] = "mcp_server"
    data: McpServerNodeData


class SubagentNode(_NodeBase):
    type: Literal["subagent"] = "subagent"
    data: SubagentNodeData


class AgentNode(_NodeBase):
    type: Literal["agent"] = "agent"
    data: AgentNodeData = Field(default_factory=AgentNodeData)


class MemoryNode(_NodeBase):
    type: Literal["memory"] = "memory"
    data: MemoryNodeData = Field(default_factory=MemoryNodeData)


AnyNode = Annotated[
    (
        InputNode
        | OutputNode
        | GuardrailNode
        | RouterNode
        | KnowledgeBaseNode
        | DataSourceNode
        | DatabaseNode
        | ToolNode
        | McpServerNode
        | SubagentNode
        | AgentNode
        | MemoryNode
    ),
    Field(discriminator="type"),
]


class Edge(_Strict):
    id: str | None = None
    source: str
    target: str


class Graph(_Strict):
    schema_version: Literal[1] = 1
    nodes: list[AnyNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def by_id(self) -> dict[str, AnyNode]:
        return {n.id: n for n in self.nodes}

    def nodes_of(self, node_type: NodeType) -> list[AnyNode]:
        return [n for n in self.nodes if n.type == node_type]

    def incomers(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def outgoers(self, node_id: str) -> list[str]:
        return [e.target for e in self.edges if e.source == node_id]


__all__ = [
    "AgentNode",
    "AgentNodeData",
    "AnyNode",
    "DataSourceNode",
    "DatabaseNode",
    "DatabaseNodeData",
    "Edge",
    "Graph",
    "GuardrailNode",
    "InputNode",
    "KnowledgeBaseNode",
    "McpServerNode",
    "MemoryNode",
    "NodeType",
    "OutputNode",
    "Position",
    "RouterNode",
    "SubagentNode",
    "SubagentNodeData",
    "ToolNode",
    "ToolNodeData",
]
