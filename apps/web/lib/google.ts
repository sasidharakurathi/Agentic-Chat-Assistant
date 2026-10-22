/** Google sign-in in the web app (Phase 8.3). The flow runs through the
 *  API: the browser goes to its `start`, Google, its `callback`, then back
 *  here to `/auth/google#code=...`, where the one-minute code is traded for
 *  the session tokens. The code is after `#`: no server sees it. */

import { API_URL } from "@/lib/api";
import { safeNext } from "@/lib/safe-next";

/** Where a "Continue with Google" button goes. */
export function googleStartUrl(opts: {
  next?: string | null;
  invite?: string | null;
  ticket?: string | null;
}): string {
  const q = new URLSearchParams();
  const next = safeNext(opts.next ?? null);
  if (next) q.set("next", next);
  if (opts.invite) q.set("invite", opts.invite);
  if (opts.ticket) q.set("ticket", opts.ticket);
  const qs = q.toString();
  return `${API_URL}/api/v1/auth/google/start${qs ? `?${qs}` : ""}`;
}

/** The code and the page to go on to, from `#code=...&next=...`. */
export function parseHandoff(hash: string): { code: string | null; next: string | null } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return { code: params.get("code"), next: safeNext(params.get("next")) };
}

/** Why a Google sign-in didn't go through, and what to do (from the API's
 *  `?google=` code). */
const WORDS: Record<string, string> = {
  no_invite:
    "Sign-up here is by invitation. Ask an admin for an invite, then use its link to join with Google.",
  password_account:
    "This address already has an account with a password. Sign in with it, then connect Google in Your account.",
  account_disabled: "This account is switched off. Ask an admin if you think that's a mistake.",
  domain: "Use your company Google account: other Google accounts can't sign in here.",
  unverified: "Google hasn't confirmed this address. Use your company Google account.",
  invite:
    "This invite can't be used with that Google account: it's for another address, used, withdrawn or expired.",
  linked_elsewhere: "That Google account is already connected to another account here.",
  already_linked: "This account is already connected to a different Google account.",
  email_mismatch:
    "That Google account has a different email address from this account. Connect the Google account with the same address.",
  state: "The sign-in expired or was started in another browser. Try again.",
  ticket: "That took too long. Try connecting Google again.",
  cancelled: "Google sign-in was cancelled.",
  exchange: "Google didn't confirm the sign-in. Try again.",
  token:
    "Google's answer couldn't be checked. Try again, and tell your IT team if it keeps happening.",
};

export function googleError(code: string | null | undefined): string | null {
  if (!code || code === "linked") return null;
  return WORDS[code] ?? "Google sign-in didn't work. Try again.";
}
