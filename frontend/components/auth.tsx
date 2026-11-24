"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { User } from "@/lib/types";

type AuthState = {
  user: User | null;
  can: (permission: string) => boolean;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({ user: null, can: () => false, logout: async () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => router.replace("/login"));
  }, [router]);

  // Mirrors backend RBAC so buttons can be disabled; the backend still enforces every rule.
  const can = useCallback(
    (permission: string) => !!user && (user.permissions.includes("*") || user.permissions.includes(permission)),
    [user],
  );

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }, [router]);

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">Loading ReqPilot…</div>
    );
  }
  return <AuthContext.Provider value={{ user, can, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
