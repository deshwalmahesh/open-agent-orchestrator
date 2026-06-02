import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { listTools } from "@/api/tools";
import { listSkills } from "@/api/skills";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import { getLLMDefaults, saveLLMDefaults } from "@/lib/llm-defaults";
import type { Agent, AgentConfig } from "@/types";

// ─── Provider quick-select ────────────────────────────────────────────────────
const PROVIDER_PRESETS = [
  { label: "OpenAI",   url: "https://api.openai.com/v1",  model: "gpt-4o-mini" },
  { label: "vLLM",     url: "http://localhost:8000/v1",   model: "" },
  { label: "Anthropic",url: "https://api.anthropic.com/v1", model: "claude-3-haiku-20240307" },
  { label: "Custom",   url: "",                            model: "" },
] as const;

// ─── Zod schema ───────────────────────────────────────────────────────────────
// Name + role intentionally NOT required: for a root pipeline the supervisor's
// identity-naming friction was reported as overload. Defaults fill in if blank.
const schema = z.object({
  name: z.string(),
  role: z.string(),
  system_prompt: z.string().min(1, "Required"),
  llm_base_url: z.string().min(1, "Required"),
  llm_api_key: z.string(),
  llm_model: z.string().min(1, "Required"),
  llm_temperature: z.number().min(0).max(2),
  llm_max_tokens: z.number().int().min(1),
  tools: z.array(z.string()),
  memory_type: z.enum(["none", "buffer", "summary"]),
  memory_window: z.number().int().min(1),
  memory_threshold: z.number().int().min(1),
  max_steps: z.number().int().min(1),
  skills: z.array(z.string()),
  subagents: z.array(z.string()),
});

type FormValues = z.infer<typeof schema>;

function configToForm(config: AgentConfig): FormValues {
  return {
    name: config.name,
    role: config.role,
    system_prompt: config.system_prompt,
    llm_base_url: config.llm.base_url,
    llm_api_key: config.llm.api_key,
    llm_model: config.llm.model,
    llm_temperature: config.llm.temperature,
    llm_max_tokens: config.llm.max_tokens,
    tools: config.tools,
    memory_type: config.memory.type,
    memory_window: config.memory.window,
    memory_threshold: config.memory.summary_threshold,
    max_steps: config.limits.max_steps,
    skills: config.skills,
    subagents: config.subagents,
  };
}

function formToConfig(values: FormValues, existing?: AgentConfig): AgentConfig {
  return {
    name: values.name.trim() || existing?.name || "Pipeline",
    role: values.role.trim() || existing?.role || "supervisor",
    description: existing?.description ?? null,
    system_prompt: values.system_prompt,
    llm: {
      base_url: values.llm_base_url,
      api_key: values.llm_api_key || "EMPTY",
      model: values.llm_model,
      temperature: values.llm_temperature,
      max_tokens: values.llm_max_tokens,
      timeout_s: existing?.llm.timeout_s ?? 30.0,
    },
    tools: values.tools,
    memory: {
      type: values.memory_type,
      window: values.memory_window,
      summary_threshold: values.memory_threshold,
    },
    limits: {
      max_steps: values.max_steps,
    },
    subagents: values.subagents,
    skills: values.skills,
    mcp_servers: existing?.mcp_servers ?? [],
    channels: existing?.channels ?? [],
    metadata: existing?.metadata ?? {},
  };
}

function getDefaultValues(): FormValues {
  const llm = getLLMDefaults();
  return {
    name: "",
    role: "supervisor",
    system_prompt: "You are a helpful assistant.",
    llm_base_url: llm.base_url,
    llm_api_key: llm.api_key,
    llm_model: llm.model,
    llm_temperature: llm.temperature,
    llm_max_tokens: llm.max_tokens,
    tools: [],
    memory_type: "summary",
    memory_window: 10,
    memory_threshold: 20,
    max_steps: 8,
    skills: [],
    subagents: [],
  };
}

interface Props {
  agent?: Agent;
  allAgents: Agent[];
  onSubmit: (config: AgentConfig) => Promise<void>;
  onCancel: () => void;
  submitting: boolean;
}

