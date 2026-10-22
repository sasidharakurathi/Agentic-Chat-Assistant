/** Keeping a conversation whole on a weak connection (Phase 8.6).
 *
 *  An answer is written on the server whether or not anyone watches. When
 *  the connection drops partway, the page finds the turn and watches it
 *  again from the last event it saw, so nothing is missed or repeated and
 *  nothing is paid twice. When sending fails before the server kept the
 *  message, the text goes back into the box: nothing typed is lost. */

import { ApiError } from "@/lib/api";
import { matchesMasked } from "@/lib/typed-secrets";

/** Waits before each try to watch a dropped answer again: then give up and
 *  offer to reload. */
export const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000] as const;

/** The request never got an answer that says anything: no network, or the
 *  app's own "can't reach the server" (status 0). A refusal (403, 409,
 *  429) is an answer. */
export function unreachable(err: unknown): boolean {
  return !(err instanceof ApiError) || err.status === 0;
}

/** The message box after a send that wasn't kept: the text comes back,
 *  unless something new has been typed meanwhile. */
export function giveBack(box: string, attempt: string): string {
  return box.trim() ? box : attempt;
}

/** Whether the server kept a message whose send never got an answer: it
 *  is the newest message you wrote. (Asking the same thing twice in a row
 *  reads as kept: the box isn't refilled, which loses nothing typed.) The
 *  server keeps a typed password masked (Phase 9.6), so the stored text is
 *  compared as the masked form of what was typed: otherwise the box would
 *  be refilled with the password, and it could be sent twice. */
export function keptAlready(
  messages: { role: string; content: string }[],
  attempt: string | undefined,
): boolean {
  if (!attempt) return true;
  const mine = [...messages].reverse().find((m) => m.role === "user");
  return mine !== undefined && matchesMasked(mine.content, attempt);
}

/** The codes of a used-up limit that has a time it starts again: the
 *  organisation's or assistant's budget, the asker's own limit, and the
 *  builder budget that previews spend from (Phase 9.7). */
const LIMITS_WITH_RESET = new Set(["budget_exceeded", "person_limit", "builder_budget"]);

/** "It will be back at 05:30." for a turn refused by a used-up limit that
 *  says when it resets; empty for any other failure. */
export function limitBack(code: string, resetsAt: string | null | undefined, now = new Date()) {
  return LIMITS_WITH_RESET.has(code) ? whenBack(resetsAt, now) : "";
}

/** Seconds to wait before trying again: from a 429's details, or a failed
 *  turn's `retry_after_s`. Null when it doesn't say. */
export function retryAfterOf(source: unknown): number | null {
  let seconds: unknown = null;
  if (source instanceof ApiError) {
    const details = source.details as { retry_after_s?: unknown } | undefined;
    seconds = details?.retry_after_s;
  } else if (typeof source === "object" && source !== null && "retry_after_s" in source) {
    seconds = (source as { retry_after_s?: unknown }).retry_after_s;
  }
  return typeof seconds === "number" && seconds > 0 ? Math.ceil(seconds) : null;
}

/** When a used-up limit starts again, in the reader's own time: "It will
 *  be back at 05:30." today, or "It will be back on 1 November." Empty
 *  when the server didn't say. */
export function whenBack(resetsAt: string | null | undefined, now = new Date(), locale?: string) {
  if (!resetsAt) return "";
  const at = new Date(resetsAt);
  if (Number.isNaN(at.getTime())) return "";
  const sameDay = at.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const time = at.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return `It will be back at ${time}.`;
  if (at.toDateString() === tomorrow.toDateString()) return `It will be back tomorrow at ${time}.`;
  return `It will be back on ${at.toLocaleDateString(locale, { day: "numeric", month: "long" })}.`;
}

/** Wait, unless the conversation is left meanwhile. */
export function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const t = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        resolve();
      },
      { once: true },
    );
  });
}
