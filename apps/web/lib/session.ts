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
 *    of presenting the spent one. */

export type StoredTokens = {
  readonly access: string | null;
  readonly refresh: string | null;
  set(pair: { access_token: string; refresh_token: string }): void;
};

type Deps = {
  tokens: StoredTokens;
  refreshUrl: string;
  fetchFn?: typeof fetch;
  locks?: { request<T>(name: string, cb: () => Promise<T>): Promise<T> } | null;
};

export function createRefresher({ tokens, refreshUrl, fetchFn, locks }: Deps) {
  let inflight: Promise<boolean> | null = null;

  async function exchange(spent: string): Promise<boolean> {
    // Rotated by another tab while this one waited for the lock.
    if (tokens.refresh !== spent) return Boolean(tokens.access);
    const res = await (fetchFn ?? fetch)(refreshUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: spent }),
      cache: "no-store",
    });
    if (!res.ok) return false;
    tokens.set(await res.json());
    return true;
  }

  /** True when there is a fresh access token to retry with. */
  return function refresh(): Promise<boolean> {
    const spent = tokens.refresh;
    if (!spent) return Promise.resolve(false);
    inflight ??= (locks ? locks.request("as.refresh", () => exchange(spent)) : exchange(spent))
      .catch(() => false)
      .finally(() => {
        inflight = null;
      });
    return inflight;
  };
}
