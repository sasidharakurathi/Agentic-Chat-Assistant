import type { GraphNode } from "@/lib/api";
import { modelName } from "@/lib/subagent-models";

/** Words for what a station shows on the canvas (docs/DESIGN.md sections 6
 *  and 9): plain names, plain details, correct plurals, no middle dots. Pure
 *  functions, so the canvas, the drawer and the Key all read the same. */

/** "1 tool", "2 tools". */
export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The built-in tools, by key. The same words as the tool settings'
 *  `TOOL_LABEL` (Add capability, Settings, the drawer), so a tool has one
 *  name everywhere. Kept here rather than imported, so this pure module
 *  (and its tests) does not load the settings components. */
export const TOOL_NAME: Record<string, string> = {
  calculator: "Calculator",
  datetime: "Date and time",
  web_search: "Web search",
  http_request: "HTTP requests",
};

export const SUBAGENT_ROLE_NAME: Record<string, string> = {
  retrieval: "Retrieval subagent",
  sql: "SQL subagent",
  research: "Research subagent",
};

export const ENGINE_NAME: Record<string, string> = {
  postgres: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  mongodb: "MongoDB",
};

/** A data source's ingest state in words; nothing once it is ready. The
 *  one map for every place a source's state is shown (Add capability, the
 *  data source select, the Sources tab). */
export const SOURCE_STATUS: Record<string, string> = {
  pending: "Queued",
  processing: "Indexing",
  error: "Failed",
};

const TYPE_NAME: Record<string, string> = {
  input: "Input",
  output: "Output",
  guardrail: "Guardrails",
  router: "Router",
  knowledge_base: "Knowledge base",
  data_source: "Data source",
  database: "Database",
  tool: "Tool",
  mcp_server: "MCP server",
  subagent: "Subagent",
  agent: "Agent",
  memory: "Memory",
};

const str = (v: unknown) => (v == null ? "" : String(v));

/** What a station points at (a source, a connection, a server) names it
 *  when the page could resolve it: its bullet already says the type. */
const NAMED_BY_LABEL = new Set(["data_source", "database", "mcp_server"]);

/** The name a station shows: its type, except a tool (its own name), a
 *  subagent (its role), and a source, database or MCP server whose `label`
 *  the page resolved ("refund-policy.md", "Shop PG"), which are told apart by
 *  name. */
export function stationName(node: Pick<GraphNode, "type" | "data">, label?: string): string {
  if (label && NAMED_BY_LABEL.has(node.type)) return label;
  if (node.type === "tool") return TOOL_NAME[str(node.data.key)] ?? (str(node.data.key) || "Tool");
  if (node.type === "subagent") return SUBAGENT_ROLE_NAME[str(node.data.role)] ?? "Subagent";
  return TYPE_NAME[node.type] ?? node.type;
}

const APPROVAL: Record<string, string> = {
  require: "Asks a person first",
  auto: "Runs without asking",
  deny: "Blocked",
};

/** The one-line detail under a station's name, in plain words. `label` is
 *  the name of what the node points at (a source, a connection, a server),
 *  resolved by the page from the assistant's live data. */
export function stationDetail(node: Pick<GraphNode, "type" | "data">, label?: string): string {
  const d = node.data;
  switch (node.type) {
    case "input":
      return "Messages come in here";
    case "output": {
      return d.citations === false ? "Answers without citations" : "Answers with citations";
    }
    case "guardrail": {
      const rules = Array.isArray(d.rules) ? d.rules.length : 0;
      return rules === 0 ? "Standard checks, no rules" : plural(rules, "rule", "rules");
    }
    case "router":
      return "Picks the effort for each message";
    case "agent": {
      const model = str((d.models as { main?: { model?: string } } | undefined)?.main?.model);
      return model ? modelName(model) : "No model set";
    }
    case "subagent": {
      const model = str((d.model as { model?: string } | null | undefined)?.model);
      return model ? modelName(model) : "Shared subagent model";
    }
    case "knowledge_base": {
      const n = (d.retrieval as { rerank_top_n?: number } | undefined)?.rerank_top_n;
      return typeof n === "number"
        ? plural(n, "result per search", "results per search")
        : "Results per search not set";
    }
    case "data_source":
      return label ? "Data source" : "Source removed. Pick another.";
    case "database":
      return label ? "Database" : "Connection removed. Pick another.";
    case "mcp_server": {
      if (!label) return "Server removed. Pick another.";
      const allowed = Array.isArray(d.tool_allowlist) ? d.tool_allowlist.length : 0;
      return allowed === 0
        ? "No tools allowed yet"
        : plural(allowed, "tool allowed", "tools allowed");
    }
    case "tool":
      return APPROVAL[str(d.approval)] ?? "Built-in tool";
    case "memory":
      if (d.persist_history === false && d.memory_tool !== true) return "Keeps nothing";
      return d.memory_tool === true ? "Keeps chat history and notes" : "Keeps chat history";
    default:
      return "";
  }
}

/** A node its own data says is switched off. Memory that keeps nothing is
 *  the one case the graph holds; servers switched off come from the page. */
export function switchedOffByData(node: Pick<GraphNode, "type" | "data">): boolean {
  return (
    node.type === "memory" && node.data.persist_history === false && node.data.memory_tool !== true
  );
}

/** "2 problems, 1 to check" for a station's lamp. */
export function problemSummary(errors: number, warnings: number): string {
  if (errors > 0 && warnings > 0) {
    return `${plural(errors, "problem", "problems")}, ${warnings} to check`;
  }
  if (errors > 0) return plural(errors, "problem", "problems");
  return plural(warnings, "thing to check", "things to check");
}

/** Why Publish is off: "Fix 2 problems to publish". */
export function publishBlockedReason(errors: number): string {
  return `Fix ${plural(errors, "problem", "problems")} to publish`;
}

/** Whether a node's data says it can change data: a database with its
 *  writes exposed. Nothing else holds that fact, so nothing else is
 *  marked (no marker means read-only or unknown). */
export function canChangeData(node: Pick<GraphNode, "type" | "data">): boolean {
  return node.type === "database" && node.data.expose_write === true;
}
