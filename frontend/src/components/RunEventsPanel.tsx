import { useRef, useEffect } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { RunEvent } from "@/types";

const TYPE_COLOR: Record<string, string> = {
  "run.started": "bg-blue-100 text-blue-800",
  "run.finished": "bg-green-100 text-green-800",
  "run.error": "bg-red-100 text-red-800",
  "tool.start": "bg-yellow-100 text-yellow-800",
  "tool.end": "bg-yellow-100 text-yellow-800",
  "agent.delegated": "bg-purple-100 text-purple-800",
  "agent.returned": "bg-purple-100 text-purple-800",
  "llm.call": "bg-gray-100 text-gray-700",
};

function eventSummary(event: RunEvent): string {
  const d = event.data;
  if (event.type === "run.finished" && d.usage) {
    const u = d.usage as { total_tokens?: number; total_cost?: number };
    return `tokens: ${u.total_tokens ?? "?"} · cost: $${(u.total_cost ?? 0).toFixed(5)}`;
  }
  if (event.type === "tool.start") return `${d.tool ?? d.name ?? ""}`;
  if (event.type === "agent.delegated") return `→ ${d.child ?? ""}`;
  if (event.type === "run.error") return String(d.error ?? "");
  return "";
}

interface Props {
  events: RunEvent[];
  isRunning: boolean;
}

export default function RunEventsPanel({ events, isRunning }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0 && !isRunning) return null;

  return (
    <div className="border-t bg-muted/30">
      <div className="px-4 py-2 flex items-center gap-2 border-b">
        <span className="text-xs font-semibold text-muted-foreground">Run Events</span>
        {isRunning && (
          <span className="text-xs text-blue-600 animate-pulse">● running</span>
        )}
      </div>
      <ScrollArea className="h-40 px-4 py-2">
        <div className="space-y-1 text-xs font-mono">
          {events.map((ev, i) => {
            const colorCls = TYPE_COLOR[ev.type] ?? "bg-gray-100 text-gray-600";
            const summary = eventSummary(ev);
            return (
              <div key={i} className="flex items-start gap-2">
                <span className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${colorCls}`}>
                  {ev.type}
                </span>
                {summary && <span className="text-muted-foreground">{summary}</span>}
              </div>
            );
          })}
        </div>
        <div ref={bottomRef} />
      </ScrollArea>
    </div>
  );
}
