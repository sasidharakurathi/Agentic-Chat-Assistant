/**
 * Typed client for the Assistant Studio backend (Phase 1).
 * Tokens live in localStorage; the active org id is attached as X-Org-Id.
 */

import { createAuthedFetch, createRefresher } from "@/lib/session";
import { readReasonHeaders } from "@/lib/read-reasons";
import type { Schemas } from "@assistant-studio/shared";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "as.access";
const REFRESH_KEY = "as.refresh";
const ORG_KEY = "as.org";

export type ApiErrorBody = {
  error: { code: string; message: string; details?: unknown };
  request_id?: string;
};

/** The reasons behind a 422, in the server's own words.
 *
 *  A validation failure's message is always "Request validation failed";
 *  what actually went wrong (a URL carrying a key, a bad name) is in
 *  `details.errors[].msg`. Forms showed only the generic line. Pydantic
 *  prefixes a validator's text with "Value error, ", which is dropped. */
export function validationMessage(details: unknown): string | null {
  const errors = (details as { errors?: { msg?: unknown }[] } | undefined)?.errors;
  if (!Array.isArray(errors)) return null;
  const msgs = errors
    .map((e) => (typeof e?.msg === "string" ? e.msg.replace(/^Value error, /, "") : ""))
    .filter(Boolean);
  return msgs.length ? [...new Set(msgs)].join(" ") : null;
}

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(status: number, body: ApiErrorBody) {
    super(
      (body?.error?.code === "validation_error" && validationMessage(body.error.details)) ||
        (body?.error?.message ?? `HTTP ${status}`),
    );
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown";
    this.details = body?.error?.details;
  }
}

const ls = {
  get: (k: string) => (typeof window === "undefined" ? null : window.localStorage.getItem(k)),
  set: (k: string, v: string) => window.localStorage.setItem(k, v),
  del: (k: string) => window.localStorage.removeItem(k),
};

export const tokenStore = {
  get access() {
    return ls.get(ACCESS_KEY);
  },
  get refresh() {
    return ls.get(REFRESH_KEY);
  },
  set(pair: TokenPair) {
    ls.set(ACCESS_KEY, pair.access_token);
    ls.set(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    ls.del(ACCESS_KEY);
    ls.del(REFRESH_KEY);
  },
};

export const orgStore = {
  get(): string | null {
    return ls.get(ORG_KEY);
  },
  set(id: string) {
    ls.set(ORG_KEY, id);
  },
  clear() {
    ls.del(ORG_KEY);
  },
};

/** `orgId`: ask in this organisation rather than the active one (a link
 *  that names a record in another, Phase 8.6). */
type Opts = { method?: string; body?: unknown; auth?: boolean; org?: boolean; orgId?: string };

/** One page of a list endpoint. Every list is paginated by an opaque
 *  cursor: pass `next_cursor` back to get the next page; `null` = done. */
export type Page<T> = { items: T[]; next_cursor: string | null };

function paged(path: string, cursor?: string | null, limit?: number): string {
  const q = new URLSearchParams();
  if (cursor) q.set("cursor", cursor);
  if (limit) q.set("limit", String(limit));
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}

/** Follow `next_cursor` to the end. Only for lists that are small by nature
 *  (one user's orgs, one assistant's connections) — a list that grows
 *  without bound gets real paging in the UI instead. */
export async function collectAll<T>(
  fetchPage: (cursor: string | null) => Promise<Page<T>>,
): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | null = null;
  for (let i = 0; i < 100; i++) {
    const page: Page<T> = await fetchPage(cursor);
    out.push(...page.items);
    if (!page.next_cursor) return out;
    cursor = page.next_cursor;
  }
  return out;
}

const refreshSession = createRefresher({
  tokens: tokenStore,
  refreshUrl: `${API_URL}/api/v1/auth/refresh`,
  locks: typeof navigator !== "undefined" && "locks" in navigator ? navigator.locks : null,
});

/** `fetch` with the access token, refreshed once on a 401 (lib/session.ts). */
const authedFetch = createAuthedFetch({
  tokens: tokenStore,
  refresh: refreshSession,
  offline: () =>
    new ApiError(0, {
      error: { code: "offline", message: "Can't reach the server right now. Trying again." },
    }),
});

async function request<T>(path: string, opts: Opts = {}): Promise<T> {
  const build = (token: string | null): RequestInit => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...readReasonHeaders(path),
    };
    if (opts.auth !== false && token) headers.Authorization = `Bearer ${token}`;
    if (opts.org !== false) {
      const org = opts.orgId ?? orgStore.get();
      if (org) headers["X-Org-Id"] = org;
    }
    return {
      method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    };
  };
  const url = `${API_URL}${path}`;
  const res = opts.auth === false ? await fetch(url, build(null)) : await authedFetch(url, build);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data as ApiErrorBody);
  return data as T;
}

// ── Types ────────────────────────────────────────────────────
//
// Generated from the API's OpenAPI schema (`@assistant-studio/shared`), not
// written by hand. These used to be a hand-maintained copy that drifted from
// the Pydantic models without anything noticing. Now a schema change that
// breaks the UI breaks `tsc` instead.
//
// Two shapes stay deliberately loose, because the canvas and the panels treat
// them as open records edited field by field: the pipeline `Graph` and the
// `AssistantConfig`. They are pinned to the generated types by the
// compile-time checks at the end of this section, so they cannot drift either.

type S = Schemas;

export type TokenPair = S["TokenPair"];
export type LoginOut = S["LoginOut"];
export type TwoFactorStatus = S["TwoFactorStatus"];

/** Two-step sign-in, for the signed-in person (Phase 8.4). */
export const twoFactor = {
  status: () => request<TwoFactorStatus>("/api/v1/auth/two-factor", { org: false }),
  setup: () =>
    request<S["TwoFactorSetupOut"]>("/api/v1/auth/two-factor/setup", {
      method: "POST",
      org: false,
    }),
  confirm: (code: string) =>
    request<S["TwoFactorConfirmed"]>("/api/v1/auth/two-factor/confirm", {
      body: { code },
      org: false,
    }),
  verify: (code: string) =>
    request<TokenPair>("/api/v1/auth/two-factor/verify", { body: { code }, org: false }),
  recoveryCodes: (code: string) =>
    request<S["RecoveryCodesOut"]>("/api/v1/auth/two-factor/recovery-codes", {
      body: { code },
      org: false,
    }),
  disable: (code: string) =>
    request<{ message: string }>("/api/v1/auth/two-factor/disable", {
      body: { code },
      org: false,
    }),
};
export type Me = S["MeResponse"];
export type Org = S["OrgOut"];
// Phase 9.7: the builder budget (hand-written until the types are regenerated).
export type OrgSettings = S["OrgSettingsOut"] & { builder_monthly_usd?: number | null };

export type GraphNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
};
export type GraphEdge = { id?: string | null; source: string; target: string };
export type Graph = { schema_version: number; nodes: GraphNode[]; edges: GraphEdge[] };

export type AssistantConfig = Record<string, unknown> & {
  system_prompt: string;
  models: { main: { model: string; effort: string } } & Record<string, unknown>;
  tools: Record<string, { enabled: boolean } & Record<string, unknown>>;
  guardrails: Record<string, unknown> & { rules: string[] };
};

export type ValidationIssue = S["GraphIssue"];
/** A suggested one-click fix for a graph issue (task 5.11). */
export type GraphFix = S["GraphFix"];
export type ValidationResult = S["ValidationResult"];

export type Assistant = S["AssistantSummary"];
export type SampleSummary = S["SampleSummary"];
export type AssistantDetail = Omit<S["AssistantDetail"], "draft_graph" | "draft_config"> & {
  draft_graph: Graph;
  draft_config: AssistantConfig;
};
export type AssistantVersion = S["VersionSummary"];
export type EffortLevel = S["ModelSpec-Output"]["effort"];
export type DiffEntry = S["DiffEntry"];
export type VersionDiff = S["VersionDiff"];

// Evals (task 6.1).
export type EvalSuite = S["EvalSuiteOut"];
export type EvalSuiteDetail = S["EvalSuiteDetail"];
export type EvalSuiteConfig = S["EvalSuiteConfig-Output"];
// Phase 9.8: `follow_ups` (hand-written until the types are regenerated).
export type EvalCase = S["EvalCaseOut"] & { follow_ups?: string[] };
// Phase 10.4: `expects_approval` (hand-written until the types are regenerated).
export type EvalExpected = S["EvalExpected-Output"] & { expects_approval?: boolean };
export type EvalLabels = S["EvalLabels-Output"];
/** A case as it is sent: every expectation is optional. */
export type EvalCaseDraft = {
  input: string;
  /** Sent after `input` in the same conversation (Phase 9.8). */
  follow_ups?: string[];
  expected?: Partial<EvalExpected>;
  labels?: Partial<EvalLabels>;
};
export type EvalRun = S["EvalRunSummary"];
export type EvalRunDetail = S["EvalRunDetail"];
/** One turn of a multi-turn case's result (Phase 9.8, hand-written). */
export type EvalTurn = {
  input: string;
  output: string;
  tools: string[];
  error: string | null;
  cost_usd: number;
  duration_ms: number;
  run_id: string | null;
  trace_id: string | null;
};
export type EvalCaseResult = S["EvalCaseResultOut"] & { turns?: EvalTurn[] };

