import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import PersonaPopup from "@/components/PersonaPopup";
import { listSkills } from "@/api/skills";
import { listPersonas } from "@/api/personas";
import { listProviders } from "@/api/providers";
import { useAuth } from "@/hooks/useAuth";
import { cn, isPipelineRoot } from "@/lib/utils";
import { getLLMDefaults, saveLLMDefaults } from "@/lib/llm-defaults";
import type { Agent, AgentConfig, LLMProvider, Persona } from "@/types";

// ─── Zod schema ───────────────────────────────────────────────────────────────
// system_prompt is no longer a form field — it's derived from the picked persona
// at save time. The user picks/creates a persona via PersonaPopup and that
// becomes the agent's prompt. Allows custom legacy prompts to be preserved when
// no persona id is selected (formToConfig falls back to existing.system_prompt).
const schema = z.object({
  name: z.string(),
  role: z.string(),
  // Provider id validated dynamically against the backend catalogue at submit
  // time — keep schema permissive so a new provider doesn't need a frontend
  // rebuild. The dropdown only offers known ids anyway.
  llm_provider: z.string(),
  llm_base_url: z.string(),
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
    llm_provider: (config.llm.provider as string) ?? "openai",
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

function formToConfig(
  values: FormValues,
  systemPrompt: string,
  existing?: AgentConfig,
): AgentConfig {
  return {
    name: values.name.trim() || existing?.name || "Pipeline",
    role: values.role.trim() || existing?.role || "supervisor",
    description: existing?.description ?? null,
    system_prompt: systemPrompt,
    llm: {
      // `as LLMProvider` — backend validates against its dispatch table; the
      // dropdown only offers known ids.
      provider: values.llm_provider as AgentConfig["llm"]["provider"],
      base_url: values.llm_base_url.trim(),
      api_key: values.llm_api_key,
      model: values.llm_model.trim(),
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
    llm_provider: llm.provider,
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

  const { data: skillsList = [] } = useQuery({
    queryKey: ["skills"],
    queryFn: () => listSkills(token!),
    enabled: !!token,
  });

  const { data: personas = [] } = useQuery({
    queryKey: ["personas"],
    queryFn: () => listPersonas(token!),
    enabled: !!token,
  });

  // Provider catalogue lives in the backend (app/api/providers.py) so adding
  // a provider is a one-line edit there. Fallback list keeps the form usable
  // during the first fetch.
  const { data: providers = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: () => listProviders(token!),
    enabled: !!token,
    staleTime: 5 * 60_000,
  });

  // Persona selection lives outside react-hook-form because PersonaPopup
  // controls it directly (mutations return the saved persona).
  const [personaId, setPersonaId] = useState<string>("");
  // Popup state. "new" = create empty; Persona = edit-in-place or copy-from-global.
  const [popup, setPopup] = useState<Persona | "new" | null>(null);

  // When personas load (or agent changes), try to auto-select the persona whose
  // system_prompt matches the agent's current prompt. If none match, leave
  // personaId="" — the existing prompt is preserved on save (custom prompt path).
  useEffect(() => {
    if (personas.length === 0) return;
    if (personaId) return;
    if (agent?.config.system_prompt) {
      const match = personas.find((p) => p.system_prompt === agent.config.system_prompt);
      if (match) setPersonaId(match.id);
    }
  }, [personas, agent, personaId]);

  const memType = watch("memory_type");
  const temperature = watch("llm_temperature");
  const selectedSkills = watch("skills");
  const selectedSubagents = watch("subagents");

  const isPaid = (user?.plan ?? "free") !== "free";
  const subagentOptions = allAgents.filter((a) => a.id !== agent?.id);

  // Root pipeline: hide Role (canvas always labels root "Supervisor"). For a
  // brand-new agent (no agent prop), assume root — that's the create path on
  // /pipelines. Sub-agent: keep Role visible so its role can describe its job.
  const isRoot = !agent || isPipelineRoot(agent, allAgents);

  const selectedPersona = personas.find((p) => p.id === personaId);

  function toggleItem(field: "skills" | "subagents", id: string, checked: boolean) {
    const current = field === "skills" ? selectedSkills : selectedSubagents;
    setValue(field, checked ? [...current, id] : current.filter((x) => x !== id));
  }

  async function onValid(values: FormValues) {
    // Persist LLM settings so new agents / sub-agents pre-fill from them
    saveLLMDefaults({
      provider: values.llm_provider as LLMProvider,
      base_url: values.llm_base_url,
      api_key: values.llm_api_key,
      model: values.llm_model,
      temperature: values.llm_temperature,
      max_tokens: values.llm_max_tokens,
    });
    // If a persona is picked, its prompt becomes the agent's prompt. If none
    // is picked (custom prompt path), keep whatever the existing agent had —
    // or fall back to the first persona's prompt for a brand-new agent.
    const systemPrompt =
      selectedPersona?.system_prompt
      ?? agent?.config.system_prompt
      ?? personas[0]?.system_prompt
      ?? "You are a helpful assistant.";
    await onSubmit(formToConfig(values, systemPrompt, agent?.config));
  }

  return (
    <form onSubmit={handleSubmit(onValid)} className="space-y-6">

      {/* ── Identity ──────────────────────────────────────────────────────── */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">Identity</h3>
        <div className={cn("grid gap-3", isRoot ? "grid-cols-1" : "grid-cols-2")}>
          <div className="space-y-1.5">
            <Label className="text-xs font-semibold text-gray-600">Name</Label>
            <Input {...register("name")} placeholder="Pipeline" className="focus-visible:ring-violet-300" />
          </div>
          {!isRoot && (
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Role</Label>
              <Input {...register("role")} placeholder="assistant" className="focus-visible:ring-violet-300" />
            </div>
          )}
        </div>

        {/* Persona — replaces the System Prompt textarea. The picked persona's
            prompt becomes the agent's system prompt at save time. */}
        <div className="mt-3 space-y-1.5">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-semibold text-gray-600">Persona</Label>
            <div className="flex gap-2">
              {selectedPersona && selectedPersona.owner_id !== null && (
                <button
                  type="button"
                  onClick={() => setPopup(selectedPersona)}
                  className="text-[11px] text-violet-600 hover:underline"
                >
                  Edit
                </button>
              )}
              {selectedPersona && selectedPersona.owner_id === null && (
                <button
                  type="button"
                  onClick={() => setPopup(selectedPersona)}
                  className="text-[11px] text-violet-600 hover:underline"
                >
                  Copy
                </button>
              )}
              <button
                type="button"
                onClick={() => setPopup("new")}
                className="text-[11px] text-violet-600 hover:underline"
              >
                + New
              </button>
            </div>
          </div>
          <select
            value={personaId}
            onChange={(e) => setPersonaId(e.target.value)}
            className="w-full h-9 border border-input rounded-md px-2 text-sm bg-background focus-visible:ring-2 focus-visible:ring-violet-300"
          >
            <option value="">— Custom prompt (keep existing) —</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}{p.owner_id === null ? " (default)" : ""}
              </option>
            ))}
          </select>
          {selectedPersona && (
            <p className="text-[11px] text-muted-foreground whitespace-pre-wrap line-clamp-4 bg-gray-50 border border-gray-200 rounded-lg p-2 mt-1">
              {selectedPersona.system_prompt}
            </p>
          )}
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

            {/* Provider dropdown — backend-driven catalogue. Base URL / Model /
                API Key are shown uniformly regardless of provider; backend
                build_chat_model handles provider-specific quirks. */}
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Provider</Label>
              <select
                {...register("llm_provider")}
                className="w-full h-9 border border-input rounded-md px-2 text-sm bg-background focus-visible:ring-2 focus-visible:ring-violet-300"
              >
                {providers.length === 0 ? (
                  <option value="">Loading…</option>
                ) : (
                  providers.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))
                )}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Base URL</Label>
              <Input
                {...register("llm_base_url")}
                placeholder="leave blank to use the provider's default"
                className="font-mono text-xs focus-visible:ring-violet-300"
              />
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-gray-600">Model *</Label>
              <Input
                {...register("llm_model")}
                placeholder="model name"
                className="focus-visible:ring-violet-300"
              />
              {errors.llm_model && <p className="text-xs text-destructive">{errors.llm_model.message}</p>}
            </div>

            {/* API key: hidden for paid plans, required for free */}
            {isPaid ? (
              <div className="px-4 py-3 bg-violet-50 border border-violet-200 rounded-lg">
                <p className="text-xs font-bold text-violet-700">API key — managed by your plan</p>
                <p className="text-xs text-violet-500 mt-0.5">Your {user?.plan} plan provides LLM access automatically.</p>
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label className="text-xs font-semibold text-gray-600">API Key</Label>
                <Input
                  {...register("llm_api_key")}
                  type="password"
                  placeholder="provider API key"
                  className="focus-visible:ring-violet-300"
                />
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

      <PersonaPopup
        open={popup !== null}
        initial={popup === "new" ? null : popup}
        onClose={() => setPopup(null)}
        onSaved={(p) => setPersonaId(p.id)}
        onDeleted={(deletedId) => {
          // If the deleted persona was selected, drop selection so save uses fallback.
          if (personaId === deletedId) setPersonaId("");
        }}
      />
    </form>
  );
}
