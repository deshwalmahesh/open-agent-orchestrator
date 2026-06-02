import { apiFetch } from "./client";

export interface SlackStatus {
  connected: boolean;
  active_agent_id: string | null;
}

export async function getSlackStatus(token: string): Promise<SlackStatus> {
  return apiFetch("/slack/status", {}, token);
}

export async function connectSlack(
  token: string,
  data: { bot_token: string; app_token: string; agent_id?: string },
): Promise<SlackStatus> {
  return apiFetch("/slack/connect", { method: "POST", body: JSON.stringify(data) }, token);
}

export async function disconnectSlack(token: string): Promise<{ connected: boolean }> {
  return apiFetch("/slack/disconnect", { method: "POST" }, token);
}
