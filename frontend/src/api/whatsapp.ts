import { apiFetch } from "./client";

export interface WhatsAppStatus {
  connected: boolean;
  active_agent_id: string | null;
  webhook_url: string | null;
  from_number: string | null;
}

export async function getWhatsAppStatus(token: string): Promise<WhatsAppStatus> {
  return apiFetch("/whatsapp/status", {}, token);
}

export async function connectWhatsApp(
  token: string,
  data: {
    account_sid: string;
    auth_token: string;
    from_number: string;
    webhook_base_url?: string;
    agent_id?: string;
  },
): Promise<WhatsAppStatus> {
  return apiFetch("/whatsapp/connect", { method: "POST", body: JSON.stringify(data) }, token);
}

export async function disconnectWhatsApp(token: string): Promise<{ connected: boolean }> {
  return apiFetch("/whatsapp/disconnect", { method: "POST" }, token);
}

export async function setWhatsAppActive(
  token: string,
  agentId: string,
): Promise<{ active_agent_id: string | null }> {
  return apiFetch(
    "/whatsapp/active",
    { method: "POST", body: JSON.stringify({ agent_id: agentId }) },
    token,
  );
}