// Approve on evidence (Phase 10.4). Hand-written until the types are
// regenerated; mirrors app/schemas/approval_evidence.py.
export type SuiteScore = {
  suite_id: string;
  suite_name: string;
  run_id: string | null;
  ran_on: "version" | "draft" | null;
  finished_at: string | null;
  cases: number;
  passed: number;
  failed: number;
  errors: number;
  pass_rate: number | null;
  previous_pass_rate: number | null;
  regressions: string[];
};
export type VersionEvidence = {
  version_number: number;
  compared_with: number | null;
  scorecard: SuiteScore[];
  safety: {
    checked_calls: number;
    writes_without_approval: number;
    approval_requests: number;
    approval_cases: number;
    approval_cases_passed: number;
    ok: boolean;
    flagged: { tool: string; run_id: string; conversation_id: string }[];
  };
  cost: {
    conversations_measured: number;
    avg_cost_usd: number | null;
    per_month: number;
    mid_usd: number | null;
    low_usd: number | null;
    high_usd: number | null;
    note: string;
  };
  notes: string[];
};

export type Conversation = S["ConversationSummary"];

// ── Chat (Phase 8.1) ─────────────────────────────────────────
//
// What Chat shows, for every role the Employee role included: an assistant
// as a card, your conversations, and (for admins) the requests waiting.

export type ChatAssistant = S["ChatAssistantOut"];
export type Listing = S["Listing"];
export type Group = S["GroupOut"];
export type GroupMember = S["GroupMemberOut"];
export type ChatConversation = S["ChatConversationOut"];
export type Notice = S["NoticeOut"];
export type NoticeAdmin = S["NoticeAdminOut"];
export type NoticeRevision = S["NoticeRevisionOut"];
export type ConversationRead = S["AccessReadOut"];
export type AssistantAbout = S["AssistantAboutOut"];
export type FeedbackSummary = S["FeedbackSummaryOut"];
export type LegalHoldItem = S["HoldOut"];
export type Erasure = S["ErasureOut"];

export const chat = {
  /** The assistants that answer in Chat for you (Phase 8.5): Official
   *  first; `q` searches, `category` narrows. */
  assistants: (filters: { q?: string; category?: string } = {}) => {
    const qs = new URLSearchParams();
    if (filters.q) qs.set("q", filters.q);
    if (filters.category) qs.set("category", filters.category);
    const tail = qs.toString();
    return request<ChatAssistant[]>(`/api/v1/chat/assistants${tail ? `?${tail}` : ""}`);
  },
  assistant: (id: string) => request<ChatAssistant>(`/api/v1/chat/assistants/${id}`),
  /** Your own conversations, with every assistant, newest first. Phase
   *  8.8: `q` finds words in a title or the conversation; `removed` lists
   *  the ones taken out of your history, to put back. */
  conversations: (cursor?: string | null, filters: { q?: string; removed?: boolean } = {}) => {
    let url = paged("/api/v1/chat/conversations", cursor);
    const add = (part: string) => (url = `${url}${url.includes("?") ? "&" : "?"}${part}`);
    if (filters.q?.trim()) add(`q=${encodeURIComponent(filters.q.trim())}`);
    if (filters.removed) add("removed=true");
    return request<Page<ChatConversation>>(url);
  },
  /** The organisation's steps for when the app can't reach the server
   *  (Phase 8.6), kept for the offline page. */
  help: () => request<S["ChatHelpOut"]>("/api/v1/chat/help"),
  /** How Chat uses your data (Phase 8.7), and whether you accepted it. */
  notice: () => request<Notice>("/api/v1/chat/notice"),
  acceptNotice: (version: number) =>
    request<Notice>("/api/v1/chat/notice:accept", { body: { version } }),
  /** Each time an admin opened one of your conversations, and why. */
  access: (cursor?: string | null) =>
    request<Page<ConversationRead>>(paged("/api/v1/chat/access", cursor)),
  /** An assistant's card: what its approved version uses and sends out. */
  about: (id: string) => request<AssistantAbout>(`/api/v1/chat/assistants/${id}/about`),
};
/** Where a cited chunk sits inside its source document. */
export type CitationLoc = {
  page?: number | null;
  page_end?: number | null;
  char_start?: number | null;
  char_end?: number | null;
  breadcrumb?: string[];
  ordinal?: number | null;
};

export type Citation = {
  marker: number;
  chunk_id: string;
  document_id: string;
  data_source_id: string | null;
  title: string;
  source_type: "file" | "url" | "text" | null;
  uri: string | null;
  snippet: string;
  score: number;
  loc: CitationLoc;
  /** Ready-to-open address, web sources only. A file needs a presigned URL
   *  minted on click; pasted text has no external target at all. */
  href: string | null;
  /** [start, end) offsets of this marker in the message content. The UI splits
   *  on these rather than re-deriving markers, so it can never disagree with
   *  the backend about what is and isn't a citation. */
  spans: [number, number][];
};

/** What a guardrail did during a turn (task 5.3): live as an event, and
 *  saved with the answer as a block. */
export type GuardrailFinding = {
  /** "secret": a password or key you typed was hidden (Phase 9.6). */
  check: "injection" | "exfiltration" | "pii" | "schema" | "budget" | "secret";
  where: "user_message" | "tool_input" | "tool_result";
  detail: string;
  tool?: string | null;
};

/** One entry of `Message.blocks`.
 *
 *  `type` is only present on messages written from task 2.9 onward — rows
 *  persisted before it are bare tool calls with no discriminator, so readers
 *  must treat "no type" as a tool call rather than filtering for
 *  `type === "tool_call"` (which would silently erase tool cards from all
 *  existing conversation history). */
export type MessageBlock =
  | ({ type?: "tool_call" } & {
      id: string;
      name: string;
      input: Record<string, unknown>;
      status?: string;
      output?: string;
    })
  | ({ type: "citation" } & Citation)
  | ({ type: "guardrail" } & GuardrailFinding)
  /** A screenshot sent with your message (Phase 9.6). */
  | { type: "image"; attachment_id: string };

/** `blocks` is untyped in the schema (a JSON list); the UI knows its shape. */
export type ChatMessage = Omit<S["MessageOut"], "blocks"> & { blocks: MessageBlock[] };

// The loose shapes must still accept whatever the API actually returns.
type Assignable<From, To> = [From] extends [To] ? true : false;
const _graphFits: Assignable<S["Graph-Output"], Graph> = true;
const _configFits: Assignable<S["AssistantConfig-Output"], AssistantConfig> = true;
void _graphFits;
void _configFits;

// ── Endpoints ────────────────────────────────────────────────

export const auth = {
  /** `inviteToken`: from the invite link that led here, for a server where
   *  sign-up is by invitation. */
  register: (email: string, password: string, name: string, inviteToken?: string | null) =>
    request<TokenPair>("/api/v1/auth/register", {
      body: { email, password, name, invite_token: inviteToken ?? null },
      auth: false,
      org: false,
    }),
  /** Tokens, or (two-step sign-in, Phase 8.4) a challenge to finish. */
  login: (email: string, password: string) =>
    request<LoginOut>("/api/v1/auth/login", {
      body: { email, password },
      auth: false,
      org: false,
    }),
  me: () => request<Me>("/api/v1/auth/me", { org: false }),
  /** A code to sign in on another device (Phase 8.6): five minutes, once. */
  deviceCode: () =>
    request<S["DeviceCodeOut"]>("/api/v1/auth/device-code", { method: "POST", org: false }),
  /** Sign in with a code from a signed-in device: tokens, or a challenge
   *  when the account has two-step sign-in. */
  redeemDeviceCode: (code: string) =>
    request<LoginOut>("/api/v1/auth/device-code/redeem", {
      body: { code },
      auth: false,
      org: false,
    }),
  /** A sign-in's second step (Phase 8.4). */
  loginTwoFactor: (challenge: string, code: string) =>
    request<TokenPair>("/api/v1/auth/login/two-factor", {
      body: { challenge, code },
      auth: false,
      org: false,
    }),
  /** The ways to sign in on this server (Phase 8.3). */
  providers: () =>
    request<S["ProvidersOut"]>("/api/v1/auth/providers", { auth: false, org: false }),
  /** Back from Google: the one-minute code, for this browser's tokens. */
  googleExchange: (code: string) =>
    request<LoginOut>("/api/v1/auth/google/exchange", {
      body: { code },
      auth: false,
      org: false,
    }),
  /** Connecting Google, signed in: a ticket for the trip to Google. */
  googleLinkTicket: () =>
    request<S["LinkTicketOut"]>("/api/v1/auth/google/link-ticket", {
      method: "POST",
      org: false,
    }),
  googleDisconnect: () =>
    request<{ message: string }>("/api/v1/auth/google", { method: "DELETE", org: false }),
  /** Getting back in (Phase 7b.6). The same answer whether or not an
   *  account has the address. */
  requestReset: (email: string) =>
    request<{ message: string }>("/api/v1/auth/password-reset", {
      body: { email },
      auth: false,
      org: false,
    }),
  confirmReset: (token: string, newPassword: string) =>
    request<{ message: string }>("/api/v1/auth/password-reset/confirm", {
      body: { token, new_password: newPassword },
      auth: false,
      org: false,
    }),
  /** Every other session ends; this one gets a fresh pair. */
  changePassword: (currentPassword: string, newPassword: string) =>
    request<TokenPair>("/api/v1/auth/password", {
      body: { current_password: currentPassword, new_password: newPassword },
      org: false,
    }),
  sendVerification: () =>
    request<{ message: string }>("/api/v1/auth/verify-email/send", {
      method: "POST",
      org: false,
    }),
  verifyEmail: (token: string) =>
    request<{ message: string }>("/api/v1/auth/verify-email", {
      body: { token },
      auth: false,
      org: false,
    }),
  logout: (refresh_token: string) =>
    request<{ message: string }>("/api/v1/auth/logout", {
      body: { refresh_token },
      auth: false,
      org: false,
    }),
};

