/**
 * Shared types between the API and the web app.
 *
 * Phase 1 will generate the bulk of this from the backend's OpenAPI schema
 * (`openapi-typescript`). For now it holds only hand-maintained constants that
 * both sides need to agree on.
 */

export const MEMBER_ROLES = ["owner", "admin", "member"] as const;
export type MemberRole = (typeof MEMBER_ROLES)[number];

/** SSE event kinds streamed by POST /conversations/{id}/messages (Phase 1). */
export const CHAT_EVENT_KINDS = [
  "token",
  "thinking",
  "tool_call",
  "tool_result",
  "citation",
  "approval_required",
  "usage",
  "error",
  "done",
] as const;
export type ChatEventKind = (typeof CHAT_EVENT_KINDS)[number];
