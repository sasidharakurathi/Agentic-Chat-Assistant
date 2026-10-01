"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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

export { safeNext };

type AuthState = {
  ready: boolean;
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

  const load = useCallback(async () => {
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
    } catch {
      tokenStore.clear();
      orgStore.clear();
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
    [ready, user, orgs, activeOrgId, memberships, setActiveOrg, login, register, logout, load],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