export type Member = S["MemberOut"];

export type InvitePreview = S["InvitePreview"];

export const invites = {
  /** What an invite is for. Works signed out: the invitee may have no account. */
  preview: (token: string) =>
    request<InvitePreview>(`/api/v1/invites/${encodeURIComponent(token)}`, {
      auth: false,
      org: false,
    }),
  accept: (token: string) =>
    request<{ org_id: string; org_name: string; role: Member["role"] }>(
      `/api/v1/invites/${encodeURIComponent(token)}/accept`,
      { method: "POST", org: false },
    ),
  create: (orgId: string, email: string, role: Member["role"]) =>
    request<{
      id: string;
      email: string;
      role: Member["role"];
      expires_at: string;
      accept_url: string;
      /** Also emailed to them (Phase 7b.5). */
      emailed?: boolean;
    }>(`/api/v1/orgs/${orgId}/invites`, { body: { email, role }, org: false }),
};

export type AuditEntry = S["AuditEntryOut"];
export type AuditIntegrity = S["AuditIntegrityOut"];

/** What the audit page narrows to (Phase 7b.7): `action` is a prefix of the
 *  action's name, `actor` a user id, `since` an ISO time. */
export type AuditFilters = { action?: string; actor?: string; since?: string };

function auditQuery(f: AuditFilters, cursor?: string | null): string {
  const q = new URLSearchParams();
  if (f.action) q.set("action", f.action);
  if (f.actor) q.set("actor", f.actor);
  if (f.since) q.set("since", f.since);
  if (cursor) q.set("cursor", cursor);
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

export const orgs = {
  list: () => collectAll((c) => request<Page<Org>>(paged("/api/v1/orgs", c, 200), { org: false })),
  members: (orgId: string, cursor?: string | null) =>
    request<Page<Member>>(paged(`/api/v1/orgs/${orgId}/members`, cursor), { org: false }),
  /** What removing someone would hand on, close or revoke (Phase 7b.4). */
  leaving: (orgId: string, userId: string) =>
    request<S["LeavingOut"]>(`/api/v1/orgs/${orgId}/members/${userId}/leaving`, { org: false }),
  deactivate: (orgId: string, userId: string, transferTo?: string | null) =>
    request<Member>(`/api/v1/orgs/${orgId}/members/${userId}:deactivate`, {
      body: { transfer_to: transferTo ?? null },
      org: false,
    }),
  reactivate: (orgId: string, userId: string) =>
    request<Member>(`/api/v1/orgs/${orgId}/members/${userId}:reactivate`, {
      method: "POST",
      org: false,
    }),
  /** The audit log, newest first (Phase 7b.7): `action` is a prefix. */
  audit: (orgId: string, filters: AuditFilters, cursor?: string | null) =>
    request<Page<AuditEntry>>(`/api/v1/orgs/${orgId}/audit-log${auditQuery(filters, cursor)}`, {
      org: false,
    }),
  /** The owner's integrity check of the hash chain. */
  auditIntegrity: (orgId: string) =>
    request<S["AuditIntegrityOut"]>(`/api/v1/orgs/${orgId}/audit-log/integrity`, { org: false }),
  /** The owner's CSV export, as a file to save. */
  auditCsv: async (orgId: string, filters: AuditFilters): Promise<Blob> => {
    const url = `${API_URL}/api/v1/orgs/${orgId}/audit-log.csv${auditQuery(filters)}`;
    const res = await authedFetch(url, (token) => ({
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      cache: "no-store",
    }));
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, (text ? JSON.parse(text) : null) as ApiErrorBody);
    }
    return res.blob();
  },
  /** Invites nobody has used or withdrawn. */
  invites: (orgId: string) =>
    request<S["InviteOut"][]>(`/api/v1/orgs/${orgId}/invites`, { org: false }),
  /** The organisation's name and switches (Phase 7b.8): admins read, the owner sets. */
  settings: (orgId: string) =>
    request<OrgSettings>(`/api/v1/orgs/${orgId}/settings`, { org: false }),
  /** The privacy notice (Phase 8.7): admins read it, the owner edits it. */
  notice: (orgId: string) => request<NoticeAdmin>(`/api/v1/orgs/${orgId}/notice`, { org: false }),
  saveNotice: (orgId: string, body: string, askAgain: boolean) =>
    request<NoticeAdmin>(`/api/v1/orgs/${orgId}/notice`, {
      method: "PUT",
      body: { body, ask_again: askAgain },
      org: false,
    }),
  /** How long conversations are kept (Phase 8.10): admins set it. */
  setRetention: (orgId: string, days: number) =>
    request<{ days: number; below_floor: boolean }>(`/api/v1/orgs/${orgId}/retention`, {
      method: "PUT",
      body: { days },
      org: false,
    }),
  /** Erase one person's data for good: the owner's, email typed to confirm. */
  erasePerson: (orgId: string, userId: string, confirmEmail: string) =>
    request<S["ErasedOut"]>(`/api/v1/orgs/${orgId}/members/${userId}:erase`, {
      body: { confirm_email: confirmEmail },
      org: false,
    }),
  holds: (orgId: string) => request<LegalHoldItem[]>(`/api/v1/orgs/${orgId}/holds`, { org: false }),
  createHold: (
    orgId: string,
    body: { user_id: string | null; conversation_id: string | null; reason: string },
  ) => request<LegalHoldItem>(`/api/v1/orgs/${orgId}/holds`, { body, org: false }),
  releaseHold: (orgId: string, holdId: string) =>
    request<LegalHoldItem>(`/api/v1/orgs/${orgId}/holds:release`, {
      body: { hold_id: holdId },
      org: false,
    }),
  erasures: (orgId: string) => request<Erasure[]>(`/api/v1/orgs/${orgId}/erasures`, { org: false }),
  noticeRevisions: (orgId: string) =>
    request<NoticeRevision[]>(`/api/v1/orgs/${orgId}/notice/revisions`, { org: false }),
  updateSettings: (
    orgId: string,
    body: S["OrgSettingsUpdate"] & { builder_monthly_usd?: number | null },
  ) =>
    request<OrgSettings>(`/api/v1/orgs/${orgId}/settings`, {
      method: "PATCH",
      body,
      org: false,
    }),
  /** Groups (Phase 8.5): names for anyone choosing an audience; the rest
   *  is admins'. */
  groups: (orgId: string) => request<Group[]>(`/api/v1/orgs/${orgId}/groups`, { org: false }),
  createGroup: (orgId: string, name: string, description = "") =>
    request<Group>(`/api/v1/orgs/${orgId}/groups`, { body: { name, description }, org: false }),
  deleteGroup: (orgId: string, groupId: string) =>
    request<{ message: string }>(`/api/v1/orgs/${orgId}/groups/${groupId}`, {
      method: "DELETE",
      org: false,
    }),
  groupMembers: (orgId: string, groupId: string) =>
    request<GroupMember[]>(`/api/v1/orgs/${orgId}/groups/${groupId}/members`, { org: false }),
  addGroupMember: (orgId: string, groupId: string, userId: string) =>
    request<GroupMember[]>(`/api/v1/orgs/${orgId}/groups/${groupId}/members`, {
      body: { user_id: userId },
      org: false,
    }),
  removeGroupMember: (orgId: string, groupId: string, userId: string) =>
    request<GroupMember[]>(`/api/v1/orgs/${orgId}/groups/${groupId}/members/${userId}`, {
      method: "DELETE",
      org: false,
    }),
  /** How this server sends email: read-only, from its environment (7b.5). */
  mailStatus: (orgId: string) =>
    request<S["MailStatusOut"]>(`/api/v1/orgs/${orgId}/mail`, { org: false }),
  /** A test message to the admin asking, and nobody else. */
  mailTest: (orgId: string) =>
    request<S["MailTestOut"]>(`/api/v1/orgs/${orgId}/mail:test`, { method: "POST", org: false }),
  revokeInvite: (orgId: string, inviteId: string) =>
    request<{ message: string }>(`/api/v1/orgs/${orgId}/invites/${inviteId}`, {
      method: "DELETE",
      org: false,
    }),
};

