import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/hooks/useAuth";
import { cn, isPipelineRoot } from "@/lib/utils";
import { connectSlack, disconnectSlack, getSlackStatus, setSlackActive } from "@/api/slack";
import { listAgents } from "@/api/agents";

// Integrations page — one card per external system. Today: Slack.
export default function IntegrationsPage() {
  return (
    <div className="p-6 max-w-3xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold">Integrations</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Connect external systems (Slack, …) to your deployed pipelines.
        </p>
      </div>
      <SlackCard />
    </div>
  );
}

function SlackCard() {
  const { token } = useAuth();
  const qc = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["slack-status"],
    queryFn: () => getSlackStatus(token!),
    enabled: !!token,
    staleTime: 15_000,
  });

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(token!),
    enabled: !!token,
  });

  // Slack-active pipeline must be a deployed root pipeline.
  const deployedPipelines = agents.filter((a) => isPipelineRoot(a, agents) && !!a.deployed_at);
  const activeAgent = agents.find((a) => a.id === status?.active_agent_id);

  // Edit mode is implicit: if connected, we show summary; clicking Edit Tokens
  // flips to the form so the user can rotate keys without disconnecting first.
  const [editing, setEditing] = useState(false);
  const [botToken, setBotToken] = useState("");
  const [appToken, setAppToken] = useState("");
  const [pickedAgent, setPickedAgent] = useState("");

  // When the status flips to "not connected", auto-open the form.
  // When connected and no agent is selected, default the picker to the active one.
  useEffect(() => {
    if (status && !status.connected) setEditing(true);
    if (status?.active_agent_id) setPickedAgent(status.active_agent_id);
  }, [status]);

  const connectMut = useMutation({
    mutationFn: () =>
      connectSlack(token!, {
        bot_token: botToken,
        app_token: appToken,
        agent_id: pickedAgent || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["slack-status"] });
      setBotToken("");
      setAppToken("");
      setEditing(false);
    },
    onError: (err: Error) => alert(`Connect failed: ${err.message}`),
  });

  const disconnectMut = useMutation({
    mutationFn: () => disconnectSlack(token!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["slack-status"] });
      setEditing(true);
    },
    onError: (err: Error) => alert(`Disconnect failed: ${err.message}`),
  });

  const setActiveMut = useMutation({
    mutationFn: (agentId: string) => setSlackActive(token!, agentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["slack-status"] }),
    onError: (err: Error) => alert(`Switch failed: ${err.message}`),
  });

  const connected = !!status?.connected;
  const canSubmit = botToken.trim() && appToken.trim() && !connectMut.isPending;

  return (
    <div className="border rounded-2xl bg-white overflow-hidden">
      {/* Header */}
      <div className="p-5 flex items-center gap-3">
        <div className="size-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center text-white text-base font-bold">
          S
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="font-semibold text-gray-900">Slack</h2>
            <Badge
              className={cn(
                "text-xs font-medium border",
                connected
                  ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-100 border-emerald-200"
                  : "bg-zinc-100 text-zinc-600 hover:bg-zinc-100 border-zinc-200",
              )}
            >
              <span
                className={cn(
                  "w-1.5 h-1.5 rounded-full inline-block mr-1.5",
                  connected ? "bg-emerald-500" : "bg-zinc-400",
                )}
              />
              {connected ? "Connected" : "Not connected"}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Reply to Slack DMs from one of your deployed pipelines. Socket Mode — no public URL needed.
          </p>
        </div>
        {connected && !editing && (
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
            Edit Tokens
          </Button>
        )}
      </div>

      <Separator />

      {/* Body */}
      <div className="p-5 space-y-5">
        {connected && !editing && (
          <div className="space-y-4">
            <div>
              <Label className="text-xs font-semibold text-gray-600">Active Pipeline</Label>
              <div className="mt-1.5">
                {deployedPipelines.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic">No deployed pipelines. Deploy one on the Pipelines page.</p>
                ) : (
                  <Select
                    value={status?.active_agent_id ?? ""}
                    onValueChange={(v: string | null) => v && setActiveMut.mutate(v)}
                  >
                    <SelectTrigger><SelectValue placeholder="Select pipeline" /></SelectTrigger>
                    <SelectContent>
                      {deployedPipelines.map((a) => (
                        <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
              {activeAgent && (
                <p className="text-[11px] text-muted-foreground mt-1">
                  DMs to your Slack bot are routed to <span className="font-medium text-gray-700">{activeAgent.name}</span>.
                </p>
              )}
            </div>

            <div className="flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                disabled={disconnectMut.isPending}
                onClick={() => {
                  if (confirm("Disconnect Slack? The bot will stop responding until you reconnect.")) {
                    disconnectMut.mutate();
                  }
                }}
              >
                {disconnectMut.isPending ? "Disconnecting…" : "Disconnect"}
              </Button>
            </div>
          </div>
        )}

        {(!connected || editing) && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Bot Token (xoxb-…) *</Label>
              <Input
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                type="password"
                placeholder={connected ? "•••• (saved) — enter new token to replace" : "xoxb-…"}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">App-Level Token (xapp-…) *</Label>
              <Input
                value={appToken}
                onChange={(e) => setAppToken(e.target.value)}
                type="password"
                placeholder={connected ? "•••• (saved) — enter new token to replace" : "xapp-…"}
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-muted-foreground">
                Generate at <span className="font-mono">api.slack.com/apps</span> → your app → Basic Information → App-Level Tokens. Needs <code className="bg-zinc-100 px-1 rounded">connections:write</code>.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Active Pipeline (optional)</Label>
              {deployedPipelines.length === 0 ? (
                <p className="text-xs text-muted-foreground italic">No deployed pipelines yet — connect now, pick one later.</p>
              ) : (
                <Select value={pickedAgent} onValueChange={(v: string | null) => setPickedAgent(v ?? "")}>
                  <SelectTrigger><SelectValue placeholder="Bind a pipeline now (optional)" /></SelectTrigger>
                  <SelectContent>
                    {deployedPipelines.map((a) => (
                      <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <p className="text-[11px] text-muted-foreground">
                You can change which pipeline owns the Slack binding any time.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-1">
              {connected && (
                <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setBotToken(""); setAppToken(""); }}>
                  Cancel
                </Button>
              )}
              <Button
                size="sm"
                className="bg-violet-600 hover:bg-violet-700 text-white"
                disabled={!canSubmit}
                onClick={() => connectMut.mutate()}
              >
                {connectMut.isPending ? "Saving…" : connected ? "Save & Restart Bot" : "Connect Slack"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
