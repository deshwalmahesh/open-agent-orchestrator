import { apiFetch } from "./client";
import type { ToolConfig } from "@/types";

export async function listToolConfigs(token: string): Promise<ToolConfig[]> {
  return apiFetch("/tool-configs", {}, token);
}

export async function upsertToolConfig(
  token: string,
  toolName: string,
  config: Record<string, string>,
): Promise<ToolConfig> {
  return apiFetch(
    `/tool-configs/${encodeURIComponent(toolName)}`,
    { method: "PUT", body: JSON.stringify({ config }) },
    token,
  );
}

export async function deleteToolConfig(token: string, toolName: string): Promise<void> {
  return apiFetch(`/tool-configs/${encodeURIComponent(toolName)}`, { method: "DELETE" }, token);
}

export async function validateToolConfig(
  token: string,
  toolName: string,
  config: Record<string, string>,
): Promise<{ ok: boolean; error?: string }> {
  return apiFetch(
    `/tool-configs/${encodeURIComponent(toolName)}/validate`,
    { method: "POST", body: JSON.stringify({ config }) },
    token,
  );
}