/** The canvas's wiring rules, served from the validator's own constants so
 *  the frontend can't hold a stale copy of the edge allow-list. */
export type GraphSchema = {
  node_types: string[];
  allowed_edges: [string, string][];
  singleton_types: string[];
};

export const meta = {
  configSchema: () =>
    request<{
      schema: unknown;
      default: AssistantConfig;
      allowed_models: string[];
      /** Which model each alias ("sonnet") runs as today (Phase 7a.6). */
      model_aliases?: Record<string, string>;
      /** RAG_OFFLINE=1: web search is off on this instance. */
      offline?: boolean;
      /** AI helpers call the real model and bill for it (else a free stand-in). */
      real_model?: boolean;
    }>("/api/v1/meta/config-schema", { org: false }),
  graphSchema: () => request<GraphSchema>("/api/v1/meta/graph-schema", { org: false }),
  /** The sample assistants shipped with this server. */
  samples: () => request<SampleSummary[]>("/api/v1/meta/samples", { org: false }),
};

export const assistants = {
  list: (cursor?: string | null) => request<Page<Assistant>>(paged("/api/v1/assistants", cursor)),
  create: (name: string, description = "") =>
    request<AssistantDetail>("/api/v1/assistants", { body: { name, description } }),
  /** A new draft from a shipped sample: its graph, documents and eval suite. */
  fromSample: (sampleId: string, name?: string) =>
    request<AssistantDetail>("/api/v1/assistants:from-sample", {
      body: { sample_id: sampleId, name: name ?? null },
    }),
  get: (id: string) => request<AssistantDetail>(`/api/v1/assistants/${id}`),
  /** The draft listing (Phase 8.5): reaches Chat with the next approved version. */
  listing: (id: string) => request<Listing>(`/api/v1/assistants/${id}/listing`),
  /** What people said about its answers (Phase 8.8): never who asked. */
  feedback: (id: string) => request<FeedbackSummary>(`/api/v1/assistants/${id}/feedback`),
  saveListing: (id: string, listing: Listing) =>
    request<Listing>(`/api/v1/assistants/${id}/listing`, { method: "PUT", body: listing }),
  /** An admin's Official mark (Phase 8.5). */
  setOfficial: (id: string, official: boolean) =>
    request<Assistant>(`/api/v1/assistants/${id}:official`, { body: { official } }),
  /** Name and description only: the status changes through review and
   *  pausing, never an edit (Phase 7b.1). */
  patch: (id: string, body: Partial<Pick<Assistant, "name" | "description">>) =>
    request<AssistantDetail>(`/api/v1/assistants/${id}`, { method: "PATCH", body }),
  /** Deletes the assistant and everything it owns. Usage history is kept. */
  remove: (id: string) =>
    request<{ message: string }>(`/api/v1/assistants/${id}`, { method: "DELETE" }),
  putDraftGraph: (id: string, graph: Graph) =>
    request<{ graph: Graph; validation: ValidationResult; config: AssistantConfig | null }>(
      `/api/v1/assistants/${id}/draft-graph`,
      { method: "PUT", body: graph },
    ),
  putDraftConfig: (id: string, config: AssistantConfig) =>
    request<{ config: AssistantConfig; graph: Graph; validation: ValidationResult }>(
      `/api/v1/assistants/${id}/draft-config`,
      { method: "PUT", body: config },
    ),
  /** Freeze the draft as a new version and put it up for review. It answers
   *  in Chat once an admin approves it (Phase 7b.1). */
  submit: (id: string, note = "") =>
    request<AssistantVersion & { graph: Graph; config: AssistantConfig }>(
      `/api/v1/assistants/${id}/versions`,
      { body: { note } },
    ),
  approve: (id: string, number: number, note = "") =>
    request<AssistantVersion>(`/api/v1/assistants/${id}/versions/${number}/approve`, {
      body: { note },
    }),
  /** `note` is required: it is what the builder reads. */
  reject: (id: string, number: number, note: string) =>
    request<AssistantVersion>(`/api/v1/assistants/${id}/versions/${number}/reject`, {
      body: { note },
    }),
  withdraw: (id: string, number: number) =>
    request<AssistantVersion>(`/api/v1/assistants/${id}/versions/${number}/withdraw`, {
      method: "POST",
    }),
  /** Roll back to an earlier approved version. */
  makeLive: (id: string, number: number) =>
    request<AssistantVersion>(`/api/v1/assistants/${id}/versions/${number}/make-live`, {
      method: "POST",
    }),
  /** Pause: nothing answers, previews included, until an admin reopens it. */
  archive: (id: string, reason: string) =>
    request<AssistantDetail>(`/api/v1/assistants/${id}/archive`, { body: { reason } }),
  unarchive: (id: string) =>
    request<AssistantDetail>(`/api/v1/assistants/${id}/unarchive`, { method: "POST" }),
  versions: (id: string, cursor?: string | null) =>
    request<Page<AssistantVersion>>(paged(`/api/v1/assistants/${id}/versions`, cursor)),
  /** One published version, with its graph and config. */
  version: (id: string, number: number) =>
    request<
      AssistantVersion & {
        graph: Graph;
        config: AssistantConfig;
        manifest?: S["VersionDetail"]["manifest"];
      }
    >(`/api/v1/assistants/${id}/versions/${number}`),
  /** What has changed since a version was submitted (Phase 7b.1). */
  drift: (id: string, number: number) =>
    request<S["VersionDrift"]>(`/api/v1/assistants/${id}/versions/${number}/drift`),
  /** What to approve on (Phase 10.4): scorecard, safety, cost forecast. */
  evidence: (id: string, number: number, perMonth = 500) =>
    request<VersionEvidence>(
      `/api/v1/assistants/${id}/versions/${number}/evidence?per_month=${perMonth}`,
    ),
  /** Co-editors besides its creator (Phase 7b.4). */
  editors: (id: string) => request<S["EditorOut"][]>(`/api/v1/assistants/${id}/editors`),
  addEditor: (id: string, userId: string) =>
    request<S["EditorOut"][]>(`/api/v1/assistants/${id}/editors`, { body: { user_id: userId } }),
  removeEditor: (id: string, userId: string) =>
    request<{ message: string }>(`/api/v1/assistants/${id}/editors/${userId}`, {
      method: "DELETE",
    }),
  /** Config + graph differences between two published versions. */
  diff: (id: string, a: number, b: number) =>
    request<VersionDiff>(`/api/v1/assistants/${id}/versions/diff?a=${a}&b=${b}`),
};

export const evals = {
  suites: (assistantId: string) =>
    request<S["EvalSuitesOut"]>(`/api/v1/assistants/${assistantId}/eval-suites`),
  createSuite: (assistantId: string, name: string) =>
    request<EvalSuiteDetail>(`/api/v1/assistants/${assistantId}/eval-suites`, { body: { name } }),
  suite: (suiteId: string) => request<EvalSuiteDetail>(`/api/v1/eval-suites/${suiteId}`),
  updateSuite: (suiteId: string, body: { name?: string; config?: EvalSuiteConfig }) =>
    request<EvalSuiteDetail>(`/api/v1/eval-suites/${suiteId}`, { method: "PATCH", body }),
  removeSuite: (suiteId: string) =>
    request<{ message: string }>(`/api/v1/eval-suites/${suiteId}`, { method: "DELETE" }),
  /** Add cases, or (`replace`) swap every case for these. */
  addCases: (suiteId: string, cases: EvalCaseDraft[], mode: "append" | "replace" = "append") =>
    request<EvalSuiteDetail>(`/api/v1/eval-suites/${suiteId}/cases:bulk`, {
      body: { cases, mode },
    }),
  updateCase: (suiteId: string, caseId: string, body: EvalCaseDraft) =>
    request<EvalCase>(`/api/v1/eval-suites/${suiteId}/cases/${caseId}`, { method: "PUT", body }),
  removeCase: (suiteId: string, caseId: string) =>
    request<{ message: string }>(`/api/v1/eval-suites/${suiteId}/cases/${caseId}`, {
      method: "DELETE",
    }),
  /** Queue a run on a published version; `null` runs the draft. */
  start: (suiteId: string, versionId: string | null) =>
    request<EvalRun>(`/api/v1/eval-suites/${suiteId}/runs`, {
      body: { assistant_version_id: versionId },
    }),
  runs: (suiteId: string) => request<{ runs: EvalRun[] }>(`/api/v1/eval-suites/${suiteId}/runs`),
  run: (runId: string) => request<EvalRunDetail>(`/api/v1/eval-runs/${runId}`),
  cancel: (runId: string) =>
    request<EvalRun>(`/api/v1/eval-runs/${runId}:cancel`, { method: "POST" }),
};

