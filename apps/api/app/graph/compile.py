"""``compile_graph(graph) -> AssistantConfig``.

Pure, deterministic, total on valid graphs, no I/O. It walks the edges into the
single ``agent`` node and projects the wired nodes onto the config. An invalid
graph raises :class:`GraphCompileError` (call :func:`~app.graph.validate.validate_graph`
first if you want the warnings too).
"""

from __future__ import annotations

from app.graph.nodes import DataSourceNode, Graph, OutputNode
from app.graph.validate import validate_graph
from app.schemas.assistant_config import (
    AssistantConfig,
    DatabaseRef,
    Guardrails,
    HttpRequestTool,
    McpServerRef,
    MemoryConfig,
    ModelRoles,
    RagConfig,
    SubagentsConfig,
    ToggleTool,
    ToolsConfig,
    WebSearchTool,
)


class GraphCompileError(ValueError):
    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("; ".join(messages))


def _adjacency(graph: Graph) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for e in graph.edges:
        if e.source in adj:
            adj[e.source].append(e.target)
    return adj


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value]
    return []


def _reaches(start: str, target: str, adj: dict[str, list[str]]) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        if u == target:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, []))
    return False


def _subagents(
    graph: Graph, agent_id: str, models: ModelRoles
) -> tuple[SubagentsConfig, ModelRoles]:
    """Which subagent roles are on, and the subagent model role with a node's
    `model` / `max_turns` overrides applied (they used to be dropped)."""
    sub = SubagentsConfig(retrieval=False, sql=False, research=False)
    nodes = [n for n in graph.nodes if n.type == "subagent" and agent_id in graph.outgoers(n.id)]
    for n in nodes:
        setattr(sub, n.data.role, True)
    # One subagent model role serves every subagent, so one node's overrides
    # apply: the retrieval node's (the one the runtime builds), else the first.
    lead = next((n for n in nodes if n.data.role == "retrieval"), None) or next(iter(nodes), None)
    if lead is None or (lead.data.model is None and lead.data.max_turns is None):
        return sub, models
    spec = (lead.data.model or models.subagent).model_copy(deep=True)
    if lead.data.max_turns is not None:
        spec = spec.model_copy(update={"max_turns": lead.data.max_turns})
    return sub, models.model_copy(update={"subagent": spec})


def compile_graph(graph: Graph) -> AssistantConfig:
    result = validate_graph(graph)
    if not result.ok:
        raise GraphCompileError([e.message for e in result.errors])

    by_id = graph.by_id()
    adj = _adjacency(graph)
    agent_node = next(n for n in graph.nodes if n.type == "agent")
    agent_id = agent_node.id

    def wired_to_agent(node_id: str) -> bool:
        return _reaches(node_id, agent_id, adj)

    # ── models (agent node, router node overrides) ───────────
    models = agent_node.data.models.model_copy(deep=True)
    # Wired to the agent like every other capability. An unwired router used
    # to rewrite models.router anyway, with no orphan warning to say so.
    router_nodes = [n for n in graph.nodes if n.type == "router" and wired_to_agent(n.id)]
    if router_nodes:
        models = ModelRoles(
            router=router_nodes[0].data.model.model_copy(deep=True),
            main=models.main,
            subagent=models.subagent,
            judge=models.judge,
        )

    # ── guardrails / memory ──────────────────────────────────
    guard_nodes = [n for n in graph.nodes if n.type == "guardrail" and wired_to_agent(n.id)]
    guardrails = (
        Guardrails.model_validate(guard_nodes[0].data.model_dump()) if guard_nodes else Guardrails()
    )
    mem_nodes = [n for n in graph.nodes if n.type == "memory" and wired_to_agent(n.id)]
    memory = (
        MemoryConfig.model_validate(mem_nodes[0].data.model_dump()) if mem_nodes else MemoryConfig()
    )

    # ── rag (knowledge_base node + feeding data_source nodes) ─
    # The output node's `citations` is the answer-side switch: numbered
    # citations are shown only if both it and the knowledge base allow them.
    # It was written by the projection and read by nothing.
    output_citations = next(
        (n.data.citations for n in graph.nodes if isinstance(n, OutputNode)), True
    )
    kb_nodes = [n for n in graph.nodes if n.type == "knowledge_base" and wired_to_agent(n.id)]
    if kb_nodes:
        kb = kb_nodes[0]
        source_ids = sorted(
            node.data.data_source_id
            for s in graph.incomers(kb.id)
            if isinstance(node := by_id[s], DataSourceNode)
        )
        rag = RagConfig(
            enabled=True,
            embedder=kb.data.embedder,
            reranker=kb.data.reranker,
            chunking=kb.data.chunking.model_copy(deep=True),
            contextual_retrieval=kb.data.contextual_retrieval,
            retrieval=kb.data.retrieval.model_copy(deep=True),
            citations=kb.data.citations and output_citations,
            source_ids=source_ids,
        )
    else:
        rag = RagConfig(citations=output_citations)

    # ── databases ────────────────────────────────────────────
    databases = sorted(
        (
            DatabaseRef(
                connection_id=n.data.connection_id,
                nl2sql=n.data.nl2sql,
                expose_write=n.data.expose_write,
            )
            for n in graph.nodes
            if n.type == "database" and wired_to_agent(n.id)
        ),
        key=lambda d: d.connection_id,
    )

    # ── tools ────────────────────────────────────────────────
    tools = ToolsConfig()
    for n in graph.nodes:
        if n.type != "tool" or not wired_to_agent(n.id):
            continue
        key = n.data.key
        cfg = n.data.config
        if key == "web_search":
            tools.web_search = WebSearchTool(
                enabled=True,
                max_uses=_as_int(cfg.get("max_uses"), 5),
                allowed_domains=_as_str_list(cfg.get("allowed_domains")),
            )
        elif key == "http_request":
            tools.http_request = HttpRequestTool(
                enabled=True,
                allowed_domains=_as_str_list(cfg.get("allowed_domains")),
                approval=n.data.approval,
            )
        elif key == "calculator":
            tools.calculator = ToggleTool(enabled=True)
        elif key == "datetime":
            tools.datetime = ToggleTool(enabled=True)

    # ── mcp servers ──────────────────────────────────────────
    # The node's allowlist and approval were dropped here before task 4.6:
    # the config carried only which servers, never which of their tools.
    mcp_servers = sorted(
        (
            McpServerRef(
                id=n.data.mcp_server_id,
                tools=list(n.data.tool_allowlist),
                approval=n.data.approval,
                tool_approvals=dict(n.data.tool_approvals),
            )
            for n in graph.nodes
            if n.type == "mcp_server" and wired_to_agent(n.id)
        ),
        key=lambda m: m.id,
    )

    # ── subagents ────────────────────────────────────────────
    sub, models = _subagents(graph, agent_id, models)

    return AssistantConfig(
        models=models,
        system_prompt=agent_node.data.system_prompt,
        guardrails=guardrails,
        rag=rag,
        databases=databases,
        tools=tools,
        mcp_servers=mcp_servers,
        subagents=sub,
        memory=memory,
        approval_policy=agent_node.data.approval_policy.model_copy(deep=True),
    )


__all__ = ["GraphCompileError", "compile_graph"]
