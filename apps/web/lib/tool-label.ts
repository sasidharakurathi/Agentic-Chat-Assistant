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