/** Chat (`live`) or the Studio's test chat (`preview`; Phase 7b.1). A
 *  preview runs the draft, or `version` when one is given. */
export type ChatMode = { preview: boolean; version?: number };

export const conversations = {
  /** Your own conversations; `everyone` (admins) lists everyone's, with
   *  other people's titles left out (Phase 7b.2). */
  list: (assistantId: string, cursor?: string | null, mode?: ChatMode, everyone = false) => {
    let url = paged(`/api/v1/assistants/${assistantId}/conversations`, cursor);
    const add = (q: string) => (url = `${url}${url.includes("?") ? "&" : "?"}${q}`);
    if (mode?.preview) add("mode=preview");
    if (everyone) add("everyone=true");
    return request<Page<Conversation>>(url);
  },
  create: (assistantId: string, title?: string, mode?: ChatMode) =>
    request<Conversation>(`/api/v1/assistants/${assistantId}/conversations`, {
      body: mode?.preview ? { title, mode: "preview", version: mode.version ?? null } : { title },
    }),
  /** The conversation plus its most recent messages (not the whole history:
   *  `messages_next_cursor` pages further back via `olderMessages`). */
  /** The conversation plus its most recent messages, and the turn running
   *  in it (`turn_id`), if any. */
  get: (id: string, orgId?: string) =>
    request<
      Conversation & {
        messages: ChatMessage[];
        messages_next_cursor: string | null;
        turn_id?: string | null;
        /** Phase 8.8: its starter's thumbs, by answer, and "solved". */
        feedback?: Record<string, { rating?: "up" | "down" | null; comment?: string }>;
        solved?: boolean | null;
      }
    >(`/api/v1/conversations/${id}`, { orgId }),
  /** Runs (one per answered turn), newest first; `messageId` narrows to the
   *  run that produced one message. */
  runs: (id: string, messageId?: string) =>
    request<Page<Run>>(
      paged(`/api/v1/conversations/${id}/runs`) +
        (messageId ? `?message_id=${encodeURIComponent(messageId)}` : ""),
    ),
  /** Older history, newest page first; each page's items are oldest-first,
   *  so a page can be prepended as-is. */
  olderMessages: (id: string, cursor: string) =>
    request<Page<ChatMessage>>(paged(`/api/v1/conversations/${id}/messages`, cursor)),
  rename: (id: string, title: string) =>
    request<Conversation>(`/api/v1/conversations/${id}`, { method: "PATCH", body: { title } }),
  /** Put back a conversation removed from your history (Phase 8.8). */
  restore: (id: string) =>
    request<Conversation>(`/api/v1/conversations/${id}:restore`, { method: "POST" }),
  /** Your thumbs on one answer, with an optional comment; null takes it back. */
  rate: (id: string, messageId: string, rating: "up" | "down" | null, comment = "") =>
    request<{ rating?: "up" | "down" | null; comment?: string }>(
      `/api/v1/conversations/${id}/messages/${messageId}/feedback`,
      { method: "PUT", body: { rating, comment } },
    ),
  /** "Did this solve it?", once per conversation. */
  solve: (id: string, solved: boolean) =>
    request<{ solved: boolean }>(`/api/v1/conversations/${id}/outcome`, {
      method: "PUT",
      body: { solved },
    }),
  /** Remove from your history (Phase 8.8 wording; the API's DELETE): out of
   *  your lists and search, kept for the company, admins can still open it,
   *  and `restore` puts it back. */
  archive: (id: string) =>
    request<{ message: string }>(`/api/v1/conversations/${id}`, { method: "DELETE" }),
  /** Ask the running turn to stop. Its stream then ends with a normal
   *  `done`, keeping whatever text had already arrived. The only way to stop
   *  one: leaving the page does not (QOS-01). */
  interrupt: (id: string) =>
    request<{ message: string }>(`/api/v1/conversations/${id}:interrupt`, { method: "POST" }),
};

export type Run = S["RunOut"];
/** A run step by step, with the canvas nodes it touched (task 5.9). */
export type RunTrace = S["RunDetail"];
export type TraceStep = S["TraceStep"];

export const runs = {
  trace: (conversationId: string, runId: string) =>
    request<RunTrace>(`/api/v1/conversations/${conversationId}/runs/${runId}`),
};

export type DataSourceStatus = S["DataSourceStatus"];

export type DataSource = S["DataSourceSummary"];
/** Read-only conversation links (Phase 9.4). */
export type ShareCreated = S["ShareCreated"];
export type Share = S["ShareOut"];
export type SharedConversation = Omit<S["SharedConversationOut"], "messages"> & {
  messages: ChatMessage[];
};

export const shares = {
  create: (conversationId: string) =>
    request<ShareCreated>(`/api/v1/conversations/${conversationId}/shares`, { method: "POST" }),
  list: (conversationId: string) =>
    request<Share[]>(`/api/v1/conversations/${conversationId}/shares`),
  revoke: (conversationId: string, shareId: string) =>
    request<Share>(`/api/v1/conversations/${conversationId}/shares/${shareId}`, {
      method: "DELETE",
    }),
  open: (token: string) =>
    request<SharedConversation>(`/api/v1/shared/${encodeURIComponent(token)}`, { org: false }),
};

/** Hand-offs to a person (Phase 9.5). Hand-written until the shared types
 *  are regenerated. */
export type Handoff = {
  id: string;
  conversation_id: string;
  status: "sent" | "failed";
  channel: "email" | "action";
  ticket_ref: string | null;
  summary: string;
  created_at: string;
};
export type HandoffList = {
  available: boolean;
  contact_email: string;
  opens_ticket: boolean;
  items: Handoff[];
};
export const handoffs = {
  list: (conversationId: string) =>
    request<HandoffList>(`/api/v1/conversations/${conversationId}/handoffs`),
  create: (conversationId: string, note: string) =>
    request<Handoff>(`/api/v1/conversations/${conversationId}/handoff`, { body: { note } }),
};

export type ContentUrl = S["ContentUrl"];

export type ApprovalRisk = S["ApprovalRisk"];
export type ApprovalStatus = S["ApprovalStatus"];

export type Approval = S["ApprovalOut"];
/** A request in someone's queue, with who asked and where (Phase 9.1). */
export type DecidableApproval = S["DecidableApprovalOut"];
export type ActionField = S["ActionFieldOut"];
export type ApprovalSummary = S["ApprovalSummaryOut"];
export type Approvers = S["ApproversOut"];
export type Delegation = S["DelegationOut"];
export type ActionCheck = S["ActionCheckOut"];
/** A named SQL statement on a database node (Phase 9.1). */
export type DeclaredAction = S["DeclaredAction-Input"];
export type ActionParam = S["ActionParam-Input"];
/** A declared call to a company system (Phase 9.3). */
export type HttpAction = S["HttpAction-Input"];
export type HttpConnection = S["HttpConnectionOut"];
/** An internal destination, as an owner writes it (Phase 9.3). */
export type InternalDestination = S["DestinationOut"];

export type DbEngine = S["DbEngine"];

export type DbPermissions = S["DbPermissions-Output"];

export type DbConnection = S["DbConnectionSummary"];

export type DbTestResult = S["DbTestResult"];
export type DbSchema = S["DbSchemaOut"];
export type DbColumn = DbSchema["schemas"][number]["tables"][number]["columns"][number];

export const dbConnections = {
  list: (assistantId: string) =>
    collectAll((c) =>
      request<Page<DbConnection>>(
        paged(`/api/v1/assistants/${assistantId}/db-connections`, c, 200),
      ),
    ),
  create: (assistantId: string, body: Record<string, unknown>) =>
    request<DbConnection>(`/api/v1/assistants/${assistantId}/db-connections`, { body }),
  update: (assistantId: string, id: string, body: Record<string, unknown>) =>
    request<DbConnection>(`/api/v1/assistants/${assistantId}/db-connections/${id}`, {
      method: "PATCH",
      body,
    }),
  remove: (assistantId: string, id: string) =>
    request<{ message: string }>(`/api/v1/assistants/${assistantId}/db-connections/${id}`, {
      method: "DELETE",
    }),
  test: (assistantId: string, id: string) =>
    request<DbTestResult>(`/api/v1/assistants/${assistantId}/db-connections/${id}:test`, {
      method: "POST",
    }),
  refreshSchema: (assistantId: string, id: string) =>
    request<DbSchema>(`/api/v1/assistants/${assistantId}/db-connections/${id}:refresh-schema`, {
      method: "POST",
    }),
  schema: (assistantId: string, id: string) =>
    request<DbSchema>(`/api/v1/assistants/${assistantId}/db-connections/${id}/schema`),
};

