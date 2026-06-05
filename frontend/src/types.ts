export interface User {
  id: string;
  email: string;
  name?: string;
  slack_user_id?: string | null;
  is_active: boolean;
  plan: "free" | "paid" | "admin";
}

export type LLMProvider = "openai" | "anthropic" | "google" | "vllm";

export interface LLMConfig {
  provider: LLMProvider;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
  timeout_s: number;
}

export interface MemoryConfig {
  type: "none" | "buffer" | "summary";
  window: number;
  summary_threshold: number;
}

export interface Limits {
  max_steps: number;
}

export interface ChannelBinding {
  channel: "slack" | "web" | "whatsapp";
  external_id: string;
}

export interface AgentConfig {
  name: string;
  role: string;
  description?: string | null;
  system_prompt: string;
  llm: LLMConfig;
  tools: string[];
  memory: MemoryConfig;
  limits: Limits;
  subagents: string[];
  skills: string[];
  mcp_servers: string[];
  channels: ChannelBinding[];
  metadata: Record<string, unknown>;
}

export interface Agent {
  id: string;
  name: string;
  config: AgentConfig;
  // ISO timestamp when the pipeline was deployed; null = Draft (cannot be used in chats/Slack).
  deployed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Persona {
  id: string;
  name: string;
  system_prompt: string;
  owner_id?: string | null;
}

export interface Skill {
  id: string;
  name: string;
  content: string;
  owner_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Chat {
  id: string;
  agent_id: string;
  agent_name?: string | null;
  channel: "web" | "slack" | "whatsapp";
  external_thread_id?: string | null;
  title?: string | null;
  // Snippet of the first user message — populated by /chats list, undefined elsewhere.
  preview?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  run_id?: string | null;
  sender: string;
  recipient?: string | null;
  content: string;
  ts: string;
}

export interface Run {
  id: string;
  chat_id: string;
  agent_id?: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "interrupted";
  started_at: string;
  ended_at?: string | null;
  total_tokens: { prompt: number; completion: number; total: number };
  total_cost: number;
  error?: string | null;
}

export interface RunEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface SendMessageResponse {
  run_id: string;
  chat_id: string;
}

export interface MCPServer {
  id: string;
  name: string;
  url: string;
  transport: "http" | "sse";
  headers: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface ToolConfig {
  tool_name: string;
  config: Record<string, string>;
  created_at: string;
  updated_at: string;
}
