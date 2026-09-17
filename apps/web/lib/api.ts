/**
 * Typed client for the Assistant Studio backend (Phase 1).
 * Tokens live in localStorage; the active org id is attached as X-Org-Id.
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "as.access";
const REFRESH_KEY = "as.refresh";
const ORG_KEY = "as.org";

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

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

async function request<T>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.auth !== false && tokenStore.access)
    headers.Authorization = `Bearer ${tokenStore.access}`;
  if (opts.org !== false) {
    const org = orgStore.get();
    if (org) headers["X-Org-Id"] = org;
  }
  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data as ApiErrorBody);
  return data as T;
}

// ── Types (hand-written subset) ──────────────────────────────

export type Me = {
  user: { id: string; email: string; name: string };
  memberships: { org_id: string; role: string }[];
};
export type Org = { id: string; name: string; slug: string; is_personal: boolean };

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

export type ValidationIssue = {
  code: string;
  message: string;
  node_id?: string | null;
};
export type ValidationResult = { errors: ValidationIssue[]; warnings: ValidationIssue[] };

export type Assistant = {
  id: string;
  org_id: string;
  name: string;
  slug: string;
  description: string;
  status: "draft" | "published" | "archived";
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
};
export type AssistantDetail = Assistant & {
  draft_graph: Graph;
  draft_config: AssistantConfig;
  draft_validation: ValidationResult;
};
export type AssistantVersion = {
  id: string;
  version_number: number;
  note: string;
  created_at: string;
};

export type Conversation = {
  id: string;
  assistant_id: string;
  assistant_version_id: string | null;
  title: string;
  status: string;
  cost_usd: string;
  token_usage: Record<string, number>;
  created_at: string;
  last_message_at: string | null;
};
export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  blocks: unknown[];
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  created_at: string;
};

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

export const orgs = {
  list: () => request<Org[]>("/api/v1/orgs", { org: false }),
};

export const meta = {
  configSchema: () =>
    request<{ schema: unknown; default: AssistantConfig; allowed_models: string[] }>(
      "/api/v1/meta/config-schema",
      { org: false },
    ),
};

export const assistants = {
  list: () => request<Assistant[]>("/api/v1/assistants"),
  create: (name: string, description = "") =>
    request<AssistantDetail>("/api/v1/assistants", { body: { name, description } }),
  get: (id: string) => request<AssistantDetail>(`/api/v1/assistants/${id}`),
  patch: (id: string, body: Partial<Pick<Assistant, "name" | "description" | "status">>) =>
    request<AssistantDetail>(`/api/v1/assistants/${id}`, { method: "PATCH", body }),
  putDraftGraph: (id: string, graph: Graph) =>
    request<{ graph: Graph; validation: ValidationResult; config: AssistantConfig | null }>(
      `/api/v1/assistants/${id}/draft-graph`,
      { method: "PUT", body: graph },
    ),
  putDraftConfig: (id: string, config: AssistantConfig) =>
    request<{ config: AssistantConfig; graph: Graph }>(`/api/v1/assistants/${id}/draft-config`, {
      method: "PUT",
      body: config,
    }),
  publish: (id: string, note = "") =>
    request<AssistantVersion & { graph: Graph; config: AssistantConfig }>(
      `/api/v1/assistants/${id}/versions`,
      { body: { note } },
    ),
  versions: (id: string) => request<AssistantVersion[]>(`/api/v1/assistants/${id}/versions`),
};

export const conversations = {
  list: (assistantId: string) =>
    request<Conversation[]>(`/api/v1/assistants/${assistantId}/conversations`),
  create: (assistantId: string, title?: string) =>
    request<Conversation>(`/api/v1/assistants/${assistantId}/conversations`, { body: { title } }),
  get: (id: string) =>
    request<Conversation & { messages: ChatMessage[] }>(`/api/v1/conversations/${id}`),
  rename: (id: string, title: string) =>
    request<Conversation>(`/api/v1/conversations/${id}`, { method: "PATCH", body: { title } }),
};

// ── SSE chat stream ──────────────────────────────────────────

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; id: string; status: string; output: string }
  | { type: "usage"; tokens_in: number; tokens_out: number; cost_usd: number }
  | { type: "error"; code: string; message: string }
  | { type: "done"; message_id: string; run_id: string };

export async function streamMessage(
  conversationId: string,
  text: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(tokenStore.access ? { Authorization: `Bearer ${tokenStore.access}` } : {}),
    },
    body: JSON.stringify({ text }),
    signal,
  });
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
