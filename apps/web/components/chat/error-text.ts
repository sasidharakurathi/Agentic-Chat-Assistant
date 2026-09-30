import { ApiError } from "@/lib/api";
import { failureMessage } from "@/lib/format";

/** A server message as a sentence: capitalised, with a full stop. */
export function asSentence(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const s = t[0].toUpperCase() + t.slice(1);
  return /[.!?…]$/.test(s) ? s : `${s}.`;
}

/** What to do when the server couldn't be reached, whatever failed. */
export const CONNECTION_TODO = "Check your connection, then try again.";

/** An error in the app's words (docs/DESIGN.md section 9): what failed, the
 *  server's reason when there is one, then what to do. Built on the shared
 *  `failureMessage` so chat reads like every other page. Anything that
 *  isn't an API error is treated as the connection dropping, which has its
 *  own fix whatever `fix` says. */
export function failureText(what: string, err: unknown, fix = "Try again in a moment."): string {
  if (err instanceof ApiError) return failureMessage(what, asSentence(err.message), fix);
  return failureMessage(what, null, CONNECTION_TODO);
}
