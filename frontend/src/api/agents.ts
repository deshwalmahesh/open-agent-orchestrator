import type { Agent, AgentConfig } from "@/types";
import { apiFetch } from "./client";

export async function listAgents(token: string): Promise<Agent[]> {
  return apiFetch("/agents", {}, token);
}

export async function getAgent(token: string, id: string): Promise<Agent> {
  return apiFetch(`/agents/${id}`, {}, token);
}

export async function createAgent(token: string, config: AgentConfig): Promise<Agent> {
  return apiFetch("/agents", { method: "POST", body: JSON.stringify(config) }, token);
}

export async function updateAgent(token: string, id: string, config: AgentConfig): Promise<Agent> {
  return apiFetch(`/agents/${id}`, { method: "PUT", body: JSON.stringify(config) }, token);
}

export async function deleteAgent(token: string, id: string): Promise<void> {
  return apiFetch(`/agents/${id}`, { method: "DELETE" }, token);
}

export async function deployAgent(token: string, id: string): Promise<Agent> {
  return apiFetch(`/agents/${id}/deploy`, { method: "POST" }, token);
}
