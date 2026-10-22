/** Declared actions in the builder (Phase 9.1). */

export type Tier = "read" | "confirm" | "approver";

/** How each tier runs, in a badge's words. */
export const TIER_WORDS: Record<Tier, string> = {
  read: "Runs at once",
  confirm: "Asker confirms",
  approver: "Approver decides",
};

/** A name for an action from its title: "Request access to an app" ->
 *  "request_access_to_an_app". Lower case, digits and underscores, starting
 *  with a letter, at most 40 characters, as the server requires. */
export function actionName(title: string): string {
  const slug = title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40)
    .replace(/_+$/, "");
  return /^[a-z]/.test(slug) ? slug : slug ? `a_${slug}`.slice(0, 40) : "";
}
