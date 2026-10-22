/** Why an admin opened someone else's conversation (Phase 7b.2).
 *
 *  Conversations are private. An admin may read anyone's, but the server
 *  asks for a reason on every read of it and records the read before
 *  returning anything. The reason is asked once, kept here for this tab,
 *  and sent with each of that conversation's reads. URL-encoded, because a
 *  header carries Latin-1 only. */

export const READ_REASON_HEADER = "X-Read-Reason";

const reasons = new Map<string, string>();

export function setReadReason(conversationId: string, reason: string): void {
  reasons.set(conversationId, reason);
}

export function hasReadReason(conversationId: string): boolean {
  return reasons.has(conversationId);
}

const CONVERSATION_PATH = /\/api\/v1\/conversations\/([0-9a-fA-F-]{36})(?:[/:?]|$)/;

/** The header for a request to `path`, when it reads a conversation the
 *  admin gave a reason for; otherwise nothing. */
export function readReasonHeaders(path: string): Record<string, string> {
  const id = CONVERSATION_PATH.exec(path)?.[1];
  const reason = id ? reasons.get(id) : undefined;
  return reason ? { [READ_REASON_HEADER]: encodeURIComponent(reason) } : {};
}

/** For tests. */
export function clearReadReasons(): void {
  reasons.clear();
}
