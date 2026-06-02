import { apiFetch } from "./client";
import type { MCPServer } from "@/types";

export async function listMCPServers(token: string): Promise<MCPServer[]> {
  return apiFetch("/mcp-servers", {}, token);
}

export async function createMCPServer(
  token: string,
  data: { name: string; url: string; transport?: "http" | "sse"; headers?: Record<string, string> },
): Promise<MCPServer> {
  return apiFetch("/mcp-servers", { method: "POST", body: JSON.stringify(data) }, token);
}

export async function deleteMCPServer(token: string, id: string): Promise<void> {
  return apiFetch(`/mcp-servers/${id}`, { method: "DELETE" }, token);
}

export async function discoverMCPTools(
  token: string,
  serverId: string,
): Promise<Array<{ name: string; description: string }>> {
  return apiFetch(`/mcp-servers/${serverId}/tools`, {}, token);
}
