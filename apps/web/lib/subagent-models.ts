/** A subagent role's own model settings (task 5.1), as Panels edits them.
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
