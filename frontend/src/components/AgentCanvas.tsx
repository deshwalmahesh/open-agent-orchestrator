import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  type NodeTypes,
  type NodeProps,
  type Node,
  type Edge,
  Handle,
  Position,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import AgentForm from "@/components/AgentForm";
import { updateAgent, createAgent as apiCreateAgent, deleteAgent as apiDeleteAgent } from "@/api/agents";
import { listTools } from "@/api/tools";
import { listMCPServers, discoverMCPTools, createMCPServer, deleteMCPServer } from "@/api/mcp-servers";
import { listToolConfigs, upsertToolConfig, validateToolConfig } from "@/api/tool-configs";
import { listPersonas } from "@/api/personas";
import PersonaPopup from "@/components/PersonaPopup";
import { useAuth } from "@/hooks/useAuth";
import { cn, isPipelineRoot } from "@/lib/utils";
import { saveLLMDefaults } from "@/lib/llm-defaults";
import type { Agent, AgentConfig, MCPServer } from "@/types";
import "@xyflow/react/dist/style.css";

// ─── Tool credential fields ───────────────────────────────────────────────────
const TOOL_FIELDS: Record<string, Array<{ key: string; label: string; placeholder: string; required: boolean }>> = {
  web_search: [{ key: "api_key", label: "Tavily API Key", placeholder: "tvly-xxxxxxxxxxxxxxxx", required: true }],
};

const PROVIDERS = [
  { label: "OpenAI",       url: "https://api.openai.com/v1" },
  { label: "Anthropic",    url: "https://api.anthropic.com/v1" },
  { label: "vLLM (local)", url: "http://localhost:8000/v1" },
  { label: "LiteLLM",     url: "http://localhost:4000" },
  { label: "Custom",       url: "" },
];

// ─── State types ──────────────────────────────────────────────────────────────
type ToolConfigDlg = {
  toolName: string;
  sourceAgentId: string;
  configValues: Record<string, string>;
  testState: "idle" | "testing" | "ok" | "fail";
  testError?: string;
};

type CreateAgentForm = {
  name: string;
  provider: string;
  base_url: string;
  api_key: string;
  model: string;
  personaId: string;
};

const DEFAULT_AGENT_FORM: CreateAgentForm = {
  name: "",
  provider: "OpenAI",
  base_url: "https://api.openai.com/v1",
  api_key: "",
  model: "",
  personaId: "",
};

// ─── Layout: 3-tier waterfall (top=supervisor, mid=internal, bottom=external) ──
// Visual hierarchy: main agent is the largest box, sub-agent mid, tool/mcp
// smallest. Sizes match the widths used in node JSX below. Heights stay
// uniform per row to keep dagre's tree clean.
function nodeWidth(type?: string): number {
  if (type === "main-agent") return 300;
  if (type === "sub-agent") return 220;
  return 132;  // tool, mcp
}

function nodeHeight(type?: string): number {
  if (type === "main-agent") return 100;
  if (type === "sub-agent") return 84;
  return 64;
}

function layoutNodes(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", ranksep: 90, nodesep: 28 });
  nodes.forEach((n) => g.setNode(n.id, { width: nodeWidth(n.type), height: nodeHeight(n.type) }));
  const internal = edges.filter((e) => !e.id.includes("mcp::"));
  const external = edges.filter((e) => e.id.includes("mcp::"));
  [...internal, ...external].forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const laidOut = nodes.map((n) => {
    const pos = g.node(n.id);
    const w = nodeWidth(n.type);
    const h = nodeHeight(n.type);
    return { ...n, position: { x: pos.x - w / 2, y: pos.y - h / 2 } };
  });

  const root = laidOut.find((n) => n.type === "main-agent");
  if (!root) return laidOut;
  const dx = -root.position.x;
  const dy = -root.position.y;
  return laidOut.map((n) => ({ ...n, position: { x: n.position.x + dx, y: n.position.y + dy } }));
}

// ─── Invisible handle ─────────────────────────────────────────────────────────
function H({ type, pos }: { type: "source" | "target"; pos: Position }) {
  return (
    <Handle
      type={type}
      position={pos}
      style={{ opacity: 0, width: 4, height: 4, background: "transparent", border: "none" }}
    />
  );
}

// ─── Canvas context ───────────────────────────────────────────────────────────
type PropsTarget =
  | { kind: "root-agent"; agentId: string }
  | { kind: "sub-agent"; agentId: string; parentAgentId: string }
  | { kind: "tool"; toolName: string; ownerAgentId: string; description: string }
  | { kind: "mcp"; serverId: string; serverName: string; ownerAgentId: string };

interface CanvasCtx {
  onAdd: (sourceAgentId: string, isRoot: boolean) => void;
  onProps: (t: PropsTarget) => void;
  onRemoveTool: (toolName: string, ownerAgentId: string) => void;
  onRemoveAgent: (subAgentId: string, parentAgentId: string) => void;
  onRemoveMCP: (serverId: string, parentAgentId: string) => void;
}

const Ctx = createContext<CanvasCtx | null>(null);
function useCtx(): CanvasCtx { return useContext(Ctx)!; }

// ─── Main agent node (largest, saturated violet, strong shadow) ────────────────
// Hierarchy: this is the largest and most visually heavy node on the canvas.
// Big serif-weighted title, 3px ring on selection, deep shadow at rest.
function MainNode({ id, data, selected }: NodeProps) {
  const { onAdd, onProps } = useCtx();
  return (
    <div
      className={cn(
        "group relative rounded-2xl border bg-white cursor-pointer transition-all duration-200 w-[300px]",
        selected
          ? "border-violet-500 ring-4 ring-violet-100 shadow-xl shadow-violet-200/60 -translate-y-0.5"
          : "border-violet-300 shadow-md shadow-violet-100/60 hover:shadow-lg hover:shadow-violet-200/60 hover:-translate-y-0.5",
      )}
      onDoubleClick={(e) => { e.stopPropagation(); onProps({ kind: "root-agent", agentId: id }); }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onProps({ kind: "root-agent", agentId: id }); }}
    >
      <div className="absolute inset-y-0 left-0 w-2 rounded-l-2xl bg-gradient-to-b from-violet-500 to-violet-700" />
      <div className="pl-5 pr-4 py-4">
        <div className="flex items-center gap-1.5 mb-1.5">
          <span className="w-1 h-1 rounded-full bg-violet-500" />
          <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-violet-600">Supervisor</p>
        </div>
        <p className="font-semibold text-base text-zinc-900 truncate leading-tight">{data.label as string}</p>
        {(data.model as string) && (
          <p className="text-[10px] text-zinc-400 truncate mt-1.5 font-mono">{data.model as string}</p>
        )}
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onAdd(id, true); }}
        className="nodrag nopan absolute -bottom-4 left-1/2 -translate-x-1/2 size-9 rounded-full bg-violet-600 hover:bg-violet-700 text-white text-lg font-medium flex items-center justify-center shadow-lg shadow-violet-300/60 transition-all opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100"
        title="Connect tool, MCP, or sub-agent"
      >+</button>
      <H type="source" pos={Position.Bottom} />
    </div>
  );
}

