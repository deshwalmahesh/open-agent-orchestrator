import type { Skill } from "@/types";
import { apiFetch } from "./client";

export async function listSkills(token: string): Promise<Skill[]> {
  return apiFetch("/skills", {}, token);
}

export async function createSkill(
  token: string,
  data: { name: string; content: string },
): Promise<Skill> {
  return apiFetch("/skills", { method: "POST", body: JSON.stringify(data) }, token);
}

export async function updateSkill(
  token: string,
  id: string,
  data: { name: string; content: string },
): Promise<Skill> {
  return apiFetch(`/skills/${id}`, { method: "PUT", body: JSON.stringify(data) }, token);
}

export async function deleteSkill(token: string, id: string): Promise<void> {
  return apiFetch(`/skills/${id}`, { method: "DELETE" }, token);
}