export type McpServer = S["McpServerSummary"];
export type McpTool = McpServer["tools"][number];
export type McpRunnerStatus = S["McpRunnerStatus"];
export type McpCheckResult = S["McpCheckResult"];
export type McpPreset = S["McpPresetOut"];
export type MemoryFile = S["MemoryFileOut"];
export type SandboxLimits = S["SandboxLimits"];

/** What the memory tool keeps about the signed-in user (task 5.2). */
export const memories = {
  list: (assistantId: string) =>
    request<MemoryFile[]>(`/api/v1/assistants/${assistantId}/memories`),
  clear: (assistantId: string) =>
    request<{ deleted: number }>(`/api/v1/assistants/${assistantId}/memories`, {
      method: "DELETE",
    }),
};

export type PromptSuggestion = S["PromptSuggestion"];

/** AI help for builders (Phase 5): suggestions only, nothing is saved. */
export const assist = {
  /** Draft a system prompt and rules from what the assistant is for (5.5). */
  generatePrompt: (
    assistantId: string,
    body: { description: string; current_prompt?: string | null },
  ) => request<PromptSuggestion>(`/api/v1/assistants/${assistantId}/prompt:generate`, { body }),
  /** A whole starter pipeline for this assistant, from what it has (5.6). */
  recommendPipeline: (assistantId: string, body: { description: string }) =>
    request<PipelineSuggestion>(`/api/v1/assistants/${assistantId}/pipeline:recommend`, { body }),
  /** The same before the assistant exists (the guided setup). */
  recommendStarter: (body: { description: string; name?: string }) =>
    request<PipelineSuggestion>("/api/v1/pipeline:recommend", { body }),
};

export type BudgetStatus = S["BudgetStatusOut"];
/** Builder testing this month (Phase 9.7): previews and eval runs. */
export type BuilderSpend = {
  spent_usd: number;
  limit_usd: number | null;
  ratio: number | null;
  state: "ok" | "warning" | "exceeded";
  resets_at: string;
};
export type Budgets = S["BudgetsOut"] & { builder?: BuilderSpend | null };
export type BudgetLimits = { daily_usd: number | null; monthly_usd: number | null };
export type UsageGroupBy = "assistant" | "model" | "conversation";
export type UsageRow = S["UsageRollupRow"];

/** Spend caps (task 5.7): anyone in the org reads them, admins set them. */
export const budgets = {
  org: (orgId: string) => request<Budgets>(`/api/v1/orgs/${orgId}/budgets`),
  setOrg: (orgId: string, limits: BudgetLimits) =>
    request<Budgets>(`/api/v1/orgs/${orgId}/budgets`, { method: "PUT", body: limits }),
  /** The org's budgets and this assistant's own. */
  assistant: (assistantId: string) => request<Budgets>(`/api/v1/assistants/${assistantId}/budget`),
  setAssistant: (assistantId: string, limits: BudgetLimits) =>
    request<Budgets>(`/api/v1/assistants/${assistantId}/budget`, { method: "PUT", body: limits }),
  /** Per-person limits and everyone's spend (Phase 8.9): admins only. */
  people: (orgId: string) =>
    request<PeopleBudgets>(`/api/v1/orgs/${orgId}/budgets/people`, { org: false }),
  setPersonDefault: (orgId: string, role: "employee" | "builder", limits: BudgetLimits) =>
    request<PeopleBudgets>(`/api/v1/orgs/${orgId}/budgets/people`, {
      method: "PUT",
      body: { role, ...limits },
      org: false,
    }),
  /** One person's own limit; both null: back to their role's. */
  setPersonOverride: (orgId: string, userId: string, limits: BudgetLimits) =>
    request<PeopleBudgets>(`/api/v1/orgs/${orgId}/members/${userId}/budget`, {
      method: "PUT",
      body: limits,
      org: false,
    }),
};

export type PeopleBudgets = S["PeopleBudgetsOut"];
export type AdminAlertItem = S["AlertOut"];

/** What stopped Chat for everyone (Phase 8.9), for the admins. */
export const alerts = {
  list: (orgId: string, openOnly: boolean) =>
    request<AdminAlertItem[]>(`/api/v1/orgs/${orgId}/alerts${openOnly ? "?open=true" : ""}`, {
      org: false,
    }),
  resolve: (orgId: string, kind: string, subject: string) =>
    request<{ resolved: number }>(`/api/v1/orgs/${orgId}/alerts:resolve`, {
      body: { kind, subject },
      org: false,
    }),
};

export const usage = {
  rollup: (
    orgId: string,
    q: { group_by: UsageGroupBy; from?: string; to?: string; limit?: number },
  ) => {
    const params = new URLSearchParams({ group_by: q.group_by });
    if (q.from) params.set("from", q.from);
    if (q.to) params.set("to", q.to);
    if (q.limit) params.set("limit", String(q.limit));
    return request<{ group_by: UsageGroupBy; rows: UsageRow[] }>(
      `/api/v1/orgs/${orgId}/usage?${params}`,
    );
  },
};

// ── Analytics (Phase 10.1) and traffic on the canvas (Phase 10.2) ──
// Hand-written until the generated types catch up (the lead reconciles).

export type AnalyticsFigures = {
  conversations: number;
  people: number;
  resolved: number;
  not_resolved: number;
  no_verdict: number;
  with_errors: number;
  spend_usd: number;
  resolution_rate: number | null;
  cost_per_resolved_usd: number | null;
};
export type AnalyticsDay = {
  date: string;
  conversations: number;
  resolved: number;
  spend_usd: number;
};
export type AssistantAnalytics = {
  assistant_id: string;
  days: number;
  start: string;
  end: string;
  current: AnalyticsFigures;
  previous: AnalyticsFigures;
  series: AnalyticsDay[];
};
export type OrgAnalyticsRow = {
  assistant_id: string;
  name: string;
  status: string;
  current: AnalyticsFigures;
  previous: AnalyticsFigures;
};
export type OrgAnalytics = {
  days: number;
  start: string;
  end: string;
  assistants: OrgAnalyticsRow[];
  total: AnalyticsFigures;
  previous_total: AnalyticsFigures;
};
export type NodeTraffic = { calls: number; errors: number; approvals: number };
export type AssistantTraffic = {
  assistant_id: string;
  hours: number;
  since: string;
  nodes: Record<string, NodeTraffic>;
  total: {
    turns: number;
    turn_errors: number;
    calls: number;
    errors: number;
    approvals: number;
    unplaced: number;
  };
};

export const analytics = {
  /** How its live conversations went, against the period before. */
  assistant: (assistantId: string, days = 30) =>
    request<AssistantAnalytics>(`/api/v1/assistants/${assistantId}/analytics?days=${days}`),
  /** The managers' report: every assistant (admins only). */
  org: (orgId: string, days = 30) =>
    request<OrgAnalytics>(`/api/v1/orgs/${orgId}/analytics?days=${days}`, { org: false }),
  /** The managers' report as a CSV file to save (the download is recorded). */
  orgCsv: async (orgId: string, days = 30): Promise<Blob> => {
    const url = `${API_URL}/api/v1/orgs/${orgId}/analytics.csv?days=${days}`;
    const res = await authedFetch(url, (token) => ({
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      cache: "no-store",
    }));
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, (text ? JSON.parse(text) : null) as ApiErrorBody);
    }
    return res.blob();
  },
  /** Live calls, errors and approvals per canvas station, in the last hours. */
  traffic: (assistantId: string, hours = 24) =>
    request<AssistantTraffic>(`/api/v1/assistants/${assistantId}/traffic?hours=${hours}`),
};

export type PipelineSuggestion = Omit<S["PipelineSuggestion"], "config" | "graph"> & {
  config: AssistantConfig;
  graph: Graph;
};

export const mcpServers = {
  list: (assistantId: string) =>
    collectAll((c) =>
      request<Page<McpServer>>(paged(`/api/v1/assistants/${assistantId}/mcp-servers`, c, 200)),
    ),
  create: (assistantId: string, body: Record<string, unknown>) =>
    request<McpServer>(`/api/v1/assistants/${assistantId}/mcp-servers`, { body }),
  update: (assistantId: string, id: string, body: Record<string, unknown>) =>
    request<McpServer>(`/api/v1/assistants/${assistantId}/mcp-servers/${id}`, {
      method: "PATCH",
      body,
    }),
  remove: (assistantId: string, id: string) =>
    request<{ message: string }>(`/api/v1/assistants/${assistantId}/mcp-servers/${id}`, {
      method: "DELETE",
    }),
  /** Connect and complete the MCP handshake (starts a local command). */
  health: (assistantId: string, id: string) =>
    request<McpCheckResult>(`/api/v1/assistants/${assistantId}/mcp-servers/${id}:health`, {
      method: "POST",
    }),
  /** Connect, list the server's tools, and store them. */
  discover: (assistantId: string, id: string) =>
    request<McpCheckResult>(`/api/v1/assistants/${assistantId}/mcp-servers/${id}:discover-tools`, {
      method: "POST",
    }),
  /** A short catalog of well-known servers (task 4.8). */
  presets: () => request<McpPreset[]>(`/api/v1/mcp-presets`),
  /** Where local-command servers run, and how well they are contained. */
  runner: () => request<McpRunnerStatus>(`/api/v1/mcp-runner`),
};

