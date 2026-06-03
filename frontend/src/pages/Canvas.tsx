import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { buttonVariants, Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import AgentCanvas from "@/components/AgentCanvas";
import { getAgent, listAgents, updateAgent, deployAgent } from "@/api/agents";
import { getSlackStatus, connectSlack, disconnectSlack } from "@/api/slack";
import { useAuth } from "@/hooks/useAuth";

type SaveState = "idle" | "saving" | "saved" | "error";

export default function Canvas() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const qc = useQueryClient();
  const [saveState, setSaveState] = useState<SaveState>("idle");

  // Slack dialog state
  const [slackOpen, setSlackOpen] = useState(false);
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [slackSaving, setSlackSaving] = useState(false);
  const [slackError, setSlackError] = useState("");

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

  const { data: slackStatus, refetch: refetchSlack } = useQuery({
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

  async function handleConnectSlack() {
    if (!botToken.trim() || !appToken.trim()) return;
    setSlackSaving(true);
    setSlackError("");
    try {
      // Backend atomically: saves tokens, swaps the single Slack ChannelBinding to this pipeline,
      // and (re)starts the adapter. No need for a separate updateAgent call here.
      await connectSlack(token!, { bot_token: botToken.trim(), app_token: appToken.trim(), agent_id: id });
      await qc.invalidateQueries({ queryKey: ["agents"] });
      await qc.invalidateQueries({ queryKey: ["agent", id] });
      await refetchSlack();
      setSlackOpen(false);
      setBotToken("");
      setAppToken("");
    } catch (e: unknown) {
      setSlackError(e instanceof Error ? e.message : "Failed to connect");
    } finally {
      setSlackSaving(false);
    }
  }

  async function handleDisconnectSlack() {
    try {
      await disconnectSlack(token!);
      await refetchSlack();
    } catch (e) {
      console.error("Disconnect failed:", e);
    }
  }

  if (loadingAgent) return <div className="p-6 text-muted-foreground">Loading canvas…</div>;
  if (error || !agent) return <div className="p-6 text-destructive">Pipeline not found.</div>;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-2.5 border-b flex items-center gap-3 shrink-0 bg-white">
        <Link to="/agents" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-xs")}>
          ← Pipelines
        </Link>
        <span className="font-semibold text-sm">{agent.name}</span>
        {!agent.deployed_at && (
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-300">
            Draft
          </span>
        )}
        <span className="text-muted-foreground text-xs hidden sm:inline">Pipeline Canvas</span>

        <div className="ml-auto flex items-center gap-2">
          {/* Stats */}
          <span className="text-xs text-muted-foreground hidden md:inline">
            {agent.config.tools.length} tools
            {agent.config.mcp_servers.length > 0 && ` · ${agent.config.mcp_servers.length} MCP`}
            {agent.config.subagents.length > 0 && ` · ${agent.config.subagents.length} sub-agents`}
          </span>

          {/* Slack button — visible once agent exists */}
          {slackConnected ? (
            <button
              onClick={handleDisconnectSlack}
              className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 hover:text-red-500 transition-colors px-2 py-1 rounded-lg hover:bg-red-50"
              title="Slack connected — click to disconnect"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              Slack
            </button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="text-xs border-gray-200 text-gray-600 hover:border-[#4A154B] hover:text-[#4A154B]"
              onClick={() => setSlackOpen(true)}
            >
              Connect Slack
            </Button>
          )}

          {/* Deploy (Draft) / Save (Deployed) — same button, role depends on state */}
          {saveState === "saved" && (
            <span className="text-xs text-emerald-600 font-medium flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
              {agent.deployed_at ? "Saved" : "Deployed"}
            </span>
          )}
          {saveState === "error" && <span className="text-xs text-red-500 font-medium">Failed</span>}
          <Button
            size="sm"
            onClick={handleDeploy}
            disabled={saveState === "saving"}
            className={cn(
              "text-white text-xs",
              agent.deployed_at ? "bg-violet-600 hover:bg-violet-700" : "bg-amber-500 hover:bg-amber-600",
            )}
          >
            {saveState === "saving"
              ? (agent.deployed_at ? "Saving…" : "Deploying…")
              : (agent.deployed_at ? "Save" : "Deploy")}
          </Button>
        </div>
      </div>

      {/* Canvas */}
      <div className="flex-1">
        <AgentCanvas agent={agent} allAgents={allAgents} />
      </div>

      {/* Slack connect dialog */}
      {slackOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
            {/* Header */}
            <div className="px-6 py-5 bg-[#4A154B]">
              <div className="flex items-center gap-3">
                <svg viewBox="0 0 54 54" className="w-8 h-8 shrink-0" fill="none">
                  <path d="M19.712.133a5.381 5.381 0 0 0-5.376 5.387 5.381 5.381 0 0 0 5.376 5.386h5.376V5.52A5.381 5.381 0 0 0 19.712.133m0 14.365H5.376A5.381 5.381 0 0 0 0 19.884a5.381 5.381 0 0 0 5.376 5.387h14.336a5.381 5.381 0 0 0 5.376-5.387 5.381 5.381 0 0 0-5.376-5.386" fill="#36C5F0"/>
                  <path d="M53.76 19.884a5.381 5.381 0 0 0-5.376-5.386 5.381 5.381 0 0 0-5.376 5.386v5.387h5.376a5.381 5.381 0 0 0 5.376-5.387m-14.336 0V5.52A5.381 5.381 0 0 0 34.048.133a5.381 5.381 0 0 0-5.376 5.387v14.364a5.381 5.381 0 0 0 5.376 5.387 5.381 5.381 0 0 0 5.376-5.387" fill="#2EB67D"/>
                  <path d="M34.048 54a5.381 5.381 0 0 0 5.376-5.387 5.381 5.381 0 0 0-5.376-5.386h-5.376v5.386A5.381 5.381 0 0 0 34.048 54m0-14.365h14.336a5.381 5.381 0 0 0 5.376-5.386 5.381 5.381 0 0 0-5.376-5.387H34.048a5.381 5.381 0 0 0-5.376 5.387 5.381 5.381 0 0 0 5.376 5.386" fill="#ECB22E"/>
                  <path d="M0 34.249a5.381 5.381 0 0 0 5.376 5.386 5.381 5.381 0 0 0 5.376-5.386v-5.387H5.376A5.381 5.381 0 0 0 0 34.249m14.336 0v14.364A5.381 5.381 0 0 0 19.712 54a5.381 5.381 0 0 0 5.376-5.387V34.249a5.381 5.381 0 0 0-5.376-5.387 5.381 5.381 0 0 0-5.376 5.387" fill="#E01E5A"/>
                </svg>
                <div>
                  <p className="font-bold text-white text-base">Connect to Slack</p>
                  <p className="text-purple-200 text-xs">Route agent replies through your Slack workspace</p>
                </div>
              </div>
            </div>

            <div className="px-6 py-5 space-y-4">
              <div className="text-xs text-muted-foreground bg-gray-50 rounded-lg p-3 space-y-1">
                <p className="font-semibold text-gray-700">How to get your tokens:</p>
                <p>1. Create a Slack App at <span className="font-mono">api.slack.com/apps</span></p>
                <p>2. Enable Socket Mode → copy the <span className="font-mono">xapp-</span> App Token</p>
                <p>3. Install to workspace → copy the <span className="font-mono">xoxb-</span> Bot Token</p>
                <p>4. Set your Slack user ID via <span className="font-mono">PATCH /users/me</span> so DMs route to you</p>
              </div>

              <div className="space-y-3">
                <div>
                  <Label className="text-xs font-semibold text-gray-700">Bot Token (xoxb-…) *</Label>
                  <Input
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="xoxb-..."
                    type="password"
                    className="mt-1.5 font-mono text-sm"
                    autoFocus
                  />
                </div>
                <div>
                  <Label className="text-xs font-semibold text-gray-700">App-Level Token (xapp-…) *</Label>
                  <Input
                    value={appToken}
                    onChange={(e) => setAppToken(e.target.value)}
                    placeholder="xapp-..."
                    type="password"
                    className="mt-1.5 font-mono text-sm"
                  />
                </div>
              </div>

              {slackError && (
                <p className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-lg p-2">{slackError}</p>
              )}

              <div className="flex gap-2 pt-1">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => { setSlackOpen(false); setSlackError(""); }}
                  className="flex-none"
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleConnectSlack}
                  disabled={slackSaving || !botToken.trim() || !appToken.trim()}
                  className="flex-1 bg-[#4A154B] hover:bg-[#3d1140] text-white"
                >
                  {slackSaving ? "Connecting…" : "Connect"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
