/** How a tool's name reads to a person.
 *
 *  `mcp__caps__sql_query` -> `sql_query` (the platform's own tools);
 *  `mcp__github__search` -> `github: search` (a registered MCP server's tool,
 *  task 4.6, so it is clear whose tool asked); anything else unchanged. */
export function toolLabel(name: string): string {
  const m = /^mcp__([^_]+(?:-[^_]+)*)__(.+)$/.exec(name);
  if (!m) return name;
  const [, server, tool] = m;
  return server === "caps" ? tool : `${server}: ${tool}`;
}

/** The bare tool name, without any server: what the UI keys behaviour on. */
export function toolShortName(name: string): string {
  const m = /^mcp__[^_]+(?:-[^_]+)*__(.+)$/.exec(name);
  return m ? m[1] : name;
}

/** A delegation call names its subagent (task 5.1: there are three now):
 *  `Agent` with `{subagent_type: "sql"}` -> `sql subagent`. Null for any
 *  other call. */
export function subagentLabel(
  name: string,
  input: Record<string, unknown> | undefined,
): string | null {
  const role = input?.subagent_type;
  return name === "Agent" && typeof role === "string" && role ? `${role} subagent` : null;
}

/** The node type a tool call belongs to, so the chat can draw its step in
 *  that capability's line colour. Mirrors the server's own mapping from a
 *  call to a canvas node (`app/graph/trace_nodes.py`): the platform's
 *  knowledge, database, memory and tool calls; a registered MCP server's
 *  tools; a delegation to a subagent. Anything unknown counts as a tool. */
export type ToolNodeType =
  "knowledge_base" | "memory" | "database" | "tool" | "mcp_server" | "subagent";

const CAPS_TYPE: Record<string, ToolNodeType> = {
  kb_search: "knowledge_base",
  kb_list_sources: "knowledge_base",
  sql_query: "database",
  sql_introspect: "database",
  sql_list_schemas: "database",
  mongo_find: "database",
  mongo_aggregate: "database",
  memory: "memory",
  http_request: "tool",
  calculator: "tool",
  datetime: "tool",
};

function mcpParts(name: string): { server: string; tool: string } | null {
  const m = /^mcp__([^_]+(?:-[^_]+)*)__(.+)$/.exec(name);
  return m ? { server: m[1], tool: m[2] } : null;
}

export function toolNodeType(name: string): ToolNodeType {
  if (name === "Agent") return "subagent";
  const parts = mcpParts(name);
  if (!parts) return "tool";
  if (parts.server !== "caps") return "mcp_server";
  return CAPS_TYPE[parts.tool] ?? "tool";
}

function quoted(value: unknown, max = 60): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/\s+/g, " ");
  if (!text) return null;
  return `"${text.length > max ? `${text.slice(0, max - 1)}…` : text}"`;
}

function named(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function host(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try {
    return new URL(url).host || null;
  } catch {
    return null;
  }
}

/** What a tool call did, as a plain sentence: "Ran a SQL query",
 *  "Searched the knowledge base for "refunds"", "Used search on github".
 *  Built only from the call's own name and input; nothing is guessed. */
export function toolSentence(name: string, input?: Record<string, unknown> | null): string {
  const args = input ?? {};
  if (name === "Agent") {
    const role = named(args.subagent_type);
    return role ? `Asked the ${role} subagent` : "Asked a subagent";
  }
  if (name === "WebSearch") {
    const q = quoted(args.query);
    return q ? `Searched the web for ${q}` : "Searched the web";
  }
  const parts = mcpParts(name);
  if (!parts) return `Used ${name}`;
  if (parts.server !== "caps") return `Used ${parts.tool} on ${parts.server}`;
  switch (parts.tool) {
    case "kb_search": {
      const q = quoted(args.query);
      return q ? `Searched the knowledge base for ${q}` : "Searched the knowledge base";
    }
    case "kb_list_sources":
      return "Listed the knowledge base sources";
    case "sql_list_schemas":
      return "Listed the databases it can use";
    case "sql_introspect": {
      const table = named(args.table);
      return table ? `Looked up the columns of ${table}` : "Looked up the tables and columns";
    }
    case "sql_query":
      return "Ran a SQL query";
    case "mongo_find": {
      const c = named(args.collection);
      return c ? `Read documents from ${c}` : "Read documents from a collection";
    }
    case "mongo_aggregate": {
      const c = named(args.collection);
      return c ? `Ran an aggregation on ${c}` : "Ran an aggregation";
    }
    case "http_request": {
      const method = named(args.method)?.toUpperCase();
      const where = host(args.url);
      if (method && where) return `Sent a ${method} request to ${where}`;
      return where ? `Sent a request to ${where}` : "Sent a web request";
    }
    case "calculator":
      return "Did a calculation";
    case "datetime":
      return "Checked the date and time";
    case "memory": {
      const command = named(args.command);
      if (command === "view") return "Read its memory";
      if (command === "delete") return "Deleted something from its memory";
      if (command) return "Updated its memory";
      return "Used its memory";
    }
    default:
      return `Used ${parts.tool}`;
  }
}
