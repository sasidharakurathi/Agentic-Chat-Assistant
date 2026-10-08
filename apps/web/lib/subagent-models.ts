/** A subagent role's own model settings, as the Settings tab edits them.
 *
 *  The config holds, per role, either nothing (the role uses the shared
 *  subagent model, `models.subagent`) or a full model spec of its own
 *  (`subagents.models[role]`). A spec that matches the shared model and
 *  effort, and only adds a turn limit, *follows* the shared model: changing
 *  the shared model carries through to it, and dropping its limit drops it.
 *  New specs start as a copy of the shared one, effort included, the same
 *  way compile builds them from a canvas node. */

export type Spec = Record<string, unknown>;

const str = (v: unknown, fallback = "") => (typeof v === "string" ? v : fallback);

export function followsShared(own: Spec | undefined, shared: Spec): boolean {
  return (
    !!own &&
    str(own.model) === str(shared.model) &&
    str(own.effort, "high") === str(shared.effort, "high")
  );
}

/** The model select: "" means "the subagent model". */
export function pickModel(own: Spec | undefined, shared: Spec, model: string): Spec | null {
  if (model) return { ...shared, ...(own ?? {}), model };
  const turns = own?.max_turns;
  return turns ? { ...shared, max_turns: turns } : null;
}

/** The turn limit field: null means "the role's default". */
export function setTurns(own: Spec | undefined, shared: Spec, turns: number | null): Spec | null {
  if (turns !== null) return { ...shared, ...(own ?? {}), max_turns: turns };
  if (!own) return null;
  return followsShared(own, shared) ? null : { ...own, max_turns: null };
}

/** A change to the shared model, carried to the roles that follow it. */
export function carryShared(
  own: Record<string, Spec>,
  shared: Spec,
  patch: Spec,
): Record<string, Spec> {
  return Object.fromEntries(
    Object.entries(own).map(([role, spec]) => [
      role,
      followsShared(spec, shared) ? { ...spec, ...patch } : spec,
    ]),
  );
}

/** What the model select shows. */
export function selectedModel(own: Spec | undefined, shared: Spec): string {
  return own && !followsShared(own, shared) ? str(own.model) : "";
}

/** The families an assistant can name instead of one model (Phase 7a.6):
 *  each runs as that family's current model, which the operator repoints. */
const FAMILIES = new Set(["haiku", "sonnet", "opus", "fable"]);

/** A model id in plain words: "claude-haiku-4-5" reads "Haiku 4.5", and the
 *  alias "sonnet" reads "Sonnet (latest)". Ids that don't follow that shape
 *  are shown as they are. */
export function modelName(id: string): string {
  if (FAMILIES.has(id)) return `${id.charAt(0).toUpperCase()}${id.slice(1)} (latest)`;
  const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(id);
  if (!m) return id;
  const family = m[1].charAt(0).toUpperCase() + m[1].slice(1);
  return `${family} ${m[2]}${m[3] ? `.${m[3]}` : ""}`;
}
