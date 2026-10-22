"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  auth as authApi,
  orgs as orgsApi,
  orgStore,
  tokenStore,
  type Me,
  type Org,
  type LoginOut,
  type TokenPair,
} from "@/lib/api";
import { inviteTokenFrom, safeNext } from "@/lib/safe-next";
import { endsSession, retryDelayMs } from "@/lib/session";

export { safeNext };

type AuthState = {
  ready: boolean;
  /** Signed in, but the server can't be reached right now (no network, or
   *  it is busy or restarting): retrying, and still signed in (Phase 7a.8). */
  offline: boolean;
  user: Me["user"] | null;
  orgs: Org[];
  activeOrgId: string | null;
  /** The signed-in user's role in the active org. */
  activeRole: string | null;
  setActiveOrg: (id: string) => void;
  /** `next`: where to land afterwards (e.g. back on an invite link). */
  /** Resolves with a challenge when the account has two-step sign-in
   *  (Phase 8.4): finish it with `finishTwoFactor`. */
  login: (email: string, password: string, next?: string | null) => Promise<string | null>;
  finishTwoFactor: (challenge: string, code: string, next?: string | null) => Promise<void>;
  /** The signed-in person's two-step sign-in, from /auth/me. */
  twoFactor: Me["two_factor"] | null;
  register: (email: string, password: string, name: string, next?: string | null) => Promise<void>;
  /** Signed in another way (Google, Phase 8.3): keep the pair, then on. */
  adopt: (pair: LoginOut | TokenPair, next?: string | null) => Promise<string | null>;
  /** Sign out, then land on the login page, which returns to `next` after. */
  logout: (next?: string) => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Me["user"] | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [memberships, setMemberships] = useState<Me["memberships"]>([]);
  const [twoFactor, setTwoFactor] = useState<Me["two_factor"] | null>(null);
  const [offline, setOffline] = useState(false);
  /** Tries since the server was last reached, for the backoff. */
  const attempts = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    if (retryTimer.current) clearTimeout(retryTimer.current);
    retryTimer.current = null;
    if (!tokenStore.access) {
      setReady(true);
      return;
    }
    try {
      const [me, orgList] = await Promise.all([authApi.me(), orgsApi.list()]);
      setUser(me.user);
      setMemberships(me.memberships);
      setTwoFactor(me.two_factor ?? null);
      setOrgs(orgList);
      const stored = orgStore.get();
      // The team's organisation before anyone's personal one (Phase 7b.8).
      const first = orgList.find((o) => !o.is_personal) ?? orgList[0];
      const valid = orgList.find((o) => o.id === stored)?.id ?? first?.id ?? null;
      if (valid) orgStore.set(valid);
      setActiveOrgId(valid);
      attempts.current = 0;
      setOffline(false);
    } catch (err) {
      if (endsSession(err)) {
        // The server refused the tokens, even after a refresh.
        tokenStore.clear();
        orgStore.clear();
        setUser(null);
        setOffline(false);
      } else {
        // No network, or the server busy or restarting: still signed in.
        // Try again later; coming back online or to the tab tries at once.
        setOffline(true);
        retryTimer.current = setTimeout(() => void load(), retryDelayMs(attempts.current++));
      }
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!offline) return;
    const now = () => void load();
    const visible = () => document.visibilityState === "visible" && now();
    window.addEventListener("online", now);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("online", now);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [offline, load]);

  // A role changed while the app was open (Phase 8.1): seen the next time
  // the page comes back into view, at most once a minute. The server still
  // decides every request; this only keeps the page in step with it.
  const lastSeen = useRef(0);
  useEffect(() => {
    const visible = () => {
      if (document.visibilityState !== "visible" || !tokenStore.access) return;
      const now = Date.now();
      if (now - lastSeen.current < 60_000) return;
      lastSeen.current = now;
      void load();
    };
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, [load]);

  useEffect(
    () => () => {
      if (retryTimer.current) clearTimeout(retryTimer.current);
    },
    [],
  );

  const setActiveOrg = useCallback((id: string) => {
    orgStore.set(id);
    setActiveOrgId(id);
  }, []);

  const afterAuth = useCallback(
    async (next?: string | null) => {
      await load();
      router.push(safeNext(next) ?? "/assistants");
    },
    [load, router],
  );

  const adopt = useCallback(
    async (pair: LoginOut | TokenPair, next?: string | null) => {
      if ("two_factor_challenge" in pair && pair.two_factor_challenge) {
        return pair.two_factor_challenge;
      }
      if (!pair.access_token || !pair.refresh_token) throw new Error("no tokens");
      tokenStore.set({
        access_token: pair.access_token,
        refresh_token: pair.refresh_token,
        token_type: pair.token_type ?? "bearer",
        expires_in: pair.expires_in ?? 900,
      });
      await afterAuth(next);
      return null;
    },
    [afterAuth],
  );

  const login = useCallback(
    async (email: string, password: string, next?: string | null) =>
      adopt(await authApi.login(email, password), next),
    [adopt],
  );

  const finishTwoFactor = useCallback(
    async (challenge: string, code: string, next?: string | null) => {
      tokenStore.set(await authApi.loginTwoFactor(challenge, code));
      await afterAuth(next);
    },
    [afterAuth],
  );

  const register = useCallback(
    async (email: string, password: string, name: string, next?: string | null) => {
      tokenStore.set(await authApi.register(email, password, name, inviteTokenFrom(next)));
      await afterAuth(next);
    },
    [afterAuth],
  );

  const logout = useCallback(
    (next?: string) => {
      const rt = tokenStore.refresh;
      if (rt) void authApi.logout(rt).catch(() => {});
      tokenStore.clear();
      orgStore.clear();
      setUser(null);
      setOrgs([]);
      setMemberships([]);
      setActiveOrgId(null);
      const back = safeNext(next);
      router.replace(back ? `/login?next=${encodeURIComponent(back)}` : "/login");
    },
    [router],
  );

  const value = useMemo<AuthState>(
    () => ({
      ready,
      offline,
      user,
      orgs,
      activeOrgId,
      activeRole: memberships.find((m) => m.org_id === activeOrgId)?.role ?? null,
      setActiveOrg,
      login,
      finishTwoFactor,
      twoFactor,
      register,
      adopt,
      logout,
      refresh: load,
    }),
    [
      ready,
      offline,
      user,
      orgs,
      activeOrgId,
      memberships,
      setActiveOrg,
      login,
      finishTwoFactor,
      twoFactor,
      register,
      adopt,
      logout,
      load,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
