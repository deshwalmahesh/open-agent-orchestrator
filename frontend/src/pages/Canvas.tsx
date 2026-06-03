import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import AgentCanvas from "@/components/AgentCanvas";
import { getAgent, listAgents, updateAgent, deployAgent } from "@/api/agents";
import { getSlackStatus } from "@/api/slack";
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

  const { data: slackStatus } = useQuery({
    queryKey: ["slack-status"],
    queryFn: () => getSlackStatus(token!),
    enabled: !!token,
    staleTime: 30_000,
  });

  const slackConnected = slackStatus?.connected ?? false;

  // Save persists config. Deploy = save + flip Draft → Deployed (one-time;
  // edits after deploy stay Deployed). Once deployed, the same button just
  // saves — relabelled to make that explicit.
  async function handleDeploy() {
    if (!agent || !token) return;
    setSaveState("saving");
    try {
      await updateAgent(token, agent.id, agent.config);
      if (!agent.deployed_at) {
        await deployAgent(token, agent.id);
      }
      await qc.invalidateQueries({ queryKey: ["agents"] });
      await qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 3000);
    } catch (e: unknown) {
      console.error("Deploy/save failed:", e);
      setSaveState("error");
      setTimeout(() => setSaveState("idle"), 3000);
    }
  }

  if (loadingAgent) return <div className="p-6 text-muted-foreground">Loading canvas…</div>;
  if (error || !agent) return <div className="p-6 text-destructive">Pipeline not found.</div>;

  return (
    <div className="flex flex-col h-full">
      {/* Header — relative so the centered deploy button can be absolute */}
      <div className="relative px-4 py-2.5 border-b flex items-center shrink-0 bg-white">
        {/* Left: nav + name */}
        <div className="flex items-center gap-3 min-w-0">
          <Link to="/agents" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs shrink-0")}>
            ← Pipelines
          </Link>
          <span className="font-semibold text-sm truncate">{agent.name}</span>
          {!agent.deployed_at && (
            <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-300 shrink-0">
              Draft
            </span>
          )}
        </div>

        {/* Center: deploy button — absolutely centered in the header bar */}
        <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2.5">
          {saveState === "saved" && (
            <span className="text-[10px] text-emerald-600 font-medium bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
              ✓ {agent.deployed_at ? "Saved" : "Deployed"}
            </span>
          )}
          {saveState === "error" && (
            <span className="text-[10px] text-red-500 font-medium bg-red-50 border border-red-200 px-2 py-0.5 rounded-full">
              Failed
            </span>
          )}
          <button
            onClick={handleDeploy}
            disabled={saveState === "saving"}
            className={cn(
              "px-6 py-2 rounded-lg text-sm font-bold text-white transition-all",
              "hover:scale-[1.02] active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100",
              agent.deployed_at
                ? "bg-violet-600 hover:bg-violet-700 ring-1 ring-violet-400/50 shadow-sm shadow-violet-300/40"
                : "bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 ring-1 ring-amber-400/50 shadow-sm shadow-amber-300/40",
              saveState === "idle" && "animate-pulse",
            )}
          >
            {saveState === "saving"
              ? (agent.deployed_at ? "Saving…" : "Deploying…")
              : "Update Pipeline"}
          </button>
        </div>

        {/* Right: stats + slack */}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground hidden md:inline">
            {agent.config.tools.length} tools
            {agent.config.mcp_servers.length > 0 && ` · ${agent.config.mcp_servers.length} MCP`}
            {agent.config.subagents.length > 0 && ` · ${agent.config.subagents.length} sub-agents`}
          </span>
          {slackConnected ? (
            <Link
              to="/integrations"
              className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50 transition-colors px-2 py-1 rounded-lg"
              title="Slack connected — manage in Integrations"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              Slack
            </Link>
          ) : (
            <Link
              to="/integrations"
              className="flex items-center gap-1.5 text-xs font-medium text-gray-600 hover:text-[#4A154B] hover:bg-violet-50 px-2.5 py-1 rounded-lg border border-gray-200 hover:border-[#4A154B] transition-colors"
              title="Connect Slack in Integrations"
            >
              Connect Slack
            </Link>
          )}
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1">
        <AgentCanvas agent={agent} allAgents={allAgents} />
      </div>
    </div>
  );
}
