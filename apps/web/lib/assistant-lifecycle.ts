/** The approval gate, as the web app shows it (Phase 7b.1).
 *
 *  A builder submits a version; an admin approves or rejects it; only the
 *  approved, live version answers in Chat. The draft is tested in a preview
 *  that only its builders and admins can open. The server enforces all of
 *  it; these decide what to offer, so nobody is shown a button that would
 *  only be refused. */

import type { Schemas } from "@assistant-studio/shared";

export type VersionStatus = Schemas["VersionStatus"];
export type AssistantStatus = Schemas["AssistantStatus"];

/** Admins and the owner review versions, roll back and pause. */
export function isAdminRole(role: string | null | undefined): boolean {
  return role === "admin" || role === "owner";
}

/** The server's rule (`AssistantContext.can_edit`): an admin, its creator,
 *  or a co-editor (Phase 7b.4). The assistant's detail carries the server's
 *  own answer, which is used when present: co-editors are known there. */
export function canEditAssistant(
  assistant: { created_by?: string | null; can_edit?: boolean } | null | undefined,
  userId: string | null | undefined,
  role: string | null | undefined,
): boolean {
  if (typeof assistant?.can_edit === "boolean") return assistant.can_edit;
  if (isAdminRole(role)) return true;
  return Boolean(assistant?.created_by && userId && assistant.created_by === userId);
}

/** The assistant's state, in the header and the list. */
export const ASSISTANT_STATUS_WORD: Record<AssistantStatus, string> = {
  draft: "Not approved yet",
  published: "Live",
  archived: "Paused",
};

export const VERSION_STATUS_WORD: Record<VersionStatus, string> = {
  submitted: "Waiting for review",
  approved: "Approved",
  rejected: "Rejected",
  superseded: "Replaced",
  withdrawn: "Withdrawn",
  rolled_back: "Rolled back",
};

/** Why nobody can chat with it, or null when Chat answers. */
export function chatUnavailable(assistant: {
  status: AssistantStatus;
  current_version_id?: string | null;
}): string | null {
  if (assistant.status === "archived") return "An admin has paused this assistant.";
  if (!assistant.current_version_id) return "No version of this assistant has been approved yet.";
  return null;
}

export type VersionAction = "approve" | "reject" | "withdraw" | "make-live" | "preview";

/** Whether this admin may approve a version (Phase 7b.3), as the server
 *  decides it: not if they submitted it or built the assistant, unless they
 *  are the owner and nobody else can review, and then only with a note. */
export type ApproveRule = "allowed" | "needs_note" | "refused";

export function approveRule({
  me,
  role,
  submittedBy,
  builtBy,
  reviewers,
}: {
  me: string | null | undefined;
  role: string | null | undefined;
  submittedBy: string | null | undefined;
  builtBy: string | null | undefined;
  /** The ids of the org's active admins and owners. */
  reviewers: string[];
}): ApproveRule {
  if (!isAdminRole(role) || !me) return "refused";
  const involved = new Set([submittedBy, builtBy].filter((u): u is string => Boolean(u)));
  if (!involved.has(me)) return "allowed";
  const others = reviewers.filter((r) => r !== me && !involved.has(r));
  if (others.length > 0 || role !== "owner") return "refused";
  return "needs_note";
}

/** The org's active admins and owners: who can approve an assistant. */
export function reviewersOf(
  members: { user_id: string; role: string; is_active?: boolean }[],
): string[] {
  return members.filter((m) => isAdminRole(m.role) && m.is_active !== false).map((m) => m.user_id);
}

/** What may be done with one version, by whom. `live`: it is the version
 *  Chat answers from now. `approve`: whether this admin may approve it. */
export function versionActions(
  version: { status: VersionStatus },
  {
    live,
    admin,
    editor,
    approve = "allowed",
  }: { live: boolean; admin: boolean; editor: boolean; approve?: ApproveRule },
): VersionAction[] {
  const out: VersionAction[] = [];
  if (version.status === "submitted") {
    if (admin && approve !== "refused") out.push("approve");
    if (admin) out.push("reject");
    if (editor) out.push("withdraw");
  }
  if (version.status === "approved" && !live && admin) out.push("make-live");
  if (editor) out.push("preview");
  return out;
}

/** The chat page's address for a preview: of the draft, or of one version. */
export function previewHref(assistantId: string, version?: number): string {
  const base = `/assistants/${assistantId}/chat?preview=1`;
  return version === undefined ? base : `${base}&version=${version}`;
}

const RANK: Record<string, number> = { member: 1, admin: 2, owner: 3 };

/** Whether `me` may remove (deactivate) or reactivate `them`, as the server
 *  decides it (Phase 7b.4): admins and owners; not themselves; an admin only
 *  someone ranked below them. */
export function canRemove(
  me: { id: string | null | undefined; role: string | null | undefined },
  them: { user_id: string; role: string },
): boolean {
  if (!isAdminRole(me.role) || !me.id || me.id === them.user_id) return false;
  if (me.role === "owner") return true;
  return (RANK[them.role] ?? 0) < (RANK[me.role ?? ""] ?? 0);
}
