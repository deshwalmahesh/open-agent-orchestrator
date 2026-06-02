import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { Agent } from "@/types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * A pipeline (root) = an agent NOT referenced as a sub-agent by any other agent.
 * Sub-agents always have at least one parent in `subagents[]` somewhere.
 */
export function isPipelineRoot(a: Agent, all: Agent[]): boolean {
  return !all.some((other) => other.id !== a.id && other.config.subagents.includes(a.id));
}
