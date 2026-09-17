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

type AuthState = {
  ready: boolean;
  user: Me["user"] | null;
  orgs: Org[];
  activeOrgId: string | null;
  setActiveOrg: (id: string) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Me["user"] | null>(null);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!tokenStore.access) {
      setReady(true);
      return;
    }
    try {
      const [me, orgList] = await Promise.all([authApi.me(), orgsApi.list()]);
      setUser(me.user);
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

  const afterAuth = useCallback(async () => {
    await load();
    router.push("/assistants");
  }, [load, router]);

  const login = useCallback(
    async (email: string, password: string) => {
      tokenStore.set(await authApi.login(email, password));
      await afterAuth();
    },
    [afterAuth],
  );

  const register = useCallback(
    async (email: string, password: string, name: string) => {
      tokenStore.set(await authApi.register(email, password, name));
      await afterAuth();
    },
    [afterAuth],
  );

  const logout = useCallback(() => {
    const rt = tokenStore.refresh;
    if (rt) void authApi.logout(rt).catch(() => {});
    tokenStore.clear();
    orgStore.clear();
    setUser(null);
    setOrgs([]);
    setActiveOrgId(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      orgs,
      activeOrgId,
      setActiveOrg,
      login,
      register,
      logout,
      refresh: load,
    }),
    [ready, user, orgs, activeOrgId, setActiveOrg, login, register, logout, load],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
