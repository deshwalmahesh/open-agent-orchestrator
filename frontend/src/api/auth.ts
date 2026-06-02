import type { User } from "@/types";
import { apiFetch } from "./client";

export async function login(email: string, password: string): Promise<{ access_token: string }> {
  const body = new URLSearchParams({ username: email, password });
  return apiFetch("/auth/jwt/login", { method: "POST", body });
}

export async function register(email: string, password: string, name: string): Promise<User> {
  return apiFetch("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, name }),
  });
}

export async function getMe(token: string): Promise<User> {
  return apiFetch("/users/me", {}, token);
}

