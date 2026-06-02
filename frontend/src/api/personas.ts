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
