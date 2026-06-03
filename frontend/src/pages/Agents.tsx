import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn, isPipelineRoot } from "@/lib/utils";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import AgentForm from "@/components/AgentForm";
import { listAgents, createAgent, updateAgent, deleteAgent } from "@/api/agents";
import { getSlackStatus, setSlackActive } from "@/api/slack";
import { listPersonas } from "@/api/personas";
import { useAuth } from "@/hooks/useAuth";
import { getLLMDefaults } from "@/lib/llm-defaults";
import type { Agent, AgentConfig } from "@/types";

const DEFAULT_SUPERVISOR_FALLBACK = "You are a helpful assistant.";

function buildDefaultConfig(name: string, supervisorPrompt: string): AgentConfig {
  const llm = getLLMDefaults();
  return {
    name,
    role: "supervisor",
    description: null,
    system_prompt: supervisorPrompt,
    llm: { base_url: llm.base_url, api_key: llm.api_key, model: llm.model, temperature: llm.temperature, max_tokens: llm.max_tokens, timeout_s: 30.0 },
    tools: [],
    memory: { type: "summary", window: 10, summary_threshold: 20 },
    limits: { max_steps: 8 },
    subagents: [],
    skills: [],
    mcp_servers: [],
    channels: [],
    metadata: {},
  };
}

export default function Agents() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const { data: agents = [], isLoading, error } = useQuery({
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
  const slackActiveId = slackStatus?.active_agent_id ?? null;

  const { data: personas = [] } = useQuery({
    queryKey: ["personas"],
    queryFn: () => listPersonas(token!),
    enabled: !!token,
    staleTime: 60_000,
  });

  // Pipelines page shows only root pipelines, not sub-agents.
  // Sub-agents are edited inside the canvas (right-click → properties).
  const pipelines = agents.filter((a) => isPipelineRoot(a, agents));

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteAgent(token!, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
    onError: (err) => console.error("Delete agent failed:", err),
  });

  const setActiveMut = useMutation({
    mutationFn: (id: string) => setSlackActive(token!, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["slack-status"] }),
    onError: (err) => console.error("Set Slack-active failed:", err),
  });

  // No dialog — click → create with defaults → open canvas immediately
  async function handleNewAgent() {
    if (creating) return;
    setCreating(true);
    const autoName = pipelines.length === 0 ? "My Pipeline" : `Pipeline ${pipelines.length + 1}`;
    const supervisorPrompt =
      personas.find((p) => p.name === "Default - Supervisor")?.system_prompt
      ?? DEFAULT_SUPERVISOR_FALLBACK;
    try {
      const agent = await createAgent(token!, buildDefaultConfig(autoName, supervisorPrompt));
      await qc.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${agent.id}/canvas`);
    } catch (err) {
      console.error("Create agent failed:", err);
      setCreating(false);
    }
  }

  async function handleEditSave(config: AgentConfig) {
    if (!editing) return;
    setSubmitting(true);
    try {
      await updateAgent(token!, editing.id, config);
      await qc.invalidateQueries({ queryKey: ["agents"] });
      setEditing(null);
    } catch (err) {
      console.error("Save agent failed:", err);
    } finally {
      setSubmitting(false);
    }
  }

  if (isLoading) return <div className="p-6 text-muted-foreground">Loading agents…</div>;
  if (error) return <div className="p-6 text-destructive">Failed to load agents.</div>;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Pipelines</h1>
        <Button
          onClick={handleNewAgent}
          disabled={creating}
          className="bg-violet-600 hover:bg-violet-700 text-white"
        >
          {creating ? "Creating…" : "+ New Pipeline"}
        </Button>
      </div>

      {pipelines.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-gray-700 mb-1">No pipelines yet</p>
          <p className="text-sm text-muted-foreground mb-6">Create your first pipeline — you'll design it visually on the canvas.</p>
          <Button
            onClick={handleNewAgent}
            disabled={creating}
            className="bg-violet-600 hover:bg-violet-700 text-white"
          >
            {creating ? "Creating…" : "+ Create your first pipeline"}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {pipelines.map((agent) => (
            <div
              key={agent.id}
              className="group flex items-center gap-3 border rounded-xl p-4 hover:bg-violet-50/40 hover:border-violet-200 transition-colors"
            >
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center text-white font-bold text-sm shrink-0">
                {agent.name.slice(0, 2).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-gray-900">{agent.name}</span>
                  <Badge variant="secondary" className="text-xs font-normal">{agent.config.role}</Badge>
                  {agent.id === slackActiveId && (
                    <Badge className="text-xs font-medium bg-emerald-100 text-emerald-700 hover:bg-emerald-100 border border-emerald-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block mr-1.5" />
                      Slack active
                    </Badge>
                  )}
                  {agent.config.subagents.length > 0 && (
                    <Badge variant="outline" className="text-xs font-normal text-blue-600 border-blue-200">
                      {agent.config.subagents.length} sub-agent{agent.config.subagents.length > 1 ? "s" : ""}
                    </Badge>
                  )}
                  {agent.config.tools.length > 0 && (
                    <Badge variant="outline" className="text-xs font-normal text-emerald-600 border-emerald-200">
                      {agent.config.tools.length} tool{agent.config.tools.length > 1 ? "s" : ""}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-3 mt-1">
                  {agent.config.llm.model && (
                    <span className="text-xs text-muted-foreground font-mono">{agent.config.llm.model}</span>
                  )}
                  {agent.config.description && (
                    <span className="text-xs text-muted-foreground truncate">{agent.config.description}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {agent.id !== slackActiveId && slackStatus?.connected && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-xs text-emerald-700 hover:text-emerald-800 hover:bg-emerald-50 opacity-0 group-hover:opacity-100 transition-opacity"
                    disabled={setActiveMut.isPending}
                    onClick={() => setActiveMut.mutate(agent.id)}
                  >
                    {setActiveMut.isPending && setActiveMut.variables === agent.id ? "Switching…" : "Make Slack-active"}
                  </Button>
                )}
                <Link
                  to={`/agents/${agent.id}/canvas`}
                  className={cn(buttonVariants({ variant: "default", size: "sm" }), "bg-violet-600 hover:bg-violet-700 text-white text-xs")}
                >
                  Open Canvas
                </Link>
                <Button variant="ghost" size="sm" className="text-xs" onClick={() => setEditing(agent)}>
                  Edit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive text-xs"
                  onClick={() => { if (confirm(`Delete pipeline "${agent.name}"?`)) deleteMut.mutate(agent.id); }}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Full edit dialog — existing agents only */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent className="max-w-xl max-h-[90vh] overflow-hidden flex flex-col p-0">
          {/* Gradient header */}
          <div className="bg-gradient-to-br from-violet-600 to-purple-700 px-6 py-5 shrink-0">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-white/20 flex items-center justify-center text-white font-bold text-sm">
                {(editing as Agent)?.name?.slice(0, 2).toUpperCase()}
              </div>
              <div>
                <p className="font-bold text-white text-base">{(editing as Agent)?.name}</p>
                <p className="text-violet-200 text-xs">{(editing as Agent)?.config.role}</p>
              </div>
            </div>
          </div>
          <div className="overflow-y-auto p-6">
            {editing && (
              <AgentForm
                agent={editing as Agent}
                allAgents={agents}
                onSubmit={handleEditSave}
                onCancel={() => setEditing(null)}
                submitting={submitting}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
