import { apiFetch } from "./client";

export interface Tool {
  name: string;          // stable registry key (used in agent config)
  display_name: string;  // human label for UI
  description: string;
}

// GET /tools is public — no token needed
export async function listTools(): Promise<Tool[]> {
  return apiFetch("/tools");
}