export const approvals = {
  /** Pending approvals for a conversation. Needed because the SSE event only
   *  reaches whoever was watching the stream — a reload must not orphan a
   *  decision that is genuinely still waiting. */
  listPending: (conversationId: string) =>
    collectAll((c) =>
      request<Page<Approval>>(paged(`/api/v1/conversations/${conversationId}/approvals`, c, 200)),
    ),
  /** Every request in a conversation, in any state (Phase 9.1): each card
   *  says where its request stands. */
  listAll: (conversationId: string) =>
    collectAll((c) =>
      request<Page<Approval>>(
        `${paged(`/api/v1/conversations/${conversationId}/approvals`, c, 200)}&all=true`,
      ),
    ),
  /** Decide a request, naming the hash of what was shown: refused if it
   *  changed since. The asker may send their own values for an action. */
  resolve: (
    approvalId: string,
    body: {
      decision: "approved" | "denied";
      input_sha256: string | null | undefined;
      note?: string;
      params?: Record<string, unknown>;
    },
  ) => request<Approval>(`/api/v1/approvals/${approvalId}:resolve`, { body }),
  /** The asker takes back a request still waiting. */
  withdraw: (approvalId: string) =>
    request<Approval>(`/api/v1/approvals/${approvalId}:withdraw`, { method: "POST" }),
  /** What waits for me to decide: all of it for an admin, an assistant's
   *  for its approvers and their stand-ins. */
  decidable: () => request<DecidableApproval[]>("/api/v1/approvals/decidable"),
  /** How many wait for me to decide, and how many of mine are open (9.2). */
  summary: () => request<ApprovalSummary>("/api/v1/approvals/summary"),
  /** My requests, newest first (Phase 9.2). */
  mine: (cursor?: string | null) =>
    request<Page<DecidableApproval>>(paged("/api/v1/approvals/mine", cursor)),
  /** The requests I decided, newest first. */
  decided: (cursor?: string | null) =>
    request<Page<DecidableApproval>>(paged("/api/v1/approvals/decided", cursor)),
  /** Every request in the organisation (admins), by state and assistant. */
  inOrg: (
    orgId: string,
    filters: { status?: string; assistant_id?: string },
    cursor?: string | null,
  ) => {
    const q = new URLSearchParams();
    if (filters.status) q.set("status", filters.status);
    if (filters.assistant_id) q.set("assistant_id", filters.assistant_id);
    const base = paged(`/api/v1/orgs/${orgId}/approvals`, cursor);
    const extra = q.toString();
    return request<Page<DecidableApproval>>(
      extra ? `${base}${base.includes("?") ? "&" : "?"}${extra}` : base,
      { org: false },
    );
  },
  /** One request, where an email links. */
  one: (approvalId: string) => request<DecidableApproval>(`/api/v1/approvals/${approvalId}`),
  approvers: (assistantId: string) =>
    request<Approvers>(`/api/v1/assistants/${assistantId}/approvers`),
  setApprovers: (assistantId: string, body: { group_ids: string[]; user_ids: string[] }) =>
    request<Approvers>(`/api/v1/assistants/${assistantId}/approvers`, { method: "PUT", body }),
  delegations: () => request<Delegation[]>("/api/v1/approvals/delegations"),
  addDelegation: (body: { email: string; until: string }) =>
    request<Delegation[]>("/api/v1/approvals/delegations", { body }),
  endDelegation: (id: string) =>
    request<void>(`/api/v1/approvals/delegations/${id}`, { method: "DELETE" }),
  /** Check an action before it goes on the canvas. */
  checkAction: (assistantId: string, connectionId: string, action: unknown) =>
    request<ActionCheck>(`/api/v1/assistants/${assistantId}/actions:check`, {
      body: { connection_id: connectionId, action },
    }),
  /** The same for an HTTP action, whose connection is in the action. */
  checkHttpAction: (assistantId: string, action: HttpAction) =>
    request<ActionCheck>(`/api/v1/assistants/${assistantId}/actions:check`, {
      body: { connection_id: action.connection_id, action, kind: "http" },
    }),
};

/** HTTP connections (Phase 9.3): a company system an assistant calls. The
 *  header values are write-only: what comes back is their names. */
export const httpConnections = {
  list: (assistantId: string) =>
    request<HttpConnection[]>(`/api/v1/assistants/${assistantId}/http-connections`),
  create: (assistantId: string, body: S["HttpConnectionCreate"]) =>
    request<HttpConnection>(`/api/v1/assistants/${assistantId}/http-connections`, { body }),
  update: (assistantId: string, id: string, body: S["HttpConnectionUpdate"]) =>
    request<HttpConnection>(`/api/v1/assistants/${assistantId}/http-connections/${id}`, {
      method: "PATCH",
      body,
    }),
  remove: (assistantId: string, id: string) =>
    request<{ message: string }>(`/api/v1/assistants/${assistantId}/http-connections/${id}`, {
      method: "DELETE",
    }),
};

/** Multipart upload. Deliberately not routed through `request`, which pins
 *  `Content-Type: application/json` — for FormData the browser has to set the
 *  header itself so it can include the multipart boundary. */
/** A screenshot uploaded into a conversation, not yet (or already) sent
 *  with a message (Phase 9.6). */
export type Attachment = {
  id: string;
  conversation_id: string;
  message_id: string | null;
  content_type: string;
  bytes: number;
  created_at: string;
};

/** Screenshots in chat (Phase 9.6). The upload is multipart, so it goes
 *  round `request` like `uploadFile` below. */
export const attachments = {
  upload: async (conversationId: string, file: Blob, name = "screenshot.png") => {
    const form = new FormData();
    form.append("file", file, name);
    const res = await authedFetch(
      `${API_URL}/api/v1/conversations/${conversationId}/attachments`,
      (token) => {
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        return { method: "POST", headers, body: form };
      },
    );
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) throw new ApiError(res.status, data as ApiErrorBody);
    return data as Attachment;
  },
  /** The image itself, read through the API with the signed-in token (not
   *  from object storage directly, which would need the bucket to allow
   *  this site's origin). The caller makes a `blob:` address of it. */
  content: async (conversationId: string, attachmentId: string): Promise<Blob> => {
    const url = `${API_URL}/api/v1/conversations/${conversationId}/attachments/${attachmentId}/content`;
    const res = await authedFetch(url, (token) => ({
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    }));
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, (text ? JSON.parse(text) : null) as ApiErrorBody);
    }
    return res.blob();
  },
};

async function uploadFile(assistantId: string, file: File): Promise<DataSource> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await authedFetch(
    `${API_URL}/api/v1/assistants/${assistantId}/data-sources/upload`,
    (token) => {
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      const org = orgStore.get();
      if (org) headers["X-Org-Id"] = org;
      return { method: "POST", headers, body: form };
    },
  );
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data as ApiErrorBody);
  return data as DataSource;
}

export const dataSources = {
  list: (assistantId: string) =>
    collectAll((c) =>
      request<Page<DataSource>>(paged(`/api/v1/assistants/${assistantId}/data-sources`, c, 200)),
    ),
  addUrl: (assistantId: string, name: string, url: string) =>
    request<DataSource>(`/api/v1/assistants/${assistantId}/data-sources`, {
      body: { type: "url", name, url },
    }),
  addText: (assistantId: string, name: string, text: string) =>
    request<DataSource>(`/api/v1/assistants/${assistantId}/data-sources`, {
      body: { type: "text", name, text },
    }),
  upload: uploadFile,
  reindex: (assistantId: string, dataSourceId: string) =>
    request<DataSource>(`/api/v1/assistants/${assistantId}/data-sources/${dataSourceId}:reindex`, {
      method: "POST",
    }),
  remove: (assistantId: string, dataSourceId: string) =>
    request<{ message: string }>(`/api/v1/assistants/${assistantId}/data-sources/${dataSourceId}`, {
      method: "DELETE",
    }),
  /** Who may read it (Phase 9.4): everyone, or these groups. */
  setAccess: (
    assistantId: string,
    dataSourceId: string,
    access: { everyone: boolean; group_ids: string[] },
  ) =>
    request<DataSource>(`/api/v1/assistants/${assistantId}/data-sources/${dataSourceId}/access`, {
      method: "PUT",
      body: access,
    }),
  /** Index a source the scan held back, for everyone (recorded). */
  confirmEveryone: (assistantId: string, dataSourceId: string) =>
    request<DataSource>(
      `/api/v1/assistants/${assistantId}/data-sources/${dataSourceId}:confirm-everyone`,
      { method: "POST" },
    ),
  /** Resolve a citation to something openable. For files this mints a
   *  short-lived presigned URL — a link click carries no Authorization
   *  header, so an authenticated route can't serve the file to a new tab. */
  contentUrl: (assistantId: string, dataSourceId: string) =>
    request<ContentUrl>(
      `/api/v1/assistants/${assistantId}/data-sources/${dataSourceId}/content-url`,
    ),
};

