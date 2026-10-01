/** The stations every assistant has. A sample is told apart by what joins
 *  its Agent, so the gallery leaves these out. */
const MAIN_LINE = new Set(["input", "guardrail", "agent", "output"]);

/** Where each kind sits when a sample's line is drawn: knowledge first, then
 *  what it can do, then who helps (docs/DESIGN.md section 2). */
const ORDER = ["knowledge_base", "memory", "database", "tool", "mcp_server", "subagent", "router"];

export type SampleStop = { type: string; count: number };

/** What a sample connects to its Agent, one entry per kind with how many,
 *  in a fixed order. `["input", "tool", "tool", "agent"]` is one stop:
 *  two tools. */
export function sampleStops(nodeTypes: string[]): SampleStop[] {
  const counts = new Map<string, number>();
  for (const type of nodeTypes) {
    if (!MAIN_LINE.has(type)) counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  const rank = (type: string) => {
    const i = ORDER.indexOf(type);
    return i === -1 ? ORDER.length : i;
  };
  return [...counts.entries()]
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => rank(a.type) - rank(b.type) || a.type.localeCompare(b.type));
}

/** "3 documents and 5 test questions", or whichever of the two it has. */
export function sampleExtras(documents: number, evalCases: number): string | null {
  const parts: string[] = [];
  if (documents > 0) parts.push(`${documents} ${documents === 1 ? "document" : "documents"}`);
  if (evalCases > 0) {
    parts.push(`${evalCases} ${evalCases === 1 ? "test question" : "test questions"}`);
  }
  return parts.length ? `Comes with ${parts.join(" and ")}.` : null;
}
