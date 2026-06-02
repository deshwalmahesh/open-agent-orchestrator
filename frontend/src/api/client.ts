const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function apiFetch<T>(
  path: string,
  opts: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (!(opts.body instanceof URLSearchParams)) {
    headers["Content-Type"] = "application/json";
  }
  if (opts.headers) Object.assign(headers, opts.headers);
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
