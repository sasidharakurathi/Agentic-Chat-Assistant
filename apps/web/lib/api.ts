/**
 * Typed client for the Assistant Studio backend (Phase 1).
 * Tokens live in localStorage; the active org id is attached as X-Org-Id.
 */

import { createRefresher } from "@/lib/session";
import type { Schemas } from "@assistant-studio/shared";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "as.access";
const REFRESH_KEY = "as.refresh";
const ORG_KEY = "as.org";

export type ApiErrorBody = {
  error: { code: string; message: string; details?: unknown };
  request_id?: string;
};

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(status: number, body: ApiErrorBody) {
    super(body?.error?.message ?? `HTTP ${status}`);
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

type Opts = { method?: string; body?: unknown; auth?: boolean; org?: boolean };

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

/** `fetch` with the current access token, refreshed and retried once on a
 *  401 (see `lib/session.ts`). `init` is a function so the retry is built
 *  with the new token. */
async function authedFetch(url: string, init: (token: string | null) => RequestInit) {
  const res = await fetch(url, init(tokenStore.access));
  if (res.status !== 401 || !tokenStore.refresh) return res;
  return (await refreshSession()) ? fetch(url, init(tokenStore.access)) : res;
}

async function request<T>(path: string, opts: Opts = {}): Promise<T> {
  const build = (token: string | null): RequestInit => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (opts.auth !== false && token) headers.Authorization = `Bearer ${token}`;
    if (opts.org !== false) {
      const org = orgStore.get();
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
export type Me = S["MeResponse"];
export type Org = S["OrgOut"];

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
export type ValidationResult = S["ValidationResult"];

export type Assistant = S["AssistantSummary"];
export type AssistantDetail = Omit<S["AssistantDetail"], "draft_graph" | "draft_config"> & {
  draft_graph: Graph;
  draft_config: AssistantConfig;
};
export type AssistantVersion = S["VersionSummary"];
export type EffortLevel = S["ModelSpec-Output"]["effort"];
export type DiffEntry = S["DiffEntry"];
export type VersionDiff = S["VersionDiff"];

export type Conversation = S["ConversationSummary"];
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
  | ({ type: "citation" } & Citation);

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
  register: (email: string, password: string, name: string) =>
    request<TokenPair>("/api/v1/auth/register", {
      body: { email, password, name },
      auth: false,
      org: false,
    }),
  login: (email: string, password: string) =>
    request<TokenPair>("/api/v1/auth/login", {
      body: { email, password },
      auth: false,
      org: false,
    }),
  me: () => request<Me>("/api/v1/auth/me", { org: false }),
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
    }>(`/api/v1/orgs/${orgId}/invites`, { body: { email, role }, org: false }),
};

export const orgs = {
  list: () => collectAll((c) => request<Page<Org>>(paged("/api/v1/orgs", c, 200), { org: false })),
  members: (orgId: string, cursor?: string | null) =>
    request<Page<Member>>(paged(`/api/v1/orgs/${orgId}/members`, cursor), { org: false }),
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
    request<{ schema: unknown; default: AssistantConfig; allowed_models: string[] }>(
      "/api/v1/meta/config-schema",
      { org: false },
    ),
  graphSchema: () => request<GraphSchema>("/api/v1/meta/graph-schema", { org: false }),
};

export const assistants = {
  list: (cursor?: string | null) => request<Page<Assistant>>(paged("/api/v1/assistants", cursor)),
  create: (name: string, description = "") =>
    request<AssistantDetail>("/api/v1/assistants", { body: { name, description } }),
  get: (id: string) => request<AssistantDetail>(`/api/v1/assistants/${id}`),
  patch: (id: string, body: Partial<Pick<Assistant, "name" | "description" | "status">>) =>
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
  publish: (id: string, note = "") =>
    request<AssistantVersion & { graph: Graph; config: AssistantConfig }>(
      `/api/v1/assistants/${id}/versions`,
      { body: { note } },
    ),
  versions: (id: string, cursor?: string | null) =>
    request<Page<AssistantVersion>>(paged(`/api/v1/assistants/${id}/versions`, cursor)),
  /** Config + graph differences between two published versions. */
  diff: (id: string, a: number, b: number) =>
    request<VersionDiff>(`/api/v1/assistants/${id}/versions/diff?a=${a}&b=${b}`),
};

export const conversations = {
  list: (assistantId: string, cursor?: string | null) =>
    request<Page<Conversation>>(paged(`/api/v1/assistants/${assistantId}/conversations`, cursor)),
  create: (assistantId: string, title?: string) =>
    request<Conversation>(`/api/v1/assistants/${assistantId}/conversations`, { body: { title } }),
  /** The conversation plus its most recent messages (not the whole history:
   *  `messages_next_cursor` pages further back via `olderMessages`). */
  get: (id: string) =>
    request<Conversation & { messages: ChatMessage[]; messages_next_cursor: string | null }>(
      `/api/v1/conversations/${id}`,
    ),
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
  /** Archive: leaves the list and becomes read-only. A direct link still reads it. */
  archive: (id: string) =>
    request<{ message: string }>(`/api/v1/conversations/${id}`, { method: "DELETE" }),
  /** Ask the running turn to stop. Its own stream then ends with a normal
   *  `done`, keeping whatever text had already arrived. */
  interrupt: (id: string) =>
    request<{ message: string }>(`/api/v1/conversations/${id}:interrupt`, { method: "POST" }),
};

export type Run = S["RunOut"];

export type DataSourceStatus = S["DataSourceStatus"];

export type DataSource = S["DataSourceSummary"];

export type ContentUrl = S["ContentUrl"];

export type ApprovalRisk = S["ApprovalRisk"];
export type ApprovalStatus = S["ApprovalStatus"];

export type Approval = S["ApprovalOut"];

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

export const approvals = {
  /** Pending approvals for a conversation. Needed because the SSE event only
   *  reaches whoever was watching the stream — a reload must not orphan a
   *  decision that is genuinely still waiting. */
  listPending: (conversationId: string) =>
    collectAll((c) =>
      request<Page<Approval>>(paged(`/api/v1/conversations/${conversationId}/approvals`, c, 200)),
    ),
  resolve: (approvalId: string, decision: "approved" | "denied") =>
    request<Approval>(`/api/v1/approvals/${approvalId}:resolve`, { body: { decision } }),
};

/** Multipart upload. Deliberately not routed through `request`, which pins
 *  `Content-Type: application/json` — for FormData the browser has to set the
 *  header itself so it can include the multipart boundary. */
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
  | { type: "tool_result"; id: string; status: string; output: string; parent_id?: string | null }
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
  | { type: "error"; code: string; message: string }
  | { type: "done"; message_id: string; run_id: string };

export async function streamMessage(
  conversationId: string,
  text: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(
    `${API_URL}/api/v1/conversations/${conversationId}/messages`,
    (token) => ({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ text }),
      signal,
    }),
  );
  if (!res.ok || !res.body) {
    const body = await res.text();
    throw new ApiError(
      res.status,
      body ? JSON.parse(body) : { error: { code: "stream_failed", message: "stream failed" } },
    );
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as ChatEvent);
    }
  }
}
