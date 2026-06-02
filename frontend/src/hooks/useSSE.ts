import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import type { RunEvent } from "@/types";

// All named event types the backend emits. Named SSE events don't fire onmessage
// so each type needs its own listener.
const BACKEND_EVENT_TYPES = [
  "run.started", "run.finished", "run.error",
  "node.started", "node.ended",
  "llm.call", "tool.start", "tool.end",
  "agent.message", "agent.delegated", "agent.returned",
  "usage", "guardrail.blocked",
] as const;

export function useSSE(runId: string | null): RunEvent[] {
  const { token } = useAuth();
  const [events, setEvents] = useState<RunEvent[]>([]);

  useEffect(() => {
    if (!runId || !token) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEvents([]);
    const base = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
    const es = new EventSource(`${base}/runs/${runId}/events?token=${token}`);

    for (const type of BACKEND_EVENT_TYPES) {
      es.addEventListener(type, (e) => {
        setEvents((prev) => [
          ...prev,
          { type, data: JSON.parse((e as MessageEvent).data) },
        ]);
      });
    }

    es.onerror = () => es.close();
    return () => es.close();
  }, [runId, token]);

  return events;
}
