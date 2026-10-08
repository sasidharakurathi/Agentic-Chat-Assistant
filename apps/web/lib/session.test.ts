import { describe, expect, it, vi } from "vitest";

import {
  createAuthedFetch,
  createRefresher,
  endsSession,
  retryDelayMs,
  type StoredTokens,
} from "./session";

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
    expect(await refresh()).toBe("ok");
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
    expect(await both).toEqual(["ok", "ok", "ok"]);
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
    expect(await refresh()).toBe("ok");
    expect(fetchFn).not.toHaveBeenCalled();
    expect(tokens.access).toBe("from-other-tab");
  });

  it("tells a refused session from one it couldn't reach (Phase 7a.8)", async () => {
    const tokens = store("a", "r");
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response("{}", { status: 429 }))
      .mockResolvedValueOnce(new Response("{}", { status: 503 }))
      .mockResolvedValueOnce(ok("a3", "r3"));
    const refresh = createRefresher({ tokens, refreshUrl: "/r", fetchFn });
    expect(await refresh()).toBe("rejected");
    expect(await refresh()).toBe("unavailable");
    expect(await refresh()).toBe("unavailable");
    expect(await refresh()).toBe("unavailable");
    expect(await refresh()).toBe("ok");
  });

  it("has nothing to refresh without a refresh token", async () => {
    const fetchFn = vi.fn();
    const refresh = createRefresher({ tokens: store(null, null), refreshUrl: "/r", fetchFn });
    expect(await refresh()).toBe("rejected");
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe("staying signed in (Phase 7a.8)", () => {
  it("ends the session only when the server refuses it", () => {
    expect(endsSession({ status: 401, code: "invalid_refresh" })).toBe(true);
    expect(endsSession({ status: 0, code: "offline" })).toBe(false);
    expect(endsSession({ status: 429 })).toBe(false);
    expect(endsSession({ status: 503 })).toBe(false);
    expect(endsSession(new TypeError("Failed to fetch"))).toBe(false);
    expect(endsSession(null)).toBe(false);
  });

  it("tries again sooner first, then no more than every 30 seconds", () => {
    expect([0, 1, 2, 3, 4, 10].map(retryDelayMs)).toEqual([
      2_000, 4_000, 8_000, 16_000, 30_000, 30_000,
    ]);
  });
});

describe("a request with an expired token (Phase 7a.8)", () => {
  const offline = () => Object.assign(new Error("offline"), { status: 0, code: "offline" });
  const reply = (status: number) => new Response("{}", { status });

  it("retries once with the new token when the refresh worked", async () => {
    const tokens = store("old", "r");
    const fetchFn = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(reply(401))
      .mockResolvedValueOnce(reply(200));
    const refresh = vi.fn(async () => {
      tokens.set({ access_token: "new", refresh_token: "r2" });
      return "ok" as const;
    });
    const authed = createAuthedFetch({ tokens, refresh, offline, fetchFn });
    const res = await authed("/x", (t) => ({ headers: { Authorization: `Bearer ${t}` } }));
    expect(res.status).toBe(200);
    expect(fetchFn.mock.calls[1][1]).toEqual({ headers: { Authorization: "Bearer new" } });
  });

  it("says offline, not signed out, when the refresh couldn't be made", async () => {
    const authed = createAuthedFetch({
      tokens: store("old", "r"),
      refresh: async () => "unavailable",
      offline,
      fetchFn: vi.fn<typeof fetch>(async () => reply(401)),
    });
    await expect(authed("/x", () => ({}))).rejects.toMatchObject({ code: "offline", status: 0 });
  });

  it("hands back the 401 when the server refused the session", async () => {
    const authed = createAuthedFetch({
      tokens: store("old", "r"),
      refresh: async () => "rejected",
      offline,
      fetchFn: vi.fn<typeof fetch>(async () => reply(401)),
    });
    expect((await authed("/x", () => ({}))).status).toBe(401);
  });
});
