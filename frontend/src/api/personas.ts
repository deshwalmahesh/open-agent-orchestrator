import type { Persona } from "@/types";
import { apiFetch } from "./client";

export async function listPersonas(token: string): Promise<Persona[]> {
  return apiFetch("/personas", {}, token);
}

export async function createPersona(
  token: string,
  data: { name: string; system_prompt: string },
): Promise<Persona> {
  return apiFetch("/personas", { method: "POST", body: JSON.stringify(data) }, token);
}

export async function updatePersona(
  token: string,
  id: string,
  data: { name: string; system_prompt: string },
): Promise<Persona> {
  return apiFetch(`/personas/${id}`, { method: "PUT", body: JSON.stringify(data) }, token);
}

export async function deletePersona(token: string, id: string): Promise<void> {
  return apiFetch(`/personas/${id}`, { method: "DELETE" }, token);
}
