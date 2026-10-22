/** The organisation's steps for getting on its network (Phase 8.6), kept
 *  in this browser for the offline page (public/offline.html), which runs
 *  when the server can't be reached and so can't ask it. Read back by
 *  public/offline.js under the same key: change both together. */

export const OFFLINE_HELP_KEY = "as.offlineHelp";

export type OfflineHelp = { organisation: string; offline_help?: string };

export function rememberOfflineHelp(help: OfflineHelp): void {
  try {
    window.localStorage.setItem(
      OFFLINE_HELP_KEY,
      JSON.stringify({ organisation: help.organisation, steps: help.offline_help ?? "" }),
    );
  } catch {
    // Private mode or full storage: the offline page says the generic part.
  }
}

/** What the offline page shows: the organisation's name and its steps,
 *  when this browser has seen them. */
export function readOfflineHelp(raw: string | null): { organisation: string; steps: string } {
  try {
    const parsed = JSON.parse(raw ?? "") as { organisation?: unknown; steps?: unknown };
    return {
      organisation: typeof parsed.organisation === "string" ? parsed.organisation : "",
      steps: typeof parsed.steps === "string" ? parsed.steps : "",
    };
  } catch {
    return { organisation: "", steps: "" };
  }
}
