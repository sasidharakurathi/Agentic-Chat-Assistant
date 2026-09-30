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
