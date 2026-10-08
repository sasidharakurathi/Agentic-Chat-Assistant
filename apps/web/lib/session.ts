/** Keeping a session alive past its 15-minute access token.
 *
 *  Nothing in the app used to call `/auth/refresh`: a 401 was just an error,
 *  and the auth provider answered it by clearing the tokens. So every user
 *  was signed out 15 minutes after signing in, with a valid 30-day refresh
 *  token sitting unused in storage.
 *
 *  Two things make refreshing safe, because the API revokes a whole session
 *  when a refresh token is presented twice (reuse detection):
 *  - one refresh per tab at a time: concurrent 401s share it;
 *  - one per browser at a time, via a Web Lock, and a tab that waited finds
 *    the token already rotated by another tab and uses the new one instead
 *    of presenting the spent one.
 *
 *  And a refresh that could not happen is not one that was refused (Phase
 *  7a.8): on a phone, a dropped connection, a busy server (429) or a restart
 *  (5xx) used to read as "signed out", and people signed in again many
 *  times a day. Only the server saying no ends a session. */

export type StoredTokens = {
  readonly access: string | null;
  readonly refresh: string | null;
  set(pair: { access_token: string; refresh_token: string }): void;
};

/** `ok`: there is a fresh access token to retry with. `rejected`: the server
 *  refused the session (401), or there is none. `unavailable`: no answer
 *  that says anything about the session (no network, 429, 5xx): try later. */
export type RefreshResult = "ok" | "rejected" | "unavailable";

type Deps = {
  tokens: StoredTokens;
  refreshUrl: string;
  fetchFn?: typeof fetch;
  locks?: { request<T>(name: string, cb: () => Promise<T>): Promise<T> } | null;
};

const TOO_MANY_REQUESTS = 429;
const SERVER_ERROR = 500;

export function createRefresher({ tokens, refreshUrl, fetchFn, locks }: Deps) {
  let inflight: Promise<RefreshResult> | null = null;

  async function exchange(spent: string): Promise<RefreshResult> {
    // Rotated by another tab while this one waited for the lock.
    if (tokens.refresh !== spent) return tokens.access ? "ok" : "rejected";
    let res: Response;
    try {
      res = await (fetchFn ?? fetch)(refreshUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: spent }),
        cache: "no-store",
      });
    } catch {
      return "unavailable";
    }
    if (res.ok) {
      tokens.set(await res.json());
      return "ok";
    }
    return res.status === TOO_MANY_REQUESTS || res.status >= SERVER_ERROR
      ? "unavailable"
      : "rejected";
  }

  return function refresh(): Promise<RefreshResult> {
    const spent = tokens.refresh;
    if (!spent) return Promise.resolve("rejected");
    inflight ??= (locks ? locks.request("as.refresh", () => exchange(spent)) : exchange(spent))
      .catch((): RefreshResult => "unavailable")
      .finally(() => {
        inflight = null;
      });
    return inflight;
  };
}

/** `fetch` with the current access token, refreshed and retried once on a
 *  401. `init` is a function so the retry is built with the new token. When
 *  the refresh could not be made (no network, a busy or restarting server),
 *  this throws `offline()` rather than handing back the 401, which the app
 *  would read as "signed out" (Phase 7a.8). */
export function createAuthedFetch({
  tokens,
  refresh,
  offline,
  fetchFn,
}: {
  tokens: StoredTokens;
  refresh: () => Promise<RefreshResult>;
  offline: () => Error;
  fetchFn?: typeof fetch;
}) {
  return async function authedFetch(
    url: string,
    init: (token: string | null) => RequestInit,
  ): Promise<Response> {
    const send = fetchFn ?? fetch;
    const res = await send(url, init(tokens.access));
    if (res.status !== 401 || !tokens.refresh) return res;
    const refreshed = await refresh();
    if (refreshed === "ok") return send(url, init(tokens.access));
    if (refreshed === "unavailable") throw offline();
    return res;
  };
}

/** Whether a failure to load the signed-in user means the session is over.
 *  Only a 401 does: the API refused the tokens even after a refresh. A
 *  dropped connection, a 429 or a 5xx says nothing about them. */
export function endsSession(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "status" in err &&
    (err as { status: unknown }).status === 401
  );
}

/** How long to wait before the next try to reach the server: 2 s, doubling,
 *  at most 30 s. Coming back online, or to the tab, tries at once. */
export function retryDelayMs(attempt: number): number {
  return Math.min(30_000, 2_000 * 2 ** Math.max(0, attempt));
}
