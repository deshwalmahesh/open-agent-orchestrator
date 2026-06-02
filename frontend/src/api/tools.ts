import { apiFetch } from "./client";

export interface Tool {
  name: string;
  description: string;
}

// GET /tools is public — no token needed
export async function listTools(): Promise<Tool[]> {
  return apiFetch("/tools");
}
