/** Whether the page runs as the installed app (Phase 8.6): from a phone's
 *  home screen, not a browser tab. On an iPhone that app keeps its own
 *  storage, which Safari (where Google sign-in can end up) doesn't share,
 *  so sign-in offers a code from another device first. */
export function isInstalledApp(): boolean {
  if (typeof window === "undefined") return false;
  const ios = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return ios || window.matchMedia?.("(display-mode: standalone)").matches === true;
}

/** A device code as it is shown: two groups of four ("ABCD-EFGH"). */
export function showCode(code: string): string {
  const plain = code.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  return plain.length === 8 ? `${plain.slice(0, 4)}-${plain.slice(4)}` : plain;
}
