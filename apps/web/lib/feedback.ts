/** Feedback in words (Phase 8.8). */

/** "Did this solve it?" as a rate, always with how many answered: a rate
 *  from three answers out of a hundred conversations isn't a deflection
 *  rate, and shouldn't read like one. */
export function solvedWords(yes: number, no: number, conversations: number): string {
  const answered = yes + no;
  if (answered === 0) {
    return conversations === 0
      ? "No conversations yet."
      : `Nobody has said yet, in ${conversations} ${conversations === 1 ? "conversation" : "conversations"}.`;
  }
  const rate = Math.round((yes / answered) * 100);
  return `${rate}% said it solved it: ${yes} of ${answered} who answered, in ${conversations} ${
    conversations === 1 ? "conversation" : "conversations"
  }.`;
}

/** Thumbs as a share of thumbs up, or null with none. */
export function upShare(up: number, down: number): number | null {
  const all = up + down;
  return all === 0 ? null : Math.round((up / all) * 100);
}
