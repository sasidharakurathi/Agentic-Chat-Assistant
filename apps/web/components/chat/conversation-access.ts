/** Who may act in a conversation (Phase 7a.4).
 *
 *  Only the person who started it can reply in it, stop its answer, rename
 *  it or archive it; the API refuses everyone else. Others who can open it,
 *  admins included, get a read-only view, so they are never offered a
 *  message box that would only be refused. */
export function canAct(
  conversation: { created_by?: string | null } | undefined,
  userId: string | null | undefined,
): boolean {
  return Boolean(conversation?.created_by && userId && conversation.created_by === userId);
}