// ── SSE chat stream ──────────────────────────────────────────

export type ChatEvent =
  // `parent_id`: set on what a subagent produced, naming the delegation call
  // it ran under. Its text is shown with that call, not in the answer.
  | { type: "token"; text: string; parent_id?: string | null }
  | { type: "thinking"; text: string; parent_id?: string | null }
  | {
      type: "tool_call";
      id: string;
      name: string;
      input: Record<string, unknown>;
      parent_id?: string | null;
    }
  | {
      type: "tool_result";
      id: string;
      status: string;
      output: string;
      parent_id?: string | null;
      /** How the call was permitted (task 4.7). */
      permission?: string | null;
    }
  | ({ type: "citation" } & Citation)
  | {
      type: "approval_required";
      approval_id: string;
      tool: string;
      input: Record<string, unknown>;
      risk: ApprovalRisk;
      /** The exact thing that will run — for SQL, the statement itself. */
      rationale: string;
      expires_at: string | null;
    }
  | { type: "usage"; tokens_in: number; tokens_out: number; cost_usd: number }
  /** `retryable`: sending the message again may work (task 5.4). A
   *  `refused` error is the model declining, not a failure. Phase 8.6:
   *  `retry_after_s`, how long to wait (`busy`); `resets_at`, when a used-up
   *  budget starts again, to say in the reader's own time. */
  | {
      type: "error";
      code: string;
      message: string;
      retryable?: boolean;
      retry_after_s?: number | null;
      resets_at?: string | null;
    }
  /** Your message is saved (Phase 8.6): a failure after this doesn't give
   *  the text back, since sending it again would ask twice. */
  | { type: "accepted"; message_id: string }
  | ({ type: "guardrail" } & GuardrailFinding)
  /** The conversation's new title, on its first turn (task 5.2). */
  | { type: "title"; title: string }
  /** A budget is 80% or more used (task 5.7); sent before the turn. */
  | {
      type: "budget";
      scope: "org" | "assistant";
      period: "day" | "month";
      ratio: number;
      message: string;
    }
  | { type: "done"; message_id: string; run_id: string };

/** One server-sent event: its `data:` as a chat event, and its `id:` if it
 *  has one. Null for a frame with no data (a comment, a keep-alive). */
export function parseSseFrame(frame: string): { id: string | null; event: ChatEvent } | null {
  let id: string | null = null;
  let data: string | null = null;
  for (const raw of frame.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.startsWith("id: ")) id = line.slice(4);
    else if (line.startsWith("data: ")) data = (data === null ? "" : data + "\n") + line.slice(6);
  }
  return data === null ? null : { id, event: JSON.parse(data) as ChatEvent };
}

/** How watching a turn ended (Phase 8.6). `ended`: the turn said so (it
 *  finished, or failed), so it is over. Otherwise the connection dropped
 *  partway: watch `turnId` again from `lastId` and nothing is missed or
 *  repeated. */
export type TurnOutcome = { turnId: string | null; ended: boolean; lastId: string | null };

/** Longest a stream may say nothing before it is taken for dropped. The
 *  server sends a keep-alive every 5 s while a turn is quiet, so silence
 *  this long is a connection a phone or a VPN let go of without saying. */
export const STREAM_SILENCE_MS = 20_000;

/** Read a turn's events off a response until it ends, or the connection
 *  drops (which is not an error here: the caller watches again). */
async function readTurn(
  res: Response,
  onEvent: (e: ChatEvent) => void,
  after: string | null = null,
  signal?: AbortSignal,
): Promise<TurnOutcome> {
  if (!res.ok || !res.body) {
    const body = await res.text();
    throw new ApiError(
      res.status,
      body ? JSON.parse(body) : { error: { code: "stream_failed", message: "stream failed" } },
    );
  }
  const outcome: TurnOutcome = {
    turnId: res.headers.get("x-turn-id"),
    ended: false,
    lastId: after,
  };
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let silence: ReturnType<typeof setTimeout> | undefined;
  const listen = () => {
    clearTimeout(silence);
    silence = setTimeout(() => void reader.cancel().catch(() => {}), STREAM_SILENCE_MS);
  };
  listen();
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        // Leaving the conversation is the caller's to see; anything else
        // is the connection going.
        if (signal?.aborted) throw err;
        break;
      }
      if (chunk.done) break;
      listen();
      buf += decoder.decode(chunk.value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const parsed = parseSseFrame(frame);
        if (!parsed) continue;
        // "lost" is the server's own word for a turn whose process
        // stopped, not a place to watch again from.
        if (parsed.id && parsed.id !== "lost") outcome.lastId = parsed.id;
        if (parsed.event.type === "done" || parsed.event.type === "error") outcome.ended = true;
        onEvent(parsed.event);
      }
    }
  } finally {
    clearTimeout(silence);
  }
  return outcome;
}

/** Send a message: starts a turn on the server and watches it. Leaving
 *  (aborting `signal`) stops watching, not the turn (QOS-01). */
export async function streamMessage(
  conversationId: string,
  text: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
  /** Screenshots uploaded into the conversation (Phase 9.6). */
  attachmentIds: string[] = [],
): Promise<TurnOutcome> {
  const res = await authedFetch(
    `${API_URL}/api/v1/conversations/${conversationId}/messages`,
    (token) => ({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(
        attachmentIds.length ? { text, attachment_ids: attachmentIds } : { text },
      ),
      signal,
    }),
  );
  return readTurn(res, onEvent, null, signal);
}

/** Watch a turn already running (or just finished) in a conversation, from
 *  its first event, or from after `after`: what a page does when it opens a
 *  conversation that is answering (QOS-01), or when its connection dropped
 *  (Phase 8.6). */
export async function watchTurn(
  conversationId: string,
  turnId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
  after: string | null = null,
): Promise<TurnOutcome> {
  const since = after ? `?after=${encodeURIComponent(after)}` : "";
  const res = await authedFetch(
    `${API_URL}/api/v1/conversations/${conversationId}/turns/${encodeURIComponent(turnId)}/events${since}`,
    (token) => ({
      headers: {
        ...readReasonHeaders(`/api/v1/conversations/${conversationId}`),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      } as Record<string, string>,
      signal,
    }),
  );
  return { ...(await readTurn(res, onEvent, after, signal)), turnId };
}

// ── Needs review (Phase 10.3) and conversation export (Phase 10.5) ──
//
// Hand-written until the shared types are generated again.

export type ReviewState = "open" | "turned_into_case" | "dismissed";
export type ReviewReason = "thumbs_down" | "not_solved" | "failed" | "declined";

/** An answer that needed review, with its question: never who asked. */
export type ReviewItem = {
  /** The answer's id: what the actions name. */
  id: string;
  state: ReviewState;
  reasons: ReviewReason[];
  comment: string;
  question: string;
  answer: string;
  version_number?: number | null;
  created_at: string;
  eval_case_id?: string | null;
  suite_id?: string | null;
  dismissed_reason?: string | null;
  decided_at?: string | null;
  fixed_version_number?: number | null;
  asker_told: boolean;
  /** Admins only: to open with a recorded reason. */
  conversation_id?: string | null;
};

export const reviewQueue = {
  list: (assistantId: string, state: ReviewState = "open") =>
    request<ReviewItem[]>(`/api/v1/assistants/${assistantId}/review-queue?state=${state}`),
  toCase: (
    assistantId: string,
    itemId: string,
    body: { suite_id: string; input?: string | null; expected?: Partial<EvalExpected> },
  ) =>
    request<ReviewItem>(`/api/v1/assistants/${assistantId}/review-queue/${itemId}:to-case`, {
      body,
    }),
  dismiss: (assistantId: string, itemId: string, reason: string) =>
    request<ReviewItem>(`/api/v1/assistants/${assistantId}/review-queue/${itemId}:dismiss`, {
      body: { reason },
    }),
};

/** A conversation as a file to save, with the name the server gave it. */
export async function exportConversation(
  conversationId: string,
  format: "md" | "json" = "md",
): Promise<{ blob: Blob; filename: string }> {
  const path = `/api/v1/conversations/${conversationId}/export?format=${format}`;
  const res = await authedFetch(`${API_URL}${path}`, (token) => {
    const headers: Record<string, string> = { ...readReasonHeaders(path) };
    if (token) headers.Authorization = `Bearer ${token}`;
    const org = orgStore.get();
    if (org) headers["X-Org-Id"] = org;
    return { headers, cache: "no-store" };
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, (text ? JSON.parse(text) : null) as ApiErrorBody);
  }
  const named = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "");
  return { blob: await res.blob(), filename: named?.[1] ?? `conversation.${format}` };
}