export default function AgentForm({ agent, allAgents, onSubmit, onCancel, submitting }: Props) {
  const { token, user } = useAuth();
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: agent ? configToForm(agent.config) : getDefaultValues(),
  });

  const { data: tools = [] } = useQuery({
    queryKey: ["tools"],
    queryFn: listTools,
    staleTime: Infinity,
  });

  const { data: skillsList = [] } = useQuery({
    queryKey: ["skills"],
    queryFn: () => listSkills(token!),
    enabled: !!token,
  });

  const memType = watch("memory_type");
  const temperature = watch("llm_temperature");
  const selectedTools = watch("tools");
  const selectedSkills = watch("skills");
  const selectedSubagents = watch("subagents");
  const currentBaseUrl = watch("llm_base_url");

  const isPaid = (user?.plan ?? "free") !== "free";
  const subagentOptions = allAgents.filter((a) => a.id !== agent?.id);

  function toggleItem(field: "tools" | "skills" | "subagents", id: string, checked: boolean) {
    const current = { tools: selectedTools, skills: selectedSkills, subagents: selectedSubagents }[field];
    setValue(field, checked ? [...current, id] : current.filter((x) => x !== id));
  }

  function applyProvider(preset: typeof PROVIDER_PRESETS[number]) {
    setValue("llm_base_url", preset.url);
    if (preset.model) setValue("llm_model", preset.model);
  }

  // Detect which provider matches the current base URL
  const activeProvider = PROVIDER_PRESETS.find((p) => p.url && currentBaseUrl === p.url)?.label ?? "Custom";

  async function onValid(values: FormValues) {
    // Persist LLM settings so new agents / sub-agents pre-fill from them
    saveLLMDefaults({
      base_url: values.llm_base_url,
      api_key: values.llm_api_key,
      model: values.llm_model,
      temperature: values.llm_temperature,
      max_tokens: values.llm_max_tokens,
    });
    await onSubmit(formToConfig(values, agent?.config));
  }

  return (
    <form onSubmit={handleSubmit(onValid)} className="space-y-6">

      {/* ── Identity ──────────────────────────────────────────────────────── */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Identity</h3>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-gray-600">Name</Label>
            <Input {...register("name")} placeholder="Pipeline" className="focus-visible:ring-violet-300" />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-gray-600">Role</Label>
            <Input {...register("role")} placeholder="supervisor" className="focus-visible:ring-violet-300" />
          </div>
        </div>
        <div className="mt-3 space-y-1.5">
          <Label className="text-xs font-semibold text-gray-600">System Prompt *</Label>
          <Textarea
            {...register("system_prompt")}
            rows={4}
            placeholder="You are a helpful assistant with expertise in…"
            className="focus-visible:ring-violet-300 resize-none text-sm"
          />
          {errors.system_prompt && <p className="text-xs text-destructive">{errors.system_prompt.message}</p>}
        </div>
      </section>

      <Separator />

      {/* ── LLM Config ────────────────────────────────────────────────────── */}
      <section>
        <Collapsible defaultOpen>
          <CollapsibleTrigger className="flex items-center w-full text-left mb-3 group">
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 flex-1">LLM Config</h3>
            <span className="text-xs text-gray-300 group-hover:text-gray-500 transition-colors">toggle</span>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-4">

            {/* Provider preset buttons */}
            <div>
              <Label className="text-xs font-semibold text-gray-600 block mb-2">Provider</Label>
              <div className="flex gap-2 flex-wrap">
                {PROVIDER_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => applyProvider(p)}
                    className={cn(
                      "px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all",
                      activeProvider === p.label
                        ? "border-violet-400 bg-violet-50 text-violet-700"
                        : "border-gray-200 text-gray-500 hover:border-violet-200 hover:bg-violet-50/50",
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Base URL *</Label>
              <Input
                {...register("llm_base_url")}
                placeholder="https://api.openai.com/v1"
                className="font-mono text-xs focus-visible:ring-violet-300"
              />
              {errors.llm_base_url && <p className="text-xs text-destructive">{errors.llm_base_url.message}</p>}
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Model *</Label>
              <Input
                {...register("llm_model")}
                placeholder="gpt-4o-mini"
                className="focus-visible:ring-violet-300"
              />
              {errors.llm_model && <p className="text-xs text-destructive">{errors.llm_model.message}</p>}
            </div>

            {/* API key: hidden for paid plans, required for free */}
            {isPaid ? (
              <div className="px-4 py-3 bg-gradient-to-r from-violet-50 to-purple-50 border border-violet-200 rounded-xl">
                <p className="text-xs font-bold text-violet-700">API key — managed by your plan</p>
                <p className="text-xs text-violet-500 mt-0.5">Your {user?.plan} plan provides LLM access automatically.</p>
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label className="text-xs font-semibold text-gray-600">
                  API Key
                  <span className="ml-1.5 text-amber-500 font-normal">(required — free plan)</span>
                </Label>
                <Input
                  {...register("llm_api_key")}
                  type="password"
                  placeholder="sk-… (leave EMPTY only if using key-free local model)"
                  className="focus-visible:ring-violet-300"
                />
                <p className="text-[10px] text-muted-foreground">Missing key will cause agent runs to fail. Enter your provider API key.</p>
              </div>
            )}

            {/* Temperature slider */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-semibold text-gray-600">Temperature</Label>
                <span className="text-xs font-mono font-bold text-violet-600 bg-violet-50 px-2 py-0.5 rounded-full">
                  {temperature.toFixed(1)}
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                {...register("llm_temperature", { valueAsNumber: true })}
                className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-violet-500 bg-gray-200"
              />
              <div className="flex justify-between text-[10px] text-gray-400">
                <span>Deterministic</span>
                <span>Creative</span>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Max Tokens</Label>
              <Input
                {...register("llm_max_tokens", { valueAsNumber: true })}
                type="number"
                min="1"
                className="w-32 focus-visible:ring-violet-300"
              />
            </div>
          </CollapsibleContent>
        </Collapsible>
      </section>

      <Separator />

      {/* ── Tools (chip toggles) ──────────────────────────────────────────── */}
      <section>
        <div className="flex items-center mb-3">
          <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 flex-1">Tools</h3>
          {selectedTools.length > 0 && (
            <span className="text-xs text-emerald-600 font-medium">{selectedTools.length} selected</span>
          )}
        </div>
        {tools.length === 0 ? (
          <p className="text-xs text-muted-foreground">Loading tools…</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {tools.map((t) => {
              const active = selectedTools.includes(t.name);
              return (
                <button
                  key={t.name}
                  type="button"
                  title={t.description}
                  onClick={() => toggleItem("tools", t.name, !active)}
                  className={cn(
                    "px-3 py-1.5 rounded-full border text-xs font-medium transition-all",
                    active
                      ? "border-emerald-400 bg-emerald-50 text-emerald-700"
                      : "border-gray-200 text-gray-500 hover:border-emerald-200 hover:bg-emerald-50/50",
                  )}
                >
                  {t.display_name}
                </button>
              );
            })}
          </div>
        )}
      </section>

      <Separator />

      {/* ── Memory type (visual cards) ────────────────────────────────────── */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Memory</h3>
        <div className="grid grid-cols-3 gap-2">
          {(["none", "buffer", "summary"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setValue("memory_type", v)}
              className={cn(
                "p-3 rounded-xl border-2 text-center transition-all",
                memType === v
                  ? "border-violet-400 bg-violet-50 text-violet-800 shadow-sm"
                  : "border-gray-100 hover:border-violet-200 text-gray-400 hover:bg-violet-50/30",
              )}
            >
              <span className="text-xs font-semibold capitalize">{v}</span>
            </button>
          ))}
        </div>
        {memType !== "none" && (
          <div className="grid grid-cols-2 gap-3 mt-3">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Window (N messages)</Label>
              <Input {...register("memory_window", { valueAsNumber: true })} type="number" min="1" className="focus-visible:ring-violet-300" />
            </div>
            {memType === "summary" && (
              <div className="space-y-1.5">
                <Label className="text-xs font-semibold text-gray-600">Summarise after (M)</Label>
                <Input {...register("memory_threshold", { valueAsNumber: true })} type="number" min="1" className="focus-visible:ring-violet-300" />
              </div>
            )}
          </div>
        )}
      </section>

      <Separator />

      {/* ── Sub-Agents ────────────────────────────────────────────────────── */}
      {subagentOptions.length > 0 && (
        <>
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Sub-Agents</h3>
            <div className="flex flex-wrap gap-2">
              {subagentOptions.map((a) => {
                const active = selectedSubagents.includes(a.id);
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => toggleItem("subagents", a.id, !active)}
                    className={cn(
                      "px-3 py-1.5 rounded-full border text-xs font-medium transition-all",
                      active
                        ? "border-blue-400 bg-blue-50 text-blue-700"
                        : "border-gray-200 text-gray-500 hover:border-blue-200 hover:bg-blue-50/50",
                    )}
                  >
                    {a.name}
                  </button>
                );
              })}
            </div>
          </section>
          <Separator />
        </>
      )}

      {/* ── Skills ────────────────────────────────────────────────────────── */}
      {skillsList.length > 0 && (
        <>
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Skills <span className="normal-case font-normal text-gray-300">— injected into system prompt</span></h3>
            <div className="flex flex-wrap gap-2">
              {skillsList.map((s) => {
                const active = selectedSkills.includes(s.id);
                return (
                  <button
                    key={s.id}
                    type="button"
                    title={s.content.slice(0, 120)}
                    onClick={() => toggleItem("skills", s.id, !active)}
                    className={cn(
                      "px-3 py-1.5 rounded-full border text-xs font-medium transition-all",
                      active
                        ? "border-amber-400 bg-amber-50 text-amber-700"
                        : "border-gray-200 text-gray-500 hover:border-amber-200 hover:bg-amber-50/50",
                    )}
                  >
                    {s.name}
                  </button>
                );
              })}
            </div>
          </section>
          <Separator />
        </>
      )}

      {/* ── Limits ─────────────────────────────────────────────────────────── */}
      <section>
        <Collapsible>
          <CollapsibleTrigger className="flex items-center w-full text-left mb-3 group">
            <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 flex-1">Limits</h3>
            <span className="text-xs text-gray-300 group-hover:text-gray-500 transition-colors">expand</span>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Max Steps</Label>
              <Input {...register("max_steps", { valueAsNumber: true })} type="number" min="1" className="w-32 focus-visible:ring-violet-300" />
              <p className="text-[10px] text-muted-foreground">Max ReAct iterations per run before the agent stops.</p>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </section>

      {/* ── Actions ───────────────────────────────────────────────────────── */}
      <div className="flex gap-2 pt-1">
        <Button
          type="submit"
          disabled={submitting}
          className="flex-1 bg-violet-600 hover:bg-violet-700 text-white"
        >
          {submitting ? "Saving…" : agent ? "Save Changes" : "Create Agent"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
