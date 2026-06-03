const KEY = "llm_defaults";

import type { LLMProvider } from "@/types";

export interface LLMDefaults {
  provider: LLMProvider;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

// No factory provider/model defaults: pre-filling values the user never saved
// is misleading (e.g. "gpt-4o-mini" implies they have an OpenAI key). Only
// numeric tunables get sensible defaults — provider/base_url/model/api_key
// stay empty until the user picks a provider or types their own.
const FACTORY: LLMDefaults = {
  provider: "openai",
  base_url: "",
  api_key: "",
  model: "",
  temperature: 0.7,
  max_tokens: 1024,
};

export function getLLMDefaults(): LLMDefaults {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...FACTORY, ...JSON.parse(raw) };
  } catch { /* ignore */ }
  return { ...FACTORY };
}

export function saveLLMDefaults(d: Partial<LLMDefaults>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...getLLMDefaults(), ...d }));
  } catch { /* ignore */ }
}
