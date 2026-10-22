/** Keeping and erasing, in words (Phase 8.10). */

/** India's DPDP one-year floor for keeping logs: below it, a warning. */
export const DPDP_FLOOR_DAYS = 365;
export const DAYS_MIN = 30;
export const DAYS_MAX = 3650;

/** What a retention setting means, and a warning below the floor. */
export function retentionWords(days: number): { meaning: string; warning: string | null } {
  const years = days / 365;
  const span = days % 365 === 0 ? `${years} ${years === 1 ? "year" : "years"}` : `${days} days`;
  return {
    meaning: `Conversations with no message for ${span} are erased for good, every night.`,
    warning:
      days < DPDP_FLOOR_DAYS
        ? "Shorter than a year. India's DPDP rules ask for logs to be kept for at least a year: check with whoever handles compliance before you shorten it."
        : null,
  };
}

/** One erasure in a line: "Retention: 12 conversations", or a person's. */
export function erasureWords(e: {
  kind: string;
  user_email?: string | null;
  conversations: number;
  memories: number;
}): string {
  const n = `${e.conversations} ${e.conversations === 1 ? "conversation" : "conversations"}`;
  if (e.kind === "person") {
    const who = e.user_email ?? "a former member";
    const notes = e.memories ? `, ${e.memories} memory ${e.memories === 1 ? "note" : "notes"}` : "";
    return `Erased ${who}'s data: ${n}${notes}`;
  }
  return `Retention: ${n}`;
}

/** What went with an erasure's conversations (Phase 10.5), in a line:
 *  "With them: 12 messages, 3 run traces, 1 summary". Null when the record
 *  predates the counts, or nothing went. */
const DETAIL_WORDS: [key: string, one: string, many: string][] = [
  ["messages", "message", "messages"],
  ["traces", "run trace", "run traces"],
  ["tool_calls", "tool call", "tool calls"],
  ["summaries", "summary", "summaries"],
  ["share_links", "share link", "share links"],
  ["feedback", "piece of feedback", "pieces of feedback"],
  ["review_items", "review decision", "review decisions"],
  ["conversation_memories", "conversation note", "conversation notes"],
  ["screenshots", "screenshot", "screenshots"],
];

export function erasureDetailWords(details?: Record<string, number> | null): string | null {
  if (!details) return null;
  const parts = DETAIL_WORDS.filter(([key]) => (details[key] ?? 0) > 0).map(
    ([key, one, many]) => `${details[key]} ${details[key] === 1 ? one : many}`,
  );
  return parts.length ? `With them: ${parts.join(", ")}` : null;
}
