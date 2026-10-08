/**
 * Typed client for the Assistant Studio backend (Phase 1).
 * Tokens live in localStorage; the active org id is attached as X-Org-Id.
 */

import { createAuthedFetch, createRefresher } from "@/lib/session";
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
export type EvalCase = S["EvalCaseOut"];
export type EvalExpected = S["EvalExpected-Output"];
export type EvalLabels = S["EvalLabels-Output"];
/** A case as it is sent: every expectation is optional. */
export type EvalCaseDraft = {
  input: string;
  expected?: Partial<EvalExpected>;
  labels?: Partial<EvalLabels>;
};
export type EvalRun = S["EvalRunSummary"];
export type EvalRunDetail = S["EvalRunDetail"];
export type EvalCaseResult = S["EvalCaseResultOut"];

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

/** What a guardrail did during a turn (task 5.3): live as an event, and
 *  saved with the answer as a block. */
export type GuardrailFinding = {
  check: "injection" | "exfiltration" | "pii" | "schema" | "budget";
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
  | ({ type: "guardrail" } & GuardrailFinding);

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
  /** One published version, with its graph and config. */
  version: (id: string, number: number) =>
    request<AssistantVersion & { graph: Graph; config: AssistantConfig }>(
      `/api/v1/assistants/${id}/versions/${number}`,
    ),
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

export const conversations = {
  list: (assistantId: string, cursor?: string | null) =>
    request<Page<Conversation>>(paged(`/api/v1/assistants/${assistantId}/conversations`, cursor)),
  create: (assistantId: string, title?: string) =>
    request<Conversation>(`/api/v1/assistants/${assistantId}/conversations`, { body: { title } }),
  /** The conversation plus its most recent messages (not the whole history:
   *  `messages_next_cursor` pages further back via `olderMessages`). */
  /** The conversation plus its most recent messages, and the turn running
   *  in it (`turn_id`), if any. */
  get: (id: string) =>
    request<
      Conversation & {
        messages: ChatMessage[];
        messages_next_cursor: string | null;
        turn_id?: string | null;
      }
    >(`/api/v1/conversations/${id}`),
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
export type Budgets = S["BudgetsOut"];
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
   *  `refused` error is the model declining, not a failure. */
  | { type: "error"; code: string; message: string; retryable?: boolean }
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

/** Read a turn's events off a response until it ends. */
async function readTurn(res: Response, onEvent: (e: ChatEvent) => void): Promise<void> {
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
      const parsed = parseSseFrame(frame);
      if (parsed) onEvent(parsed.event);
    }
  }
}

/** Send a message: starts a turn on the server and watches it. Leaving
 *  (aborting `signal`) stops watching, not the turn (QOS-01). */
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
  await readTurn(res, onEvent);
}

/** Watch a turn already running (or just finished) in a conversation, from
 *  its first event: what a page does when it opens a conversation that is
 *  answering (QOS-01). */
export async function watchTurn(
  conversationId: string,
  turnId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(
    `${API_URL}/api/v1/conversations/${conversationId}/turns/${encodeURIComponent(turnId)}/events`,
    (token) => ({
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) } as Record<string, string>,
      signal,
    }),
  );
  await readTurn(res, onEvent);
}
