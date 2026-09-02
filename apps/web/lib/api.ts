/**
 * Thin API client for the Assistant Studio backend.
 *
 * Phase 0: auth + orgs. Tokens are kept in localStorage (a real refresh-on-401
 * interceptor lands in Phase 1 alongside the chat UI).
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ACCESS_KEY = "as.access";
const REFRESH_KEY = "as.refresh";

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
  requestId?: string;

  constructor(status: number, body: ApiErrorBody) {
    super(body?.error?.message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code ?? "unknown";
    this.requestId = body?.request_id;
  }
}

export const tokenStore = {
  get access() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_KEY);
  },
  set(pair: TokenPair) {
    window.localStorage.setItem(ACCESS_KEY, pair.access_token);
    window.localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

type RequestOptions = {
  method?: string;
  body?: unknown;
  auth?: boolean;
  orgId?: string;
};

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.auth !== false && tokenStore.access) {
    headers.Authorization = `Bearer ${tokenStore.access}`;
  }
  if (opts.orgId) headers["X-Org-Id"] = opts.orgId;

  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  const data = text ? JSON.parse(text) : null;

  if (!res.ok) throw new ApiError(res.status, data as ApiErrorBody);
  return data as T;
}

// ── Typed endpoints ──────────────────────────────────────────

export const auth = {
  register: (email: string, password: string, name: string) =>
    api<TokenPair>("/api/v1/auth/register", { body: { email, password, name }, auth: false }),
  login: (email: string, password: string) =>
    api<TokenPair>("/api/v1/auth/login", { body: { email, password }, auth: false }),
  me: () =>
    api<{
      user: { id: string; email: string; name: string };
      memberships: { org_id: string; role: string }[];
    }>("/api/v1/auth/me"),
  logout: (refresh_token: string) =>
    api<{ message: string }>("/api/v1/auth/logout", { body: { refresh_token }, auth: false }),
};

export type Org = {
  id: string;
  name: string;
  slug: string;
  is_personal: boolean;
  created_at: string;
};

export const orgs = {
  list: () => api<Org[]>("/api/v1/orgs"),
  create: (name: string) => api<Org>("/api/v1/orgs", { body: { name } }),
};
