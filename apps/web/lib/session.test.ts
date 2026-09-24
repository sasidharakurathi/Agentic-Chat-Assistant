import { describe, expect, it, vi } from "vitest";

import { createRefresher, type StoredTokens } from "./session";

function store(access: string | null, refresh: string | null) {
  const s = {
    access,
    refresh,
    set(pair: { access_token: string; refresh_token: string }) {
      s.access = pair.access_token;
      s.refresh = pair.refresh_token;
    },
  };
  return s as StoredTokens & { access: string | null; refresh: string | null };
}

const ok = (access: string, refresh: string) =>
  new Response(JSON.stringify({ access_token: access, refresh_token: refresh }), { status: 200 });

describe("createRefresher", () => {
  it("exchanges the refresh token and stores the new pair", async () => {
    const tokens = store("old-a", "old-r");
    const fetchFn = vi.fn<typeof fetch>(async () => ok("new-a", "new-r"));
    const refresh = createRefresher({ tokens, refreshUrl: "/r", fetchFn });
    expect(await refresh()).toBe(true);
    expect([tokens.access, tokens.refresh]).toEqual(["new-a", "new-r"]);
    const init = fetchFn.mock.calls[0][1];
    expect(JSON.parse(String(init?.body))).toEqual({ refresh_token: "old-r" });
  });

  it("shares one refresh between concurrent callers", async () => {
    // Presenting the same refresh token twice is "reuse" to the API, which
    // then revokes the whole session.
    const tokens = store("a", "r");
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const fetchFn = vi.fn(async () => {
      await gate;
      return ok("a2", "r2");
    });
    const refresh = createRefresher({ tokens, refreshUrl: "/r", fetchFn });
    const both = Promise.all([refresh(), refresh(), refresh()]);
    release();
    expect(await both).toEqual([true, true, true]);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("does not present a token another tab already rotated", async () => {
    const tokens = store("a", "r");
    const fetchFn = vi.fn(async () => ok("x", "y"));
    // The lock is held by "another tab", which rotates the pair meanwhile.
    const locks = {
      async request<T>(_name: string, cb: () => Promise<T>): Promise<T> {
        tokens.set({ access_token: "from-other-tab", refresh_token: "r2" });
        return cb();
      },
    };
    const refresh = createRefresher({ tokens, refreshUrl: "/r", fetchFn, locks });
    expect(await refresh()).toBe(true);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(tokens.access).toBe("from-other-tab");
  });

  it("reports failure without throwing, and can try again later", async () => {
    const tokens = store("a", "r");
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(ok("a3", "r3"));
    const refresh = createRefresher({ tokens, refreshUrl: "/r", fetchFn });
    expect(await refresh()).toBe(false);
    expect(await refresh()).toBe(false);
    expect(await refresh()).toBe(true);
  });

  it("does nothing without a refresh token", async () => {
    const fetchFn = vi.fn();
    const refresh = createRefresher({ tokens: store(null, null), refreshUrl: "/r", fetchFn });
    expect(await refresh()).toBe(false);
    expect(fetchFn).not.toHaveBeenCalled();
  });
});
