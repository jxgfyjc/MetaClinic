"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { api, fetcher, setUnauthorizedHandler } from "./api";

export type User = {
  id: string; username: string; display_name: string; role: "DOCTOR" | "NURSE" | "PHARMACIST" | "ADMIN";
  dept: string; title: string; license_no: string; must_change_password: boolean; permissions: string[];
};

type Ctx = {
  user: User | null; loading: boolean; can: (perm: string) => boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>; refresh: () => void;
};
const AuthCtx = createContext<Ctx>(null as any);
export const useAuth = () => useContext(AuthCtx);

export const ROLE_LABEL: Record<string, string> = { DOCTOR: "医师", NURSE: "护士", PHARMACIST: "药师", ADMIN: "管理员" };
export const ROLE_HOME: Record<string, string> = { DOCTOR: "/", NURSE: "/", PHARMACIST: "/pharmacy", ADMIN: "/admin" };

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data, error, isLoading, mutate } = useSWR<{ user: User }>(pathname.startsWith("/p/") ? null : "/auth/me", (p) => api.get(p, { noRedirect: true }), {   // patient pages never carry a staff session
    revalidateOnFocus: true, shouldRetryOnError: false, dedupingInterval: 5000,
  });
  const user = data?.user ?? null;

  useEffect(() => {
    setUnauthorizedHandler(() => {
      mutate(undefined, { revalidate: false });
      if (!location.pathname.startsWith("/login") && !location.pathname.startsWith("/p/"))
        router.replace(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    });
    return () => setUnauthorizedHandler(null);
  }, [router, mutate]);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.post<{ user: User; must_change_password: boolean }>("/auth/login", { username, password });
    await mutate({ user: r.user }, { revalidate: false });
    return r.user;
  }, [mutate]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch {}
    await mutate(undefined, { revalidate: false });
    router.replace("/login");
  }, [mutate, router]);

  const can = useCallback((p: string) => !!user?.permissions?.includes(p), [user]);
  const value = useMemo(() => ({ user, loading: isLoading && !error, can, login, logout, refresh: () => mutate() }), [user, isLoading, error, can, login, logout, mutate]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export { fetcher };
