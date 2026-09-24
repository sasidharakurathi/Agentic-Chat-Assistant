/**
 * Types shared between the API and the web app.
 *
 * Most of this is generated, not written: `api.gen.ts` comes from the
 * backend's OpenAPI schema (`openapi.json`, exported by
 * `apps/api/scripts/export_openapi.py`). A pytest snapshot test fails when the
 * committed schema falls behind the API, and `npm run gen:check` fails when
 * the TypeScript falls behind the schema, so the web app can no longer
 * compile against an API shape that no longer exists.
 *
 * To regenerate after an API change:
 *   python -m scripts.export_openapi          (from apps/api)
 *   npm run gen -w @assistant-studio/shared
 */

export type { components, operations, paths } from "./api.gen";

import type { components } from "./api.gen";

/** Every response/request model the API declares, by its Pydantic name. */
export type Schemas = components["schemas"];

export const MEMBER_ROLES = ["owner", "admin", "member"] as const;
export type MemberRole = (typeof MEMBER_ROLES)[number];

/** SSE event kinds streamed by POST /conversations/{id}/messages. */
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

// Compile-time proof that the hand-kept constants above still match the API.
// If a role is added to the backend enum, this line stops compiling.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const _rolesMatch: Same<MemberRole, Schemas["MemberRole"]> = true;
void _rolesMatch;
