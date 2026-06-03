import { apiFetch } from "./client";

export interface ProviderInfo {
  id: string;
  label: string;
}

export async function listProviders(token: string): Promise<ProviderInfo[]> {
  return apiFetch("/providers", {}, token);
}
