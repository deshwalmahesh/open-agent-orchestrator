import type { Chat, Message, SendMessageResponse } from "@/types";
import { apiFetch } from "./client";

export async function listChats(token: string): Promise<Chat[]> {
  return apiFetch("/chats", {}, token);
}

export async function createChat(
  token: string,
  data: { agent_id: string; title?: string },
): Promise<Chat> {
  return apiFetch("/chats", { method: "POST", body: JSON.stringify({ channel: "web", ...data }) }, token);
}

export async function patchChat(
  token: string,
  id: string,
  data: { agent_id?: string },
): Promise<Chat> {
  return apiFetch(`/chats/${id}`, { method: "PATCH", body: JSON.stringify(data) }, token);
}

export async function deleteChat(token: string, id: string): Promise<void> {
  return apiFetch(`/chats/${id}`, { method: "DELETE" }, token);
}

export async function getMessages(token: string, chatId: string): Promise<Message[]> {
  return apiFetch(`/chats/${chatId}/messages`, {}, token);
}

export interface FileAttachment {
  name: string;
  content_base64: string;
  mime_type: string;
}

export async function sendMessage(
  token: string,
  chatId: string,
  text: string,
  files?: FileAttachment[],
): Promise<SendMessageResponse> {
  return apiFetch(
    `/chats/${chatId}/messages`,
    { method: "POST", body: JSON.stringify({ text, files: files ?? [] }) },
    token,
  );
}