// ─── Sub-agent node (medium, same family lighter — violet-400) ────────────────
// Smaller than main, lighter weight. Same color family signals "same kind of
// thing, lower rank" rather than introducing a new hue.
function SubAgentNode({ id, data, selected }: NodeProps) {
  const { onAdd, onProps, onRemoveAgent } = useCtx();
  return (
    <div
      className={cn(
        "group relative rounded-xl border bg-white cursor-pointer transition-all duration-150 w-[220px]",
        selected
          ? "border-violet-400 ring-2 ring-violet-100 shadow-md shadow-violet-100/60"
          : "border-violet-200 shadow-sm hover:border-violet-300 hover:shadow",
      )}
      onDoubleClick={(e) => { e.stopPropagation(); onProps({ kind: "sub-agent", agentId: id, parentAgentId: data.parentAgentId as string }); }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onProps({ kind: "sub-agent", agentId: id, parentAgentId: data.parentAgentId as string }); }}
    >
      <div className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-violet-300" />
      <div className="pl-4 pr-3 py-3">
        <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-violet-400 mb-1">Sub-Agent</p>
        <p className="font-medium text-sm text-zinc-800 truncate leading-tight">{data.label as string}</p>
        {(data.role as string) && (
          <p className="text-[10px] text-zinc-400 truncate mt-0.5">{data.role as string}</p>
        )}
      </div>
      {selected && (
        <div className="absolute -bottom-8 left-0 right-0 flex justify-center z-10">
          <button
            onClick={(e) => { e.stopPropagation(); onRemoveAgent(id, data.parentAgentId as string); }}
            className="nodrag nopan bg-white border border-red-200 text-red-500 rounded-md px-3 py-1 text-xs hover:bg-red-50 shadow-sm font-medium"
          >Disconnect</button>
        </div>
      )}
      <button
        onClick={(e) => { e.stopPropagation(); onAdd(id, false); }}
        className="nodrag nopan absolute -bottom-3.5 left-1/2 -translate-x-1/2 size-7 rounded-full bg-violet-400 hover:bg-violet-500 text-white text-base font-medium flex items-center justify-center shadow-md transition-all opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100"
        title="Add tool"
      >+</button>
      <H type="target" pos={Position.Top} />
      <H type="source" pos={Position.Bottom} />
    </div>
  );
}

// ─── Tool node (smallest, neutral zinc with a thin emerald accent dot) ────────
// Tools are utilities, not agents — they get the lightest visual weight.
// Neutral background with one tiny accent dot keeps the canvas calm.
function ToolNode({ data, selected }: NodeProps) {
  const { onProps, onRemoveTool } = useCtx();
  const toolName = data.toolName as string;
  const displayName = (data.displayName as string) || toolName;
  return (
    <div
      className={cn(
        "group relative rounded-lg border bg-white cursor-pointer transition-all duration-150 w-[132px]",
        selected
          ? "border-emerald-400 ring-2 ring-emerald-100 shadow"
          : "border-zinc-200 hover:border-emerald-300 hover:shadow-sm",
      )}
      onDoubleClick={(e) => { e.stopPropagation(); onProps({ kind: "tool", toolName, ownerAgentId: data.ownerAgentId as string, description: data.description as string }); }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onProps({ kind: "tool", toolName, ownerAgentId: data.ownerAgentId as string, description: data.description as string }); }}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="w-1 h-1 rounded-full bg-emerald-500" />
          <p className="text-[8px] font-bold uppercase tracking-[0.15em] text-zinc-400">Tool</p>
        </div>
        <p className="font-medium text-xs text-zinc-800 truncate leading-tight">{displayName}</p>
      </div>
      {selected && (
        <div className="absolute -bottom-7 left-0 right-0 flex justify-center z-10">
          <button
            onClick={(e) => { e.stopPropagation(); onRemoveTool(toolName, data.ownerAgentId as string); }}
            className="nodrag nopan bg-white border border-red-200 text-red-500 rounded-md px-2 py-0.5 text-[11px] hover:bg-red-50 shadow-sm font-medium"
          >Remove</button>
        </div>
      )}
      <H type="target" pos={Position.Top} />
    </div>
  );
}

// ─── MCP node (smallest, neutral with amber dashed-accent) ─────────────────────
function MCPNode({ data, selected }: NodeProps) {
  const { onProps, onRemoveMCP } = useCtx();
  const serverId = data.serverId as string;
  const tools = data.tools as string[];
  return (
    <div
      className={cn(
        "group relative rounded-lg border bg-white cursor-pointer transition-all duration-150 w-[132px]",
        selected
          ? "border-amber-400 ring-2 ring-amber-100 shadow"
          : "border-zinc-200 border-dashed hover:border-amber-300 hover:shadow-sm",
      )}
      onDoubleClick={(e) => { e.stopPropagation(); onProps({ kind: "mcp", serverId, serverName: data.label as string, ownerAgentId: data.ownerAgentId as string }); }}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onProps({ kind: "mcp", serverId, serverName: data.label as string, ownerAgentId: data.ownerAgentId as string }); }}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="w-1 h-1 rounded-full bg-amber-500" />
          <p className="text-[8px] font-bold uppercase tracking-[0.15em] text-zinc-400">MCP</p>
        </div>
        <p className="font-medium text-xs text-zinc-800 truncate leading-tight">{data.label as string}</p>
        <p className="text-[9px] text-zinc-400 mt-0.5">{tools.length} tool{tools.length !== 1 ? "s" : ""}</p>
      </div>
      {selected && (
        <div className="absolute -bottom-7 left-0 right-0 flex justify-center z-10">
          <button
            onClick={(e) => { e.stopPropagation(); onRemoveMCP(serverId, data.ownerAgentId as string); }}
            className="nodrag nopan bg-white border border-red-200 text-red-500 rounded-md px-2 py-0.5 text-[11px] hover:bg-red-50 shadow-sm font-medium"
          >Disconnect</button>
        </div>
      )}
      <H type="target" pos={Position.Top} />
    </div>
  );
}

const nodeTypes: NodeTypes = {
  "main-agent": MainNode,
  "sub-agent": SubAgentNode,
  tool: ToolNode,
  mcp: MCPNode,
};

// ─── Cache patch ──────────────────────────────────────────────────────────────
function patchAgent(qc: QueryClient, agentId: string, config: AgentConfig) {
  qc.setQueryData<Agent>(["agent", agentId], (p) => p ? { ...p, config } : p);
  qc.setQueryData<Agent[]>(["agents"], (p) => p?.map((a) => a.id === agentId ? { ...a, config } : a));
}

