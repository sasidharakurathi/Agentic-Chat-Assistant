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
  login: (email: string, password: string, next?: string | null) => Promise<void>;
  register: (email: string, password: string, name: string, next?: string | null) => Promise<void>;
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
      setOrgs(orgList);
      const stored = orgStore.get();
      const valid = orgList.find((o) => o.id === stored)?.id ?? orgList[0]?.id ?? null;
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

  const login = useCallback(
    async (email: string, password: string, next?: string | null) => {
      tokenStore.set(await authApi.login(email, password));
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
      register,
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
      register,
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
