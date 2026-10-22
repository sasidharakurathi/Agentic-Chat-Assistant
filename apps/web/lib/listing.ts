/** An assistant's listing in words (Phase 8.5): who it is for, as a builder
 *  or a reviewer reads it. */

export type AudienceLike = {
  kind: "everyone" | "groups" | "people";
  group_ids?: string[];
  user_ids?: string[];
};

/** "Everyone", "IT and HR", "Pat, Sam and 3 more". Names that aren't known
 *  any more read as "a removed group" or "someone removed". */
export function audienceWords(
  audience: AudienceLike | null | undefined,
  groups: Record<string, string>,
  people: Record<string, string>,
): string {
  if (!audience || audience.kind === "everyone") return "Everyone in the organisation";
  const names =
    audience.kind === "groups"
      ? (audience.group_ids ?? []).map((id) => groups[id] ?? "a removed group")
      : (audience.user_ids ?? []).map((id) => people[id] ?? "someone removed");
  if (names.length === 0) return "Nobody";
  const shown = names.slice(0, 3);
  const rest = names.length - shown.length;
  const head = rest > 0 ? [...shown, `${rest} more`] : shown;
  const list =
    head.length === 1 ? head[0] : `${head.slice(0, -1).join(", ")} and ${head[head.length - 1]}`;
  return audience.kind === "groups" ? `Members of ${list}` : list;
}

/** The categories the catalog offers, from the assistants listed. */
export function categoriesOf(items: { category?: string | null }[]): string[] {
  const seen = new Map<string, string>();
  for (const i of items) {
    const c = (i.category ?? "").trim();
    if (c && !seen.has(c.toLowerCase())) seen.set(c.toLowerCase(), c);
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
}