// ─── Section header component ─────────────────────────────────────────────────
function SectionHeader({ label, count }: { label: string; count?: number }) {
  return (
    <div className="flex items-center mb-2 px-1">
      <span className="text-[10px] font-bold uppercase tracking-widest text-gray-400 flex-1">{label}</span>
      {count !== undefined && <span className="text-[10px] text-gray-300">{count}</span>}
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────
interface Props { agent: Agent; allAgents: Agent[] }

export default function AgentCanvas({ agent, allAgents }: Props) {
  const { token } = useAuth();
  const qc = useQueryClient();

  // ── Panel / dialog state ───────────────────────────────────────────────────
  // Left panel — opens when "+" is clicked on a node
  const [addPanel, setAddPanel] = useState<{ sourceAgentId: string; isRoot: boolean } | null>(null);
  // Small dialog for tool API key config + validation
  const [toolConfigDlg, setToolConfigDlg] = useState<ToolConfigDlg | null>(null);
  // Right sheet — opens on double-click or right-click of a node
  const [propsTarget, setPropsTarget] = useState<PropsTarget | null>(null);
  const [editSubmitting, setEditSubmitting] = useState(false);

  // Inline agent creation (inside left panel's Agents section)
  const [agentCreateOpen, setAgentCreateOpen] = useState(false);
  const [agentForm, setAgentForm] = useState<CreateAgentForm>(DEFAULT_AGENT_FORM);
  const [creatingAgent, setCreatingAgent] = useState(false);

  // Persona popup state — opens from the agent-create form's "+ New" link
  const [personaPopupOpen, setPersonaPopupOpen] = useState(false);

  // Inline MCP registration (inside left panel's MCP section)
  const [mcpRegOpen, setMcpRegOpen] = useState(false);
  const [mcpRegForm, setMcpRegForm] = useState({ name: "", url: "", transport: "http" as "http" | "sse" });
  const [mcpRegState, setMcpRegState] = useState<"idle" | "saving" | "fail">("idle");
  const [mcpRegError, setMcpRegError] = useState("");

  // ── Data queries ──────────────────────────────────────────────────────────
  const { data: registryTools = [] } = useQuery({ queryKey: ["tools"], queryFn: listTools });
  const { data: mcpServers = [] } = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => listMCPServers(token!),
    enabled: !!token,
  });
  const { data: toolConfigs = [] } = useQuery({
    queryKey: ["tool-configs"],
    queryFn: () => listToolConfigs(token!),
    enabled: !!token,
  });
  const { data: personas = [] } = useQuery({
    queryKey: ["personas"],
    queryFn: () => listPersonas(token!),
    enabled: !!token,
    staleTime: 60_000,
  });

  // Default-select 'Default - Sub Agent' for new sub-agents; fall back to the
  // first persona if the seed is missing for some reason.
  useEffect(() => {
    if (!agentForm.personaId && personas.length > 0) {
      const seed = personas.find((p) => p.name === "Default - Sub Agent") ?? personas[0];
      setAgentForm((f) => ({ ...f, personaId: seed.id }));
    }
  }, [personas, agentForm.personaId]);

  // Discover tools from ALL registered MCP servers in parallel
  const { data: mcpToolsMap = {} } = useQuery({
    queryKey: ["mcp-tools", mcpServers.map((s) => s.id).join(",")],
    queryFn: async () => {
      const settled = await Promise.allSettled(
        mcpServers.map((s) =>
          discoverMCPTools(token!, s.id).then((tools) => ({ server: s, tools })),
        ),
      );
      const result: Record<string, { server: MCPServer; tools: Array<{ name: string; description: string }> }> = {};
      settled.forEach((r) => {
        if (r.status === "fulfilled") result[r.value.server.id] = r.value;
      });
      return result;
    },
    enabled: !!token && mcpServers.length > 0,
    staleTime: 30_000,
  });

  const configuredTools = useMemo(
    () => new Set(toolConfigs.map((tc) => tc.tool_name)),
    [toolConfigs],
  );

  // ── Mutations ─────────────────────────────────────────────────────────────
  const addTool = useCallback(async (toolName: string, agentId: string) => {
    const target = agentId === agent.id ? agent : allAgents.find((a) => a.id === agentId) ?? agent;
    if (target.config.tools.includes(toolName)) return;
    const updated: AgentConfig = { ...target.config, tools: [...target.config.tools, toolName] };
    patchAgent(qc, agentId, updated);
    setAddPanel(null);
    try {
      await updateAgent(token!, agentId, updated);
    } catch (e) {
      console.error("Failed to add tool:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, allAgents, token, qc]);

  const removeTool = useCallback(async (toolName: string, agentId: string) => {
    const target = agentId === agent.id ? agent : allAgents.find((a) => a.id === agentId) ?? agent;
    const updated: AgentConfig = { ...target.config, tools: target.config.tools.filter((t) => t !== toolName) };
    patchAgent(qc, agentId, updated);
    setPropsTarget(null);
    try {
      await updateAgent(token!, agentId, updated);
    } catch (e) {
      console.error("Failed to remove tool:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, allAgents, token, qc]);

  // Resolve config for any agent in the current tree (root or sub).
  const _configOf = useCallback((parentId: string): AgentConfig => {
    return parentId === agent.id
      ? agent.config
      : allAgents.find((a) => a.id === parentId)?.config ?? agent.config;
  }, [agent, allAgents]);

  const addSubagent = useCallback(async (subId: string, parentId: string = agent.id) => {
    const cfg = _configOf(parentId);
    if (cfg.subagents.includes(subId)) return;
    const updated: AgentConfig = { ...cfg, subagents: [...cfg.subagents, subId] };
    patchAgent(qc, parentId, updated);
    setAddPanel(null);
    try {
      await updateAgent(token!, parentId, updated);
    } catch (e) {
      console.error("Failed to add sub-agent:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, token, qc, _configOf]);

  const removeSubagent = useCallback(async (subId: string, parentId: string = agent.id) => {
    const cfg = _configOf(parentId);
    const updated: AgentConfig = { ...cfg, subagents: cfg.subagents.filter((id) => id !== subId) };
    patchAgent(qc, parentId, updated);
    setPropsTarget(null);
    try {
      await updateAgent(token!, parentId, updated);
    } catch (e) {
      console.error("Failed to remove sub-agent:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, token, qc, _configOf]);

  // Delete the sub-agent ENTIRELY from the account (not just detach). Detaches
  // from the current parent first if attached, then deletes the AgentDB row.
  // Other pipelines/parents that referenced it get a stale reference until they edit.
  const deleteSubagentRegistration = useCallback(async (subId: string, subName: string, parentId: string = agent.id) => {
    if (!confirm(`Delete sub-agent "${subName}" from your account?\nOther pipelines using it will lose the reference.`)) return;
    const cfg = _configOf(parentId);
    if (cfg.subagents.includes(subId)) {
      await removeSubagent(subId, parentId);
    }
    try {
      await apiDeleteAgent(token!, subId);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    } catch (e) {
      console.error("Failed to delete sub-agent:", e);
    }
  }, [agent, token, qc, removeSubagent, _configOf]);

  const addMCPServer = useCallback(async (serverId: string, parentId: string = agent.id) => {
    const cfg = _configOf(parentId);
    if (cfg.mcp_servers.includes(serverId)) return;
    const updated: AgentConfig = { ...cfg, mcp_servers: [...cfg.mcp_servers, serverId] };
    patchAgent(qc, parentId, updated);
    try {
      await updateAgent(token!, parentId, updated);
    } catch (e) {
      console.error("Failed to add MCP server:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, token, qc, _configOf]);

  const removeMCPServer = useCallback(async (serverId: string, parentId: string = agent.id) => {
    const cfg = _configOf(parentId);
    const updated: AgentConfig = { ...cfg, mcp_servers: cfg.mcp_servers.filter((id) => id !== serverId) };
    patchAgent(qc, parentId, updated);
    setPropsTarget(null);
    try {
      await updateAgent(token!, parentId, updated);
    } catch (e) {
      console.error("Failed to remove MCP server:", e);
      await qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [agent, token, qc, _configOf]);

  const deleteMCPRegistration = useCallback(async (serverId: string, serverName: string, parentId: string = agent.id) => {
    if (!confirm(`Delete MCP server "${serverName}"? This unregisters it from your account.`)) return;
    const cfg = _configOf(parentId);
    if (cfg.mcp_servers.includes(serverId)) {
      await removeMCPServer(serverId, parentId);
    }
    try {
      await deleteMCPServer(token!, serverId);
      await qc.invalidateQueries({ queryKey: ["mcp-servers"] });
    } catch (e) {
      console.error("Failed to delete MCP server:", e);
    }
  }, [agent, token, qc, removeMCPServer, _configOf]);

  // ── Context ───────────────────────────────────────────────────────────────
  const onAdd = useCallback((sourceAgentId: string, isRoot: boolean) => {
    setAddPanel({ sourceAgentId, isRoot });
    setAgentCreateOpen(false);
    setMcpRegOpen(false);
    setMcpRegState("idle");
    // Pre-fill new sub-agent form from root agent's LLM config (remove friction)
    const llm = agent.config.llm;
    if (llm.base_url || llm.model) {
      const provider = PROVIDERS.find((p) => p.url === llm.base_url)?.label ?? "Custom";
      setAgentForm((f) => ({
        ...f,
        provider,
        base_url: llm.base_url || f.base_url,
        api_key: llm.api_key !== "EMPTY" ? llm.api_key : f.api_key,
        model: llm.model || f.model,
      }));
    }
  }, [agent.config.llm]);
  const onProps = useCallback((t: PropsTarget) => setPropsTarget(t), []);
  const onRemoveTool = useCallback((n: string, owner: string) => removeTool(n, owner), [removeTool]);
  const onRemoveAgent = useCallback((id: string, parentId: string) => removeSubagent(id, parentId), [removeSubagent]);
  const onRemoveMCP = useCallback((id: string, parentId: string) => removeMCPServer(id, parentId), [removeMCPServer]);
  const ctx = useMemo<CanvasCtx>(
    () => ({ onAdd, onProps, onRemoveTool, onRemoveAgent, onRemoveMCP }),
    [onAdd, onProps, onRemoveTool, onRemoveAgent, onRemoveMCP],
  );

  // ── Graph ─────────────────────────────────────────────────────────────────
  const toolDescMap = useMemo(
    () => Object.fromEntries(registryTools.map((t) => [t.name, t.description])),
    [registryTools],
  );
  const toolDisplayMap = useMemo(
    () => Object.fromEntries(registryTools.map((t) => [t.name, t.display_name])),
    [registryTools],
  );

  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = [];
    const es: Edge[] = [];

    ns.push({
      id: agent.id,
      type: "main-agent",
      data: {
        label: agent.name,
        role: agent.config.role,
        model: agent.config.llm.model || null,
      },
      position: { x: 0, y: 0 },
    });

    // Recursive walk: render each agent's tools + MCP + sub-agents. visited
    // skips an agent if it already appears in the tree, preventing duplicate
    // React Flow node IDs when the same sub-agent is attached under multiple
    // parents (first occurrence wins in the visualization; backend still has
    // the multi-parent reality).
    const visited = new Set<string>([agent.id]);
    const walk = (node: Agent) => {
      node.config.tools.forEach((toolName) => {
        const nid = `${node.id}::${toolName}`;
        ns.push({ id: nid, type: "tool", data: { label: toolName, toolName, displayName: toolDisplayMap[toolName] ?? toolName, ownerAgentId: node.id, description: toolDescMap[toolName] ?? "" }, position: { x: 0, y: 0 } });
        es.push({ id: `e:${node.id}->${nid}`, source: node.id, target: nid, style: { stroke: "#10b981", strokeWidth: 2 } });
      });
      node.config.mcp_servers.forEach((serverId) => {
        const server = mcpServers.find((s) => s.id === serverId);
        if (!server) return;
        const tools = mcpToolsMap[serverId]?.tools.map((t) => t.name) ?? [];
        const nid = `mcp::${node.id}::${serverId}`;
        ns.push({ id: nid, type: "mcp", data: { label: server.name, serverId, tools, ownerAgentId: node.id }, position: { x: 0, y: 0 } });
        es.push({ id: `e:${node.id}->${nid}`, source: node.id, target: nid, style: { stroke: "#f59e0b", strokeWidth: 2, strokeDasharray: "6,3" } });
      });
      node.config.subagents.forEach((subId) => {
        const sub = allAgents.find((a) => a.id === subId);
        if (!sub || visited.has(sub.id)) return;
        visited.add(sub.id);
        ns.push({ id: sub.id, type: "sub-agent", data: { label: sub.name, role: sub.config.role, parentAgentId: node.id }, position: { x: 0, y: 0 } });
        es.push({ id: `e:${node.id}->${sub.id}`, source: node.id, target: sub.id, style: { stroke: "#3b82f6", strokeWidth: 2 } });
        walk(sub);
      });
    };
    walk(agent);

    return { nodes: layoutNodes(ns, es), edges: es };
  }, [agent, allAgents, toolDescMap, mcpServers, mcpToolsMap]);

  // ── Left panel: derived lists ──────────────────────────────────────────────
  const panelConfig = addPanel
    ? (addPanel.sourceAgentId === agent.id ? agent.config : allAgents.find((a) => a.id === addPanel.sourceAgentId)?.config ?? agent.config)
    : null;
  const panelTools = registryTools;
  // Sub-Agents picker: show only non-root agents (sub-agents) and exclude the current canvas root.
  // Excludes OTHER pipelines' main agents — they're pipeline owners, not attachable sub-agents.
  // Hide the source agent (whoever's "+" opened the panel) from its own
  // attach list — you can't attach a node to itself. Roots and the canvas's
  // own root are already excluded.
  const _panelSourceId = addPanel?.sourceAgentId ?? agent.id;
  const panelAgents = allAgents.filter((a) =>
    a.id !== agent.id && a.id !== _panelSourceId && !isPipelineRoot(a, allAgents),
  );
  const panelMCPServers = mcpServers;

  // ── Tool config dialog handlers ────────────────────────────────────────────
  function openToolConfig(toolName: string) {
    if (!addPanel) return;
    setToolConfigDlg({
      toolName,
      sourceAgentId: addPanel.sourceAgentId,
      configValues: {},
      testState: "idle",
    });
  }

  function handleSelectTool(toolName: string) {
    if (!addPanel) return;
    const needsConfig = TOOL_FIELDS[toolName]?.length > 0;
    if (needsConfig && !configuredTools.has(toolName)) {
      openToolConfig(toolName);
    } else {
      addTool(toolName, addPanel.sourceAgentId);
    }
  }

  async function handleTestToolConfig() {
    if (!toolConfigDlg) return;
    setToolConfigDlg((d) => d ? { ...d, testState: "testing", testError: undefined } : d);
    try {
      const res = await validateToolConfig(token!, toolConfigDlg.toolName, toolConfigDlg.configValues);
      setToolConfigDlg((d) => d ? { ...d, testState: res.ok ? "ok" : "fail", testError: res.error } : d);
    } catch {
      setToolConfigDlg((d) => d ? { ...d, testState: "fail", testError: "Request failed" } : d);
    }
  }

  async function handleSaveToolConfig() {
    if (!toolConfigDlg || toolConfigDlg.testState !== "ok") return;
    try {
      await upsertToolConfig(token!, toolConfigDlg.toolName, toolConfigDlg.configValues);
      await qc.invalidateQueries({ queryKey: ["tool-configs"] });
      addTool(toolConfigDlg.toolName, toolConfigDlg.sourceAgentId);
      setToolConfigDlg(null);
    } catch (e) {
      console.error("Failed to save tool config:", e);
    }
  }

  // ── MCP registration handler ───────────────────────────────────────────────
  async function handleRegisterMCP() {
    if (!mcpRegForm.name.trim() || !mcpRegForm.url.trim()) return;
    setMcpRegState("saving");
    setMcpRegError("");
    try {
      const server = await createMCPServer(token!, {
        name: mcpRegForm.name,
        url: mcpRegForm.url,
        transport: mcpRegForm.transport,
      });
      // Validate connection by discovering tools
      await discoverMCPTools(token!, server.id);
      // Auto-attach to current agent
      await addMCPServer(server.id);
      await qc.invalidateQueries({ queryKey: ["mcp-servers"] });
      setMcpRegState("idle");
      setMcpRegOpen(false);
      setMcpRegForm({ name: "", url: "", transport: "http" });
    } catch (e: unknown) {
      setMcpRegState("fail");
      setMcpRegError((e instanceof Error ? e.message : String(e)).slice(0, 150));
    }
  }

  // ── Create new agent inline handler ───────────────────────────────────────
  async function handleSubmitNewAgent() {
    if (!agentForm.name.trim() || !agentForm.model.trim() || !agentForm.personaId) return;
    setCreatingAgent(true);
    try {
      const picked = personas.find((x) => x.id === agentForm.personaId);
      const systemPrompt = picked?.system_prompt ?? "You are a helpful assistant.";
      // Inline form here is a fast-path for sub-agent creation — it inherits the
      // parent pipeline's provider rather than asking the user to pick again.
      // Full provider switching happens on the agent edit form.
      const parentLLM = agent.config.llm;
      const config: AgentConfig = {
        name: agentForm.name,
        role: "assistant",
        description: null,
        system_prompt: systemPrompt,
        llm: {
          provider: parentLLM.provider ?? "openai",
          base_url: agentForm.base_url || parentLLM.base_url || "",
          api_key: agentForm.api_key || parentLLM.api_key || "",
          model: agentForm.model,
          temperature: 0.7,
          max_tokens: 1024,
          timeout_s: 30.0,
        },
        tools: [],
        memory: { type: "summary", window: 10, summary_threshold: 20 },
        limits: { max_steps: 8 },
        subagents: [],
        skills: [],
        mcp_servers: [],
        channels: [],
        metadata: {},
      };
      saveLLMDefaults({
        provider: parentLLM.provider ?? "openai",
        base_url: agentForm.base_url,
        api_key: agentForm.api_key,
        model: agentForm.model,
      });
      const newAgent = await apiCreateAgent(token!, config);
      await qc.invalidateQueries({ queryKey: ["agents"] });
      await addSubagent(newAgent.id, addPanel?.sourceAgentId ?? agent.id);
      setAgentCreateOpen(false);
      setAgentForm(DEFAULT_AGENT_FORM);
    } catch (e) {
      console.error("Failed to create agent:", e);
    } finally {
      setCreatingAgent(false);
    }
  }

  // ── Agent edit (Sheet) ────────────────────────────────────────────────────
  const editAgent =
    propsTarget?.kind === "root-agent" ? agent
    : propsTarget?.kind === "sub-agent" ? allAgents.find((a) => a.id === propsTarget.agentId) ?? null
    : null;

  async function handleEditSave(config: AgentConfig) {
    if (!editAgent) return;
    setEditSubmitting(true);
    try {
      await updateAgent(token!, editAgent.id, config);
      await qc.invalidateQueries({ queryKey: ["agents"] });
      await qc.invalidateQueries({ queryKey: ["agent", editAgent.id] });
      setPropsTarget(null);
    } catch (e) {
      console.error("Agent save failed:", e);
    } finally {
      setEditSubmitting(false);
    }
  }

  const isEmpty = nodes.length === 1;

  return (
    <Ctx.Provider value={ctx}>
      <div className="w-full h-full relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.6, maxZoom: 0.95 }}
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
          edgesFocusable={false}
          className="bg-[#f8f9fb]"
        >
          <Background color="#e2e8f0" gap={28} size={1.5} />
          <Controls showInteractive={false} />
        </ReactFlow>

        {/* Empty state hint */}
        {isEmpty && (
          <div className="absolute inset-0 flex items-end justify-center pb-16 pointer-events-none">
            <div className="bg-white/80 backdrop-blur-sm border border-violet-100 rounded-2xl px-6 py-4 text-center shadow-sm">
              <p className="text-sm text-violet-600 font-medium">Hover any node and click <span className="bg-violet-100 rounded px-1 font-bold">+</span> to connect tools, sub-agents, or MCP servers</p>
              <p className="text-xs text-muted-foreground mt-1">Double-click or right-click any node to edit its properties</p>
            </div>
          </div>
        )}

        {/* Tools & Agents button — opens the root's add-connection panel */}
        {!addPanel && (
          <button
            onClick={() => onAdd(agent.id, true)}
            className="absolute top-3 left-3 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/95 border border-violet-200 shadow-sm text-xs font-semibold text-violet-700 hover:bg-violet-50 hover:border-violet-400 transition-all backdrop-blur-sm"
          >
            <span className="text-sm font-bold">+</span>
            Tools &amp; Agents
          </button>
        )}

        {/* Legend — calm, neutral background, color dots only */}
        <div className="absolute top-3 right-3 bg-white/95 border border-zinc-200 rounded-lg px-3 py-2.5 text-[11px] shadow-sm backdrop-blur-sm space-y-1.5">
          <p className="font-bold text-[9px] uppercase tracking-[0.18em] text-zinc-400 mb-1.5">Legend</p>
          <div className="flex items-center gap-2 text-zinc-700"><span className="w-1.5 h-1.5 rounded-full bg-violet-600" />Supervisor</div>
          <div className="flex items-center gap-2 text-zinc-700"><span className="w-1.5 h-1.5 rounded-full bg-violet-300" />Sub-Agent</div>
          <div className="flex items-center gap-2 text-zinc-700"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />Tool</div>
          <div className="flex items-center gap-2 text-zinc-700"><span className="w-1.5 h-1.5 rounded-full bg-amber-500" />MCP</div>
          <p className="text-zinc-400 text-[10px] pt-1 border-t border-zinc-100">Hover → + · Dbl-click → edit</p>
        </div>

        {/* ── Left panel (add connections) ────────────────────────────────── */}
        {addPanel && (
          <div className="absolute left-0 top-0 bottom-0 z-20 w-[300px] bg-white/95 backdrop-blur-md border-r border-gray-200 shadow-xl flex flex-col">
            {/* Panel header */}
            <div className="flex items-center gap-2 px-4 py-3 border-b bg-gradient-to-r from-violet-50 to-blue-50 shrink-0">
              <p className="font-semibold text-sm flex-1">Add Connection</p>
              <button
                onClick={() => { setAddPanel(null); setAgentCreateOpen(false); setMcpRegOpen(false); }}
                className="size-6 rounded-full hover:bg-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-700 transition-colors"
              >✕</button>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-5">

              {/* ── Sub-Agents section ─────────────────────────────────────── */}
              <section>
                <SectionHeader label="Sub-Agents" count={panelAgents.length} />

                  {/* Create new agent inline */}
                  {!agentCreateOpen ? (
                    <button
                      onClick={() => setAgentCreateOpen(true)}
                      className="w-full mb-2 p-2.5 rounded-xl border-2 border-dashed border-blue-200 hover:border-blue-400 hover:bg-blue-50/50 text-blue-600 text-xs font-semibold transition-all"
                    >+ Create New Sub-Agent</button>
                  ) : (
                    <div className="mb-3 p-3 rounded-xl border border-blue-200 bg-blue-50/30 space-y-2.5">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-bold text-blue-700">New Sub-Agent</span>
                        <button onClick={() => setAgentCreateOpen(false)} className="text-xs text-gray-400 hover:text-gray-600">✕</button>
                      </div>

                      <div>
                        <Label className="text-[11px] text-gray-600">Name *</Label>
                        <Input
                          value={agentForm.name}
                          onChange={(e) => setAgentForm((f) => ({ ...f, name: e.target.value }))}
                          placeholder="Research Agent"
                          className="mt-1 h-8 text-xs"
                        />
                      </div>

                      <div>
                        <Label className="text-[11px] text-gray-600">Provider</Label>
                        <select
                          value={agentForm.provider}
                          onChange={(e) => {
                            const p = PROVIDERS.find((x) => x.label === e.target.value);
                            setAgentForm((f) => ({ ...f, provider: e.target.value, base_url: p?.url ?? "" }));
                          }}
                          className="mt-1 w-full h-8 border border-input rounded-md px-2 text-xs bg-background"
                        >
                          {PROVIDERS.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
                        </select>
                      </div>

                      <div>
                        <Label className="text-[11px] text-gray-600">Base URL *</Label>
                        <Input
                          value={agentForm.base_url}
                          onChange={(e) => setAgentForm((f) => ({ ...f, base_url: e.target.value }))}
                          placeholder="https://api.openai.com/v1"
                          className="mt-1 h-8 text-xs font-mono"
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[11px] text-gray-600">Model *</Label>
                          <Input
                            value={agentForm.model}
                            onChange={(e) => setAgentForm((f) => ({ ...f, model: e.target.value }))}
                            placeholder="gpt-4o-mini"
                            className="mt-1 h-8 text-xs"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px] text-gray-600">API Key</Label>
                          <Input
                            type="password"
                            value={agentForm.api_key}
                            onChange={(e) => setAgentForm((f) => ({ ...f, api_key: e.target.value }))}
                            placeholder="optional"
                            className="mt-1 h-8 text-xs"
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <Label className="text-[11px] text-gray-600">Persona (system prompt) *</Label>
                          <button
                            type="button"
                            onClick={() => setPersonaPopupOpen(true)}
                            className="text-[11px] text-blue-600 hover:underline"
                          >
                            + New persona
                          </button>
                        </div>
                        <select
                          value={agentForm.personaId}
                          onChange={(e) => setAgentForm((f) => ({ ...f, personaId: e.target.value }))}
                          className="w-full h-8 border border-input rounded-md px-2 text-xs bg-background"
                        >
                          {personas.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name}{p.owner_id === null ? " (default)" : ""}
                            </option>
                          ))}
                        </select>
                      </div>

                      <Button
                        size="sm"
                        className="w-full h-8 bg-blue-500 hover:bg-blue-600 text-white text-xs"
                        onClick={handleSubmitNewAgent}
                        disabled={
                          creatingAgent ||
                          !agentForm.name.trim() ||
                          !agentForm.base_url.trim() ||
                          !agentForm.model.trim() ||
                          !agentForm.personaId
                        }
                      >
                        {creatingAgent ? "Creating…" : "Create & Attach"}
                      </Button>
                    </div>
                  )}

                  {panelAgents.length === 0 && !agentCreateOpen && (
                    <p className="text-xs text-muted-foreground text-center py-3">No other agents yet. Create one above.</p>
                  )}
                  {panelAgents.map((a) => {
                    const attached = panelConfig?.subagents.includes(a.id) ?? false;
                    return (
                      <div
                        key={a.id}
                        className={cn(
                          "w-full flex items-center gap-2 px-3 py-2.5 rounded-xl border mb-1.5 transition-colors",
                          attached
                            ? "border-blue-200 bg-blue-50/40"
                            : "border-gray-100 hover:border-blue-200 hover:bg-blue-50/40",
                        )}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-semibold text-gray-900 truncate">{a.name}</p>
                          <p className="text-[10px] text-gray-400 truncate">{a.config.role}</p>
                        </div>
                        {attached ? (
                          <button
                            onClick={() => removeSubagent(a.id, addPanel.sourceAgentId)}
                            title="Detach from this parent — sub-agent stays in your account"
                            className="text-[10px] bg-blue-100 hover:bg-blue-500 hover:text-white text-blue-700 rounded-full px-2 py-0.5 font-medium shrink-0 transition-colors"
                          >Detach</button>
                        ) : (
                          <button
                            onClick={() => addSubagent(a.id, addPanel.sourceAgentId)}
                            title="Attach to this parent"
                            className="text-[10px] bg-emerald-100 hover:bg-emerald-500 hover:text-white text-emerald-700 rounded-full px-2 py-0.5 font-medium shrink-0 transition-colors"
                          >Attach</button>
                        )}
                        <button
                          onClick={() => deleteSubagentRegistration(a.id, a.name, addPanel.sourceAgentId)}
                          title="Delete sub-agent — removes it from your account entirely"
                          className="size-5 rounded text-red-400 hover:text-white hover:bg-red-500 flex items-center justify-center text-sm font-bold shrink-0 transition-colors"
                        >×</button>
                      </div>
                    );
                  })}
              </section>

              {/* ── Internal tools section ─────────────────────────────────── */}
              <section>
                <SectionHeader label="Tools" count={panelTools.length} />

                {panelTools.length === 0 && (
                  <p className="text-xs text-muted-foreground text-center py-3">No tools available.</p>
                )}
                {panelTools.map((tool) => {
                  const attached = panelConfig?.tools.includes(tool.name) ?? false;
                  const needsConfig = !!TOOL_FIELDS[tool.name]?.length;
                  const isConfigured = configuredTools.has(tool.name);
                  return (
                    <button
                      key={tool.name}
                      onClick={() => !attached && handleSelectTool(tool.name)}
                      disabled={attached}
                      className={cn(
                        "w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl border text-left transition-all mb-1.5",
                        attached
                          ? "border-emerald-100 bg-emerald-50/40 opacity-60 cursor-default"
                          : "border-gray-100 hover:border-emerald-200 hover:bg-emerald-50/40 cursor-pointer",
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-semibold text-gray-900">{tool.display_name}</p>
                        <p className="text-[10px] text-gray-400 truncate">{tool.description}</p>
                      </div>
                      {attached
                        ? <span className="text-[10px] bg-emerald-100 text-emerald-600 rounded-full px-2 py-0.5 font-medium shrink-0">Attached</span>
                        : needsConfig && !isConfigured
                          ? <span className="text-[10px] bg-amber-100 text-amber-600 rounded-full px-1.5 py-0.5 shrink-0">Setup</span>
                          : <span className="text-gray-300 text-sm shrink-0">→</span>}
                    </button>
                  );
                })}
              </section>

              {/* ── MCP servers section ────────────────────────────────────── */}
              <section>
                <SectionHeader label="MCP Servers" count={panelMCPServers.length} />

                {/* Register new MCP inline form */}
                {!mcpRegOpen ? (
                  <button
                    onClick={() => { setMcpRegOpen(true); setMcpRegState("idle"); setMcpRegError(""); }}
                    className="w-full mb-2 p-2.5 rounded-xl border-2 border-dashed border-amber-200 hover:border-amber-400 hover:bg-amber-50/50 text-amber-600 text-xs font-semibold transition-all"
                  >+ Register New MCP Server</button>
                ) : (
                  <div className="mb-3 p-3 rounded-xl border border-amber-200 bg-amber-50/30 space-y-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold text-amber-700">Register MCP Server</span>
                      <button onClick={() => { setMcpRegOpen(false); setMcpRegState("idle"); }} className="text-xs text-gray-400 hover:text-gray-600">✕</button>
                    </div>

                    <div>
                      <Label className="text-[11px] text-gray-600">Server Name *</Label>
                      <Input
                        value={mcpRegForm.name}
                        onChange={(e) => setMcpRegForm((f) => ({ ...f, name: e.target.value }))}
                        placeholder="My MCP Server"
                        className="mt-1 h-8 text-xs"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-gray-600">URL *</Label>
                      <Input
                        value={mcpRegForm.url}
                        onChange={(e) => setMcpRegForm((f) => ({ ...f, url: e.target.value }))}
                        placeholder="http://localhost:3000/mcp"
                        className="mt-1 h-8 text-xs font-mono"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-gray-600">Transport</Label>
                      <select
                        value={mcpRegForm.transport}
                        onChange={(e) => setMcpRegForm((f) => ({ ...f, transport: e.target.value as "http" | "sse" }))}
                        className="mt-1 w-full h-8 border border-input rounded-md px-2 text-xs bg-background"
                      >
                        <option value="http">HTTP</option>
                        <option value="sse">SSE</option>
                      </select>
                    </div>

                    {mcpRegState === "fail" && (
                      <p className="text-[11px] text-red-500 bg-red-50 border border-red-200 rounded-lg p-2">✗ {mcpRegError || "Connection failed"}</p>
                    )}

                    <Button
                      size="sm"
                      className="w-full h-8 bg-amber-500 hover:bg-amber-600 text-white text-xs"
                      onClick={handleRegisterMCP}
                      disabled={mcpRegState === "saving" || !mcpRegForm.name.trim() || !mcpRegForm.url.trim()}
                    >
                      {mcpRegState === "saving" ? "Testing & Registering…" : "Register & Connect"}
                    </Button>
                  </div>
                )}

                {/* Existing servers as sub-groups */}
                {panelMCPServers.length === 0 && !mcpRegOpen && (
                  <p className="text-xs text-muted-foreground text-center py-3">No MCP servers registered.</p>
                )}
                {panelMCPServers.map((server) => {
                  const attached = panelConfig?.mcp_servers.includes(server.id) ?? false;
                  const discovered = mcpToolsMap[server.id]?.tools ?? [];
                  return (
                    <div
                      key={server.id}
                      className={cn(
                        "mb-2 rounded-xl border overflow-hidden",
                        attached ? "border-amber-200 bg-amber-50/30" : "border-gray-100 bg-gray-50/50",
                      )}
                    >
                      {/* Server header row */}
                      <div className="flex items-center gap-2 px-3 py-2">
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-semibold text-gray-900 truncate">{server.name}</p>
                          <p className="text-[10px] text-gray-400 truncate font-mono">{server.url}</p>
                        </div>
                        {attached ? (
                          <button
                            onClick={() => removeMCPServer(server.id, addPanel.sourceAgentId)}
                            title="Detach from this parent — server stays registered in your account"
                            className="text-[10px] bg-amber-100 hover:bg-amber-500 hover:text-white text-amber-700 rounded-full px-2 py-0.5 font-medium shrink-0 transition-colors"
                          >Detach</button>
                        ) : (
                          <button
                            onClick={() => addMCPServer(server.id, addPanel.sourceAgentId)}
                            title="Attach to this parent"
                            className="text-[10px] bg-emerald-100 hover:bg-emerald-500 hover:text-white text-emerald-700 rounded-full px-2 py-0.5 font-medium shrink-0 transition-colors"
                          >Attach</button>
                        )}
                        <button
                          onClick={() => deleteMCPRegistration(server.id, server.name, addPanel.sourceAgentId)}
                          title="Delete registration — removes the server from your account entirely"
                          className="size-5 rounded text-red-400 hover:text-white hover:bg-red-500 flex items-center justify-center text-sm font-bold shrink-0 transition-colors"
                        >×</button>
                      </div>
                      {/* Discovered tools */}
                      {discovered.length > 0 && (
                        <div className="px-3 pb-2 flex flex-wrap gap-1">
                          {discovered.slice(0, 6).map((t) => (
                            <span key={t.name} className="text-[10px] bg-amber-100/60 text-amber-700 rounded px-1.5 py-0.5 font-mono">{t.name}</span>
                          ))}
                          {discovered.length > 6 && (
                            <span className="text-[10px] text-amber-400">+{discovered.length - 6} more</span>
                          )}
                        </div>
                      )}
                      {discovered.length === 0 && (
                        <p className="px-3 pb-2 text-[10px] text-gray-400">No tools discovered (server may be offline)</p>
                      )}
                    </div>
                  );
                })}
              </section>

            </div>
          </div>
        )}

        {/* ── Tool config dialog (API key + validation) ────────────────────── */}
        <Dialog open={!!toolConfigDlg} onOpenChange={(o) => !o && setToolConfigDlg(null)}>
          <DialogContent className="max-w-sm">
            {toolConfigDlg && (() => {
              const fields = TOOL_FIELDS[toolConfigDlg.toolName] ?? [];
              const displayName = registryTools.find((t) => t.name === toolConfigDlg.toolName)?.display_name ?? toolConfigDlg.toolName;
              const allFilled = fields.every((f) => !f.required || toolConfigDlg.configValues[f.key]?.trim());
              return (
                <>
                  {/* Gradient header */}
                  <div className="-mx-6 -mt-6 px-6 pt-5 pb-4 rounded-t-lg bg-gradient-to-br from-emerald-500 to-teal-600 mb-4">
                    <p className="font-bold text-white text-base">{displayName}</p>
                    <p className="text-emerald-100 text-xs">Tool Configuration</p>
                  </div>

                  <p className="text-xs text-muted-foreground mb-3">
                    Enter credentials below. Your keys are saved to your account and used at runtime — never shared.
                  </p>

                  <div className="space-y-3">
                    {fields.map((field) => (
                      <div key={field.key}>
                        <Label className="text-xs font-semibold">
                          {field.label}{field.required && <span className="text-red-400 ml-1">*</span>}
                        </Label>
                        <Input
                          className="mt-1.5 font-mono text-sm"
                          type="password"
                          placeholder={field.placeholder}
                          value={toolConfigDlg.configValues[field.key] ?? ""}
                          onChange={(e) =>
                            setToolConfigDlg((d) => d ? {
                              ...d,
                              configValues: { ...d.configValues, [field.key]: e.target.value },
                              testState: "idle",
                              testError: undefined,
                            } : d)
                          }
                        />
                      </div>
                    ))}
                  </div>

                  {/* Test result banner */}
                  {toolConfigDlg.testState === "ok" && (
                    <div className="mt-3 flex items-center gap-2 p-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700 font-medium">
                      <span>✓</span> Connection verified — key is valid
                    </div>
                  )}
                  {toolConfigDlg.testState === "fail" && (
                    <div className="mt-3 p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
                      ✗ {toolConfigDlg.testError || "Validation failed"}
                    </div>
                  )}

                  <div className="flex gap-2 mt-4">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setToolConfigDlg(null)}
                      className="flex-none"
                    >Cancel</Button>

                    {toolConfigDlg.testState !== "ok" && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleTestToolConfig}
                        disabled={toolConfigDlg.testState === "testing" || !allFilled}
                        className="flex-1 border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                      >
                        {toolConfigDlg.testState === "testing" ? "Testing…" : "Test Connection"}
                      </Button>
                    )}

                    <Button
                      size="sm"
                      onClick={handleSaveToolConfig}
                      disabled={toolConfigDlg.testState !== "ok"}
                      className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white"
                    >
                      Save & Add
                    </Button>
                  </div>
                </>
              );
            })()}
          </DialogContent>
        </Dialog>

        {/* ── Properties / Edit sheet (right side) ────────────────────────── */}
        <Sheet open={!!propsTarget} onOpenChange={(o) => !o && setPropsTarget(null)}>
          <SheetContent className="w-[480px] overflow-y-auto p-0">

            {/* Tool properties */}
            {propsTarget?.kind === "tool" && (() => {
              const ps = propsTarget as { toolName: string; ownerAgentId: string; description: string };
              const displayName = registryTools.find((t) => t.name === ps.toolName)?.display_name ?? ps.toolName;
              return (
                <>
                  <div className="px-6 py-5 bg-gradient-to-br from-emerald-500 to-teal-600">
                    <p className="font-bold text-white text-lg">{displayName}</p>
                    <p className="text-emerald-100 text-xs font-medium uppercase tracking-wide">Internal Tool</p>
                  </div>
                  <div className="p-6 space-y-5">
                    <div>
                      <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Description</p>
                      <p className="text-sm text-gray-700">{ps.description || "No description available."}</p>
                    </div>
                    {TOOL_FIELDS[ps.toolName]?.length > 0 && (
                      <div className={cn(
                        "rounded-xl p-4 border",
                        configuredTools.has(ps.toolName)
                          ? "bg-emerald-50 border-emerald-200"
                          : "bg-amber-50 border-amber-200"
                      )}>
                        <p className={cn("text-xs font-bold mb-1", configuredTools.has(ps.toolName) ? "text-emerald-700" : "text-amber-700")}>
                          {configuredTools.has(ps.toolName) ? "Credentials saved" : "Credentials required"}
                        </p>
                        <p className={cn("text-xs", configuredTools.has(ps.toolName) ? "text-emerald-600" : "text-amber-600")}>
                          {configuredTools.has(ps.toolName)
                            ? "Your API key is stored and will be injected at runtime."
                            : "Use the + panel to add this tool and enter your credentials."}
                        </p>
                      </div>
                    )}
                    <div className="pt-3 border-t">
                      <Button size="sm" variant="destructive" onClick={() => removeTool(ps.toolName, ps.ownerAgentId)}>
                        Remove Tool
                      </Button>
                    </div>
                  </div>
                </>
              );
            })()}

            {/* MCP properties */}
            {propsTarget?.kind === "mcp" && (() => {
              const ps = propsTarget as { serverId: string; serverName: string };
              const server = mcpServers.find((s) => s.id === ps.serverId);
              const discovered = mcpToolsMap[ps.serverId]?.tools ?? [];
              return (
                <>
                  <div className="px-6 py-5 bg-gradient-to-br from-amber-500 to-orange-500">
                    <p className="font-bold text-white text-lg">{ps.serverName}</p>
                    <p className="text-amber-100 text-xs font-medium uppercase tracking-wide">MCP · External Server</p>
                  </div>
                  <div className="p-6 space-y-5">
                    {server && (
                      <div>
                        <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Connection</p>
                        <p className="text-sm font-mono text-gray-700 bg-gray-50 rounded-lg px-3 py-2 break-all">{server.url}</p>
                        <p className="text-xs text-muted-foreground mt-1.5">Transport: <span className="font-medium">{server.transport.toUpperCase()}</span></p>
                      </div>
                    )}
                    <div>
                      <p className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">
                        Available Tools ({discovered.length})
                      </p>
                      {discovered.length === 0
                        ? <p className="text-xs text-muted-foreground bg-gray-50 rounded-lg p-3">No tools discovered — server may be offline.</p>
                        : (
                          <div className="space-y-1.5">
                            {discovered.map((t) => (
                              <div key={t.name} className="flex items-start gap-2 p-2.5 bg-amber-50 border border-amber-100 rounded-lg">
                                <span className="text-xs font-mono font-bold text-amber-800">{t.name}</span>
                                {t.description && <span className="text-xs text-gray-500 truncate">— {t.description}</span>}
                              </div>
                            ))}
                          </div>
                        )}
                    </div>
                    <div className="pt-3 border-t">
                      <Button size="sm" variant="destructive" onClick={() => removeMCPServer(ps.serverId, ps.ownerAgentId)}>
                        Disconnect Server
                      </Button>
                    </div>
                  </div>
                </>
              );
            })()}

            {/* Agent properties */}
            {(propsTarget?.kind === "root-agent" || propsTarget?.kind === "sub-agent") && editAgent && (
              <>
                <div className={cn(
                  "px-6 py-5",
                  propsTarget.kind === "root-agent"
                    ? "bg-gradient-to-br from-violet-600 to-purple-700"
                    : "bg-gradient-to-br from-blue-600 to-indigo-700"
                )}>
                  <p className="font-bold text-white text-lg">{editAgent.name}</p>
                  <p className="text-white/70 text-xs font-medium uppercase tracking-wide">
                    {propsTarget.kind === "root-agent" ? "Supervisor Pipeline" : "Sub Agent"}
                  </p>
                  {/* Quick stats */}
                  <div className="flex gap-3 mt-3">
                    <span className="text-xs text-white/80 bg-white/10 rounded-full px-2.5 py-1">
                      {editAgent.config.tools.length} tools
                    </span>
                    <span className="text-xs text-white/80 bg-white/10 rounded-full px-2.5 py-1">
                      temp {editAgent.config.llm.temperature}
                    </span>
                    <span className="text-xs text-white/80 bg-white/10 rounded-full px-2.5 py-1">
                      memory: {editAgent.config.memory.type}
                    </span>
                  </div>
                </div>
                <div className="p-4 space-y-3">
                  {propsTarget.kind === "sub-agent" && (
                    <button
                      onClick={() => {
                        const parentId = propsTarget.parentAgentId;
                        if (!confirm(
                          `Remove "${editAgent.name}" and everything below it from this pipeline?\n` +
                          `\nThe sub-agent (and any nested children) stay in your account — they just stop being visible on this canvas and stop running in this pipeline. You can re-attach them anytime.`,
                        )) return;
                        removeSubagent(editAgent.id, parentId);
                      }}
                      className="w-full px-4 py-2.5 rounded-lg bg-red-50 border border-red-200 text-red-600 hover:bg-red-500 hover:text-white hover:border-red-500 text-sm font-semibold transition-colors flex items-center justify-center gap-2"
                    >
                      <span>×</span> Remove subtree from this pipeline
                    </button>
                  )}
                  <AgentForm
                    agent={editAgent}
                    allAgents={allAgents.filter((a) => a.id !== editAgent.id)}
                    onSubmit={handleEditSave}
                    onCancel={() => setPropsTarget(null)}
                    submitting={editSubmitting}
                  />
                </div>
              </>
            )}

          </SheetContent>
        </Sheet>

        <PersonaPopup
          open={personaPopupOpen}
          onClose={() => setPersonaPopupOpen(false)}
          onSaved={(p) => setAgentForm((f) => ({ ...f, personaId: p.id }))}
        />
      </div>
    </Ctx.Provider>
  );
}
