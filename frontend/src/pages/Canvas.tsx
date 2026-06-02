import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { buttonVariants, Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import AgentCanvas from "@/components/AgentCanvas";
import { getAgent, listAgents, updateAgent } from "@/api/agents";
import { useAuth } from "@/hooks/useAuth";

type SaveState = "idle" | "saving" | "saved" | "error";

export default function Canvas() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const qc = useQueryClient();
  const [saveState, setSaveState] = useState<SaveState>("idle");

  const { data: agent, isLoading: loadingAgent, error } = useQuery({
    queryKey: ["agent", id],
    queryFn: () => getAgent(token!, id!),
    enabled: !!token && !!id,
  });

  const { data: allAgents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(token!),
    enabled: !!token,
  });

  async function handleDeploy() {
    if (!agent || !token) return;
    setSaveState("saving");
    try {
      await updateAgent(token, agent.id, agent.config);
      await qc.invalidateQueries({ queryKey: ["agents"] });
      await qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 3000);
    } catch (e) {
      console.error("Deploy failed:", e);
      setSaveState("error");
      setTimeout(() => setSaveState("idle"), 3000);
    }
  }

  if (loadingAgent) return <div className="p-6 text-muted-foreground">Loading canvas…</div>;
  if (error || !agent) return <div className="p-6 text-destructive">Agent not found.</div>;

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b flex items-center gap-3 shrink-0 bg-white">
        <Link to="/agents" className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
          ← Agents
        </Link>
        <span className="font-semibold">{agent.name}</span>
        <span className="text-muted-foreground text-sm">· Supervisor Canvas</span>

        <div className="ml-auto flex items-center gap-3">
          {saveState === "saved" && (
            <span className="text-xs text-emerald-600 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              Deployed
            </span>
          )}
          {saveState === "error" && (
            <span className="text-xs text-red-500 font-medium">Deploy failed</span>
          )}
          <span className="text-xs text-muted-foreground">
            {agent.config.tools.length} tool{agent.config.tools.length !== 1 ? "s" : ""}
            {agent.config.mcp_servers.length > 0 && ` · ${agent.config.mcp_servers.length} MCP`}
            {agent.config.subagents.length > 0 && ` · ${agent.config.subagents.length} agent${agent.config.subagents.length !== 1 ? "s" : ""}`}
          </span>
          <Button
            size="sm"
            onClick={handleDeploy}
            disabled={saveState === "saving"}
            className="bg-violet-600 hover:bg-violet-700 text-white"
          >
            {saveState === "saving" ? "Deploying…" : "🚀 Deploy Agent"}
          </Button>
        </div>
      </div>
      <div className="flex-1">
        <AgentCanvas agent={agent} allAgents={allAgents} />
      </div>
    </div>
  );
}
