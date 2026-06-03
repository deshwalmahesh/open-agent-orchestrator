import { createContext, useContext, useEffect, useState, type ReactNode, createElement } from "react";
import type { User } from "@/types";
import { getMe, login as apiLogin } from "@/api/auth";

interface AuthCtx {
  token: string | null;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (!token) return;
    getMe(token).then(setUser).catch((err) => {
      console.error("Session validation failed:", err);
      localStorage.removeItem("token");
      setToken(null);
      setUser(null);
    });
  }, [token]);

  async function login(email: string, password: string) {
    const { access_token } = await apiLogin(email, password);
    localStorage.setItem("token", access_token);
    setToken(access_token);
  }

  function logout() {
    localStorage.removeItem("token");
    setToken(null);
    setUser(null);
  }

  async function refreshUser() {
    if (!token) return;
    const fresh = await getMe(token);
    setUser(fresh);
  }

  return createElement(AuthContext.Provider, { value: { token, user, login, logout, refreshUser } }, children);
}

export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
}
