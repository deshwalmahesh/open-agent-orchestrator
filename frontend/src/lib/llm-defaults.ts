const KEY = "llm_defaults";

export interface LLMDefaults {
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

const FACTORY: LLMDefaults = {
  base_url: "https://api.openai.com/v1",
  api_key: "EMPTY",
  model: "gpt-4o-mini",
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
