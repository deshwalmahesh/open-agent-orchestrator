# Open Agent Orchestrator

Visual multi-agent pipelines (you know them as "Multi Agent workflows"). Build a supervisor with sub-agents, tools, and MCP servers on a canvas. Talk to it from the web, Slack, or WhatsApp. Works with any OpenAI-compatible LLM, Anthropic, or Gemini.

![Canvas](assets/canvas.png)

# Usage

```bash
cp .env.example .env && docker compose up
```

→ `http://localhost` (UI) · `http://localhost:8000/docs` (API)

# Demo

![Demo](assets/demo.gif)

---

## Features

Ordered by impact — most useful first.

1. **Visual canvas for multi-agent pipelines.** React Flow + dagre. Drag a supervisor, attach sub-agents (recursive ReAct loops, depth-4 cap, cycles rejected at save), wire in built-in tools and MCP servers. Double-click to edit, hover to attach, color-coded by node type (supervisor > sub-agent > tool/MCP).
2. **Multi-channel, one inbox.** Web chat with file attachments (PDFs + images), Slack via Socket Mode (no public URL — Bolt + xapp-/xoxb- tokens), AND WhatsApp via Twilio (webhook-based, per-user credentials). All channels land as `ChatDB` rows and show up in the same `/chats` sidebar. Slack DMs keyed by `(channel_id, thread_ts)`; WhatsApp threads keyed by contact phone number. One pipeline per channel active at a time; switch live without restart.
3. **Bring-your-own LLM, any provider.** OpenAI, Anthropic, Google Gemini, vLLM, or anything OpenAI-compatible. Provider list is backend-driven (`GET /providers`) — add one with a one-line edit to `app/llm.py`. User-entered keys live in their browser localStorage (no server-side BYOK storage).
4. **MCP integration.** Register external MCP servers, auto-discover their tools at attach time, bind them to any sub-agent. A sample MCP server is included for testing.
5. **Reusable, modifiable pipelines.** Every agent is independently addressable: it can be a *root* (pipeline) AND simultaneously be wired into another pipeline as a sub-agent — no separate "template" type, no clone-to-fork (computed `pipeline = NOT referenced as subagent`). Sub-agents, tools, personas (named system prompts), skills (reusable knowledge docs), and tool credentials are all account-scoped, attachable to N pipelines, and modifiable in place — edits propagate to every pipeline that references them. Symmetric Attach / Detach / Delete UI on every reusable.
6. **Live run monitoring.** SSE event stream per run: `run.started`, `tool.start`/`end`, `agent.message`, `usage`, `run.finished`. Backlog replay + live feed. Token accounting aggregates across sub-agent calls (contextvar propagation).
7. **Draft → Deployed gating.** Pipelines start as Draft. They can't be used in chat or Slack until you explicitly click Deploy (which validates `llm.model`, `system_prompt`, sub-agent tree). Edits after deploy stay Deployed — no surprise re-validation.
8. **Per-user tool credentials with pre-save validation.** `POST /tool-configs/{tool}/validate` pings the upstream (e.g. Tavily one-result search) before storing the key. Errors are scrubbed — submitted keys never bounce back to the client.
9. **Rolling-summary memory.** N=10 verbatim tail, fold the oldest M=20 into `ChatDB.summary` once they exceed the threshold. `MessageDB` rows are never deleted (full audit trail).
10. **Graceful recursion-limit fallback.** When LangGraph hits `max_steps`, the run finishes with a clean apology message instead of a half-baked tool-result tail.
11. **Multi-user, multi-pipeline, multi-chat.** `fastapi-users` JWT auth. Each user owns N pipelines; each pipeline backs N chats (web + Slack threads); each chat carries its own `MessageDB` history + rolling summary. Cross-user reads return `404 not 403` (no existence leak). Reassigning a chat to a different Deployed pipeline is a single PATCH.
12. **SQLite by default, Postgres by URL swap.** Idempotent migrations via SQLAlchemy `inspect()` — no Alembic in v1, no startup error logs from "column already exists".
13. **Docker Compose dev loop.** Full bind mount + anonymous volumes for `node_modules` / `.venv` → `docker compose down && up` = truly fresh install; restarts in between are instant. Vite polling enabled so macOS Docker bind mounts hot-reload reliably.
14. **Durable, horizontally-scalable execution.** `POST /messages` writes a row + enqueues to an [arq](https://arq-docs.helpmanual.io/) Redis queue and returns in ms — the API never blocks on an LLM. Workers pull at their own rate (bounded `max_jobs` = backpressure), crashes redeliver (at-least-once + idempotent `_execute`), and a startup reconciler reaps orphans. Stateless API + worker pods scale independently: **HPA** on API CPU, **KEDA** on worker queue-depth. A global load-shed cap returns `503` instead of melting under a burst. (`RUN_EXECUTOR=inline` for single-box dev — same code path.)
15. **Multi-tenant security & cost controls.** Tenant secrets (BYOK LLM keys, Slack/Twilio tokens) are **Fernet-encrypted at rest** (`MultiFernet` key rotation). Per-plan **concurrency caps** + **daily token quotas** (`429` when exceeded) + per-model **cost metering** (`total_cost` per run). Redis-backed rate limiting across replicas. Prod **fail-fast** refuses to boot with a default `JWT_SECRET` or missing encryption keys.
16. **Observability, off-the-shelf.** Optional **Langfuse** tracing (drop-in callback → per-run tool/sub-agent/token/latency spans, incl. MCP) + **Prometheus** `/metrics` (HTTP RED metrics free, plus `runs_total`/`queue_depth`) for Grafana dashboards & alerts. In-app **thumbs up/down feedback** + per-user usage stats (`/stats`). No vendor lock-in.

---

## What's in the box

```
backend/                          FastAPI + LangGraph
  app/
    api/                          REST routers (one file per resource)
      agents.py                   AgentConfig CRUD + sub-agent tree validation + POST /deploy
      chats.py                    Chat CRUD + reassign + message send (Draft pipelines rejected)
      runs.py                     Run status + SSE event stream
      providers.py                LLM provider catalogue (id + label)
      mcp_servers.py              MCP CRUD + live tool discovery
      personas.py / skills.py     Reusable system-prompt fragments / knowledge docs
      tool_configs.py             Per-user tool credentials (validate before save)
      slack.py                    Connect / disconnect / set-active (per-user)
      whatsapp.py                 Connect / disconnect / set-active + public webhook
      stats.py                    Per-user usage stats (runs, reviews, top tools)
      health.py                   /health, /health/ready, /metrics/queue-depth
    runtime/
      agent.py                    build_agent_tree() — recursive ReAct, contextvar token aggregation
      tools.py                    Built-in tool registry (calculator, web_search, html_to_md, pdf_to_text, python_sandbox)
      checkpointer.py             Redis checkpointer (within-run state)
      events.py                   Per-run event emitter (DB + cross-process Redis pub/sub)
      usage_callback.py           UsageCounter — one callback counts every tool/sub-agent/MCP call
    services/run_service.py       Schedule + execute + persist + emit (+ quota/concurrency/load-shed)
    worker.py                     arq worker entrypoint (RUN_EXECUTOR=queue) — durable execution
    integrations/
      channels/slack_adapter.py   Bolt Socket Mode adapter (+ shared format_reply)
      channels/whatsapp_adapter.py Twilio WhatsApp adapter (per-user, stateless REST)
      sample_mcp_server.py        Demo MCP server (timestamp + word_count tools)
    db/
      models.py                   SQLAlchemy 2.0 async ORM (+ encrypted columns)
      repos.py                    Plain async helpers (caller owns the session)
      seeds/personas.yaml         Default personas, loaded on startup
    llm.py                        build_chat_model() + retry + circuit breaker
    crypto.py                     EncryptedStr/EncryptedJSON — Fernet secrets-at-rest
    quota.py                      Daily token quota (Redis) + per-model cost table
    plans.py                      Per-plan limits (concurrency / daily tokens)
    metrics.py                    Prometheus counters/gauges (runs_total, queue_depth)
    observability.py              Langfuse tracing (env-gated, no-op when unset)
    errors.py                     Failure taxonomy — stable error_code + user message + retry policy
    redis_client.py               Shared coordination Redis (pub/sub, dedup, leader lock, quota)
    leader.py                     Redis leader lock (single-runner Slack Socket Mode)
    migrate.py                    One-shot schema bootstrap (Helm pre-upgrade Job)
    domain.py                     Pydantic schemas (AgentConfig, LLMConfig, MemoryConfig, RunEvent)
    main.py                       FastAPI app + lifespan (Slack autostart, reconciler, /metrics)

frontend/                         React + Vite + ReactFlow + TanStack Query + shadcn/Tailwind
  src/
    pages/
      Agents.tsx                  /pipelines list (Draft pill + Deploy button + hover Slack-active swap)
      Canvas.tsx                  /pipelines/:id/canvas (header: Draft pill, Save/Deploy button)
      Chat.tsx                    /chats (sidebar grouped by pipeline, message bubbles, SSE ticker)
      Integrations.tsx            /integrations (Slack card: per-user connect/edit/disconnect)
      Personas.tsx                /personas (CRUD + copy-default-as-mine)
      Skills.tsx                  /skills (CRUD)
      Login.tsx
    components/
      AgentCanvas.tsx             ReactFlow scene + left "Add Connection" panel (Sub-Agents / Tools / MCP)
      AgentForm.tsx               Full edit form (provider dropdown driven by /providers)
      PersonaPopup.tsx            Shared big-textarea dialog: New / Edit / Copy-from-default
      Layout.tsx, RunEventsPanel.tsx, ui/* (shadcn)
    api/                          One client per resource, all hit /api (Vite proxy → backend)
    hooks/                        useAuth, useSSE
    lib/                          llm-defaults (localStorage BYOK), utils, isPipelineRoot

deploy/helm/orchestrator/         Helm chart: api + worker + HPA + KEDA + migrate Job + ConfigMap/Secret
.github/workflows/ci.yml          CI: lint → secret/CVE/SAST scan → test (real Redis+PG) → helm lint → build+Trivy→GHCR
Dockerfile                        Single image: builds frontend → static/, serves both (one-container deploy)
docker-compose.yml                postgres + redis + backend + worker + mcp-sample + frontend
```

---

## Architecture

### System flow

Queue mode (prod) shown below. `RUN_EXECUTOR=inline` runs the same `_execute` in the API process for single-box dev — identical code path, no worker.

```mermaid
flowchart LR
  %% ---- clients & channels ----
  subgraph EDGE["Clients & channels"]
    WEB["Web UI<br/>React + ReactFlow"]
    SLACK["Slack<br/>Socket Mode"]
    WAPP["WhatsApp<br/>Twilio webhook"]
  end

  %% ---- API tier (stateless, scale on CPU) ----
  subgraph API["API pods · FastAPI — HPA on CPU"]
    GATE["POST /messages<br/>JWT · quota · concurrency · load-shed"]
    ENQ["start_run → enqueue<br/>returns 202 in ms (never blocks on LLM)"]
    SSE["GET /runs/:id/events<br/>SSE: DB backlog + live tail"]
    MET1["GET /metrics"]
  end

  %% ---- shared Redis ----
  subgraph REDIS["Redis (shared coordination)"]
    Q[("arq queue · ZSET")]
    PS[("pub/sub · run:{id}")]
    CK[("checkpointer · dedup<br/>leader lock · quota counter")]
  end

  %% ---- worker tier (stateless, scale on queue depth) ----
  subgraph WORK["Worker pods · arq — KEDA on queue depth"]
    EX["execute_run → _execute<br/>idempotent · timeout · retry · breaker"]
    TREE["build_agent_tree<br/>recursive ReAct, depth-4"]
    LLM["LLM dispatch<br/>OpenAI / Anthropic / Gemini / vLLM"]
    TOOLS["built-in tools · MCP · sub-agents"]
    MET2["/metrics :9100"]
  end

  PG[("Postgres / SQLite<br/>secrets Fernet-encrypted at rest")]
  LF["Langfuse<br/>(optional traces)"]
  PROM["Prometheus → Grafana"]

  WEB -->|"REST"| GATE
  SLACK --> ENQ
  WAPP --> ENQ
  GATE --> ENQ
  ENQ -->|"enqueue (job_id = run_id)"| Q
  Q -->|"pull, bounded by max_jobs"| EX
  EX --> TREE --> LLM
  TREE --> TOOLS
  EX <-->|"sessions, none held over LLM"| PG
  EX -->|"publish events"| PS
  PS -->|"subscribe"| SSE
  SSE -->|"SSE"| WEB
  EX -.->|"checkpoint / counters"| CK
  ENQ -.->|"dedup / leader / quota"| CK
  LLM -.->|"traces"| LF
  TOOLS -.->|"traces"| LF
  MET1 --> PROM
  MET2 --> PROM

  classDef edge fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a;
  classDef api fill:#dcfce7,stroke:#22c55e,color:#14532d;
  classDef work fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95;
  classDef data fill:#fee2e2,stroke:#ef4444,color:#7f1d1d;
  classDef store fill:#cffafe,stroke:#0891b2,color:#155e75;
  classDef obs fill:#fef9c3,stroke:#ca8a04,color:#713f12;

  class WEB,SLACK,WAPP edge;
  class GATE,ENQ,SSE,MET1 api;
  class EX,TREE,LLM,TOOLS,MET2 work;
  class Q,PS,CK data;
  class PG store;
  class LF,PROM obs;
```

### Data model (multi-user → multi-pipeline → multi-chat)

```mermaid
flowchart TD
  U["UserDB<br/>(JWT identity · plan · slack_user_id<br/>· Twilio creds — tokens encrypted at rest)"]
  A1["AgentDB<br/>(root = pipeline · config encrypted)"]
  A2["AgentDB<br/>(sub-agent)"]
  A3["AgentDB<br/>(sub-agent — shared)"]
  CH1["ChatDB<br/>channel='web'"]
  CH2["ChatDB<br/>channel='slack'<br/>external_thread_id=C123:1.234"]
  CH4["ChatDB<br/>channel='whatsapp'<br/>external_thread_id=whatsapp:+1234"]
  CH3["ChatDB<br/>channel='web'"]
  M["MessageDB<br/>(append-only, full audit)"]
  R["RunDB + RunEventDB<br/>(status incl. awaiting_human · tokens · cost<br/>· tool_calls · error_code · SSE backlog)"]
  FB["FeedbackDB<br/>(thumbs ± comment,<br/>one per user+run)"]
  P["PersonaDB / SkillDB<br/>(account-scoped, attach to N agents)"]
  MCP["MCPServerDB / UserToolConfigDB<br/>(per-user creds — encrypted, validated pre-save)"]

  U -->|"owns N"| A1
  U -->|"owns N"| A2
  U -->|"owns N"| A3
  A1 -->|"references"| A2
  A1 -->|"references"| A3
  A2 -->|"can also be a pipeline root"| CH3
  A1 -->|"backs N"| CH1
  A1 -->|"backs N Slack threads"| CH2
  A1 -->|"backs N WhatsApp contacts"| CH4
  CH1 -->|"has many"| M
  CH2 -->|"has many"| M
  CH1 -->|"triggers"| R
  CH2 -->|"triggers"| R
  R -.->|"rated by"| FB
  U -.->|"leaves"| FB
  A1 -.->|"uses"| P
  A1 -.->|"uses"| MCP

  classDef user fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a;
  classDef agent fill:#dcfce7,stroke:#22c55e,color:#14532d;
  classDef chat fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95;
  classDef run fill:#fee2e2,stroke:#ef4444,color:#7f1d1d;
  classDef reuse fill:#fef9c3,stroke:#ca8a04,color:#713f12;
  classDef fb fill:#cffafe,stroke:#0891b2,color:#155e75;

  class U user;
  class A1,A2,A3 agent;
  class CH1,CH2,CH3,CH4 chat;
  class M,R run;
  class P,MCP reuse;
  class FB fb;
```

Key relationships:
- **User → Pipelines:** one user owns many root agents (pipelines). The same `AgentDB` row can be a root AND be referenced as a sub-agent by another pipeline — no `is_pipeline` flag, computed on read.
- **Pipeline → Chats:** one pipeline backs many chats. Web chats are user-created; Slack chats auto-spawn on first DM, keyed by `(channel_id, thread_ts)`; WhatsApp chats auto-spawn on first inbound message, keyed by contact phone number — all appear in the same `/chats` sidebar.
- **Chat → Messages:** append-only `MessageDB` for full audit; older messages fold into `ChatDB.summary` (N=10 tail, M=20 fold).
- **Reusables:** Sub-agents, personas, skills, MCP servers, and tool credentials are user-scoped and can be attached to any number of pipelines; edits propagate live.

### Layering

Three boundaries, kept thin:
- **Control plane** (`api/`) — body validation, owner checks, call repos/services.
- **Runtime** (`runtime/`, `services/`) — agent-tree compilation, run scheduling, memory, retries.
- **Persistence** (`db/`) — one ORM file, repo helpers, idempotent bootstrap.

**No DB session held during the LLM call.** `_execute` splits a turn into pre-LLM (load + insert user message), LLM (build tree + invoke — `build_agent_tree` takes a `session_factory`, not a session), post-LLM (insert agent reply + finalize). Keeps the pool free under concurrent load.

---

## How it works — every major workflow, explained twice

Two readers, one section. **Each workflow opens with a one-line "what it is" for anyone**, then **"Behind the scenes"** narrates what actually happens across the moving parts — the design: who talks to whom, when, and *why* — in plain words. Each closes with a **Code map** line for engineers who want the exact files. No wall of symbols, no hand-waving.

**The moving parts, named once (so the rest reads easily):**

| Part | Plain-English job |
|---|---|
| **Browser (React)** | What you click. Builds the config, sends messages, shows the live feed. |
| **API (FastAPI)** | The front door. Checks who you are, validates, writes to the database, hands heavy work to the queue. **Never calls the AI itself.** |
| **Queue (Redis / arq)** | A to-do list of runs waiting to execute. The shock absorber under load. |
| **Worker** | The process that actually runs an agent — calls the LLM, tools, sub-agents. Same code whether it's a separate machine (prod) or a background task inside the API (dev). |
| **Database (Postgres/SQLite)** | The source of truth: users, pipelines, chats, messages, runs. |
| **Checkpointer (Redis)** | A save-point of a run's in-progress state, so a paused run can resume exactly where it stopped. |
| **Event stream (Redis pub/sub + SSE)** | The live ticker that pushes "the agent just called web_search" to your screen. |

> **The one idea to hold on to:** a "pipeline" is **one agent**, described by **one settings object** ("the config"). Everything the agent can do is a field in that object. There is no separate workflow/DAG/template — the agent *is* the workflow. Build config → deploy → talk to it → it runs a think-act loop → optionally pauses for a human → resumes.

---

### Workflow 1 — You get an account (and why you never see anyone else's stuff)

**In one line:** you sign up, and from that moment everything you make is tagged as yours and invisible to everyone else.

**Behind the scenes:**
1. You register with email + password. The API stores you as a **user row**; the password is hashed, never kept in the clear.
2. You log in and get a **token** — a signed pass that proves "I am user X." Every later request carries it.
3. From then on, *every* thing you create — pipelines, chats, tool keys — is stamped with your user id. When you ask for something, the database lookup is always "give me row Y **where owner = me**." If it isn't yours, the answer is a flat **"not found" (404), not "forbidden" (403)** — so you can't even probe whether someone else's thing exists.

**Why this way:** one system, many tenants. The owner-stamp + "404 not 403" *is* the isolation model — dead simple, nothing to leak across users by accident.
**Code map:** `users.py` (JWT via fastapi-users); every repo helper joins through `user_id` (e.g. `get_run`); routers raise 404 on a miss.

---

### Workflow 2 — You build a pipeline (an "agent") and wire capabilities onto it

**In one line:** you fill in a form — name, instructions, which AI model, which tools/helpers — and that becomes one reusable "agent."

**Behind the scenes:**
1. In the browser you set the basics: a **name**, a **system prompt** (its standing instructions), and the **LLM** (provider, model, *your* API key, how creative, how long it may answer).
2. You **attach capabilities**. Every attach just adds an id or a name to a list on the config — attaching is *not* drawing a wire in a database, it's literally putting an id into a list:
   - built-in **tools** → names go into a `tools` list;
   - **sub-agents** (other agents it can delegate to) → their ids go into `subagents`;
   - **MCP servers** (external tool providers) → their ids go into `mcp_servers`;
   - **skills** (reusable knowledge docs) → their ids go into `skills`;
   - a **persona** (a named, reusable system prompt) → picking it fills in the system prompt then and there.
3. You hit save. The browser sends the whole settings object to the API. **The API validates the helper tree before writing anything:** it walks the sub-agents and refuses loops ("A calls B calls A"), refuses nesting deeper than 4, refuses pointing at someone else's agent, refuses a sub-agent whose name collides with a built-in tool.
4. If valid, the API stores the whole object as **one row** (as JSON), tagged with your user id, with the secret parts (your API key) **encrypted before they touch disk.** Only the name and a "deployed?" timestamp live in separate columns — because those are the only things the list screen sorts and filters on.

**Why one JSON blob, not a big relational schema:** the agent is read as a whole and rebuilt from scratch on every run. Storing it as one document makes editing trivial and means there's no "compiled" copy to keep in sync.
**The reuse trick:** because sub-agents/skills/personas are referenced *by id*, the same one attaches to many pipelines, and editing it once changes every pipeline that uses it — no copies.
**Code map:** `AgentForm.tsx` → `POST/PUT /agents` (`api/agents.py`, `_validate_subagent_tree`) → `AgentDB.config` (`db/models.py`, encrypted via `crypto.py`); the shape is `AgentConfig` in `domain.py`.

---

### Workflow 3 — Where tools come from and how they "connect"

**In one line:** by the time the AI can use a tool it's just a small callable function — but those functions arrive from four different places, at different times.

**Behind the scenes — the four sources, and when each shows up:**
1. **Built-in tools** — a fixed catalogue that ships with the app (calculator, fetch a web page, PDF→text, a tiny Python sandbox, "ask a human"). The config lists them **by name**; at run time the app looks each name up in its catalogue. An unknown name is silently skipped — never a crash.
2. **A built-in that needs a key** — web search (Tavily). It only *exists* for an agent if a key is available: the platform's key, or **your own** key that you saved and validated. No key → the tool simply isn't there (fails safe, no error).
3. **MCP tools (external, discovered live)** — you register an MCP server (a URL). These are **not** in any catalogue; at run time the app *connects to that server and asks it what tools it has.* Server down? The agent just builds without them (logged, not fatal).
4. **Sub-agents as tools** — each helper agent is wrapped so the parent can "call" it like a tool with a single instruction; the helper then runs its own full loop and returns an answer.

**Why the distinction matters:** built-ins are stable and free; credentialed ones appear/disappear with your keys; MCP ones depend on a live external service each run; sub-agents are recursive (and capped at depth 4 so a tree can't explode).
**Code map:** `runtime/tools.py` (`REGISTRY`, `build_registry`, `get_tools`); MCP discovery + sub-agent wrapping in `runtime/agent.py` (`build_agent_tree`, `_make_subagent_tool`).

---

### Workflow 4 — You flip a pipeline from Draft to Deployed

**In one line:** a new pipeline is a rough draft you can't chat with yet; clicking Deploy is the "it's ready" switch.

**Behind the scenes:**
1. New pipelines start as **Draft** (no "deployed" timestamp). Trying to chat with a Draft is refused up front.
2. You click **Deploy.** The API re-checks the essentials — it has a model, it has instructions, its helper tree is still valid — and stamps it with a "deployed at" time.
3. After that, editing it does **not** knock it back to Draft. Re-validation happens only if you deploy again — no surprise reversions.

**Why the gate exists:** so half-finished agents can't be used by accident (or wired to Slack) before you've said they're ready.
**Code map:** `POST /agents/{id}/deploy` (`api/agents.py`); "Draft" = `deployed_at IS NULL`; chat creation/reassign guard on it in `api/chats.py`.

---

### Workflow 5 — You send a message and watch the answer arrive (the core loop)

This is the heartbeat of the product.

**In one line:** you type a message; the system accepts it instantly, does the slow AI work in the background, and streams the progress back to your screen live.

```mermaid
sequenceDiagram
  autonumber
  actor U as You (browser)
  participant API as API pod (FastAPI)
  participant R as Redis (queue + pub/sub)
  participant W as Worker (arq)
  participant DB as Postgres / SQLite
  participant LLM as LLM + tools

  U->>API: POST /chats/{id}/messages (JWT, text, files)
  API->>API: owner check, size 413, concurrency 429, quota 429, load-shed 503
  API->>DB: INSERT run (status=queued)
  API->>R: enqueue job (job_id = run_id)
  API-->>U: 202 accepted, returns run_id
  U->>API: GET /runs/{id}/events?token= (SSE opens)
  W->>R: pull job (bounded by max_jobs)
  W->>DB: idempotency gate, mark running, insert user msg, load memory
  W->>W: build_agent_tree (compile ReAct + checkpointer)
  loop ReAct steps (bounded by max_steps)
    W->>LLM: model call to tool / sub-agent / MCP
    LLM-->>W: result
    W->>DB: INSERT run_event (seq++)
    W->>R: PUBLISH run:{id}
    R-->>API: event
    API-->>U: SSE event (tool.start, agent.message, usage)
  end
  W->>DB: insert agent reply, finalize run (succeeded, tokens, cost)
  W->>R: PUBLISH run.finished
  API-->>U: SSE run.finished, stream closes
```

**Behind the scenes:**
1. **You hit send.** The browser posts your message to the API.
2. **The API does the cheap, safe stuff only — it never calls the AI.** It checks you own this chat, rejects oversized attachments, and checks you're under your plan's limits (too many runs in flight? over today's token budget? queue already swamped?). Any of those → a polite "try later," not a hang.
3. **It writes a "run" record and drops a job on the queue,** then answers you in *milliseconds* with a run id. This is the key move: **accepting work is separated from doing work.**
4. **Your browser opens a live feed** for that run id (a one-way stream from the server).
5. **A worker picks the job off the queue.** In production that's a separate machine; in dev it's a background task in the same process — *the exact same code either way.*
6. **The worker assembles the conversation and the agent.** It reads your message plus the recent history (Workflow 6), then **builds the agent fresh from its config** — resolves tools, connects to MCP servers, wraps sub-agents, attaches the save-point system. Crucially it does **not** keep a database connection open while the AI thinks: it loads what it needs, releases the connection, then thinks. (That's why a small connection pool can serve far more than that many simultaneous runs.)
7. **The agent runs its think→act loop.** The model reads the request, maybe calls a tool or a sub-agent, reads the result, thinks again — bounded by a step limit so it can't loop forever. Every notable moment (tool started, message produced, tokens used) is **both saved to the database and pushed onto the live feed.**
8. **The feed reaches your screen.** The worker publishes each event to a channel named after the run; the API is subscribed and forwards it down your open stream. Because events are *also* saved, a browser that connects late gets the full backlog first, then live updates — nothing missed, even across a page refresh.
9. **The run finishes.** The final answer is saved as the agent's reply; the run is marked done with total tokens + dollar cost + a tally of which tools were used; today's usage is incremented; a "finished" event closes your stream.

**Why the queue in the middle:** a flood of 10,000 messages becomes 10,000 cheap "write + enqueue" operations, not 10,000 blocked AI calls. The queue buffers the spike; workers drain at a safe pace; past capacity the system says "try later" instead of melting.
**Code map:** `POST /chats/{id}/messages` → `start_run` → `_execute`/`_drive` (`services/run_service.py`); events in `runtime/events.py`; live stream in `api/runs.py::stream_events`; worker entry in `worker.py`.

---

### Workflow 6 — How the agent "remembers" the conversation

**In one line:** the agent has no memory of its own — every turn, the history is rebuilt from the database, and old stuff is compressed so prompts stay small and cheap.

**Behind the scenes:**
1. The agent starts each turn blank. The system loads this chat's messages from the database.
2. To keep it cheap, only the **most recent ~10 messages are kept word-for-word.** Once the backlog of un-summarized messages grows past a threshold, the oldest chunk is folded into a **running summary** with a single compression call, and that summary is saved on the chat.
3. Next turn the agent sees: its instructions + the running summary ("here's what happened earlier") + the last few messages verbatim.
4. The raw messages are **never deleted** — the summary is a convenience, the full transcript stays for audit.

**Why:** verbatim history gets expensive fast; summarizing in batches keeps the prompt small while staying coherent. And keeping history in the *database* (not inside the agent) is exactly what lets you switch a chat to a different agent mid-conversation.
**Code map:** `_resolve_context` + `_summarize` (`run_service.py`); knobs in `MemoryConfig` (`domain.py`); stored on `ChatDB.summary`/`summary_count`.

---

### Workflow 7 — Multi-agent: a supervisor handing work to sub-agents

**In one line:** a "manager" agent can delegate parts of a job to specialist agents, each of which does its own thinking and reports back.

**Behind the scenes:**
1. When you attach agent B as a sub-agent of A, B becomes — from A's point of view — **a tool called "B" that takes one instruction.**
2. During A's loop, if the model decides to use B, the system spins up B **as its own full agent** (its own model, its own tools, its own loop) with the instruction A gave it.
3. B runs to an answer and hands a string back to A, exactly like any other tool result. A keeps going.
4. This nests — B can have its own sub-agents — but only **4 levels deep,** and loops are forbidden (checked when you save).
5. **Token bookkeeping across the tree:** a shared tally rides along so the tokens B (and B's sub-agents) spend are added to the parent run's total — they'd otherwise vanish, because the parent only ever sees B's final *text,* not its internal usage.

**Why a tree, not a free-for-all graph:** the agent itself *is* the workflow; making sub-agents "just tools" means the same delegation machinery, retries, and accounting apply at every level with zero special cases.
**Code map:** `_make_subagent_tool`, the `_SUBAGENT_USAGE` accumulator, `MAX_AGENT_DEPTH = 4` (`runtime/agent.py`).

---

### Workflow 8 — Human-in-the-loop: the agent pauses to ask a person

**In one line:** for risky or ambiguous steps the agent can stop and wait for a human's yes/no or answer — for as long as it takes — then continue from the exact spot.

```mermaid
sequenceDiagram
  autonumber
  actor U as Human
  participant CH as Channel (web / Slack / WhatsApp)
  participant RS as run_service (_drive)
  participant MW as HIL middleware
  participant CK as Redis checkpointer
  participant DB as Postgres / SQLite

  Note over RS,MW: run in progress, ReAct loop
  MW->>CK: interrupt() before ask_human / hil_tool, save checkpoint
  RS->>DB: pause_run_for_human (status=awaiting_human, store question + partial tokens)
  RS->>CH: emit human.requested (the question)
  CH-->>U: shows the question, run paused (NOT finalized)
  U->>CH: reply ("yes" / an answer / edit / reject)
  alt web UI
    CH->>RS: POST /runs/{id}/resume (decisions)
  else Slack / WhatsApp
    CH->>RS: next message, start_run detects paused run, maps text to a decision
  end
  RS->>DB: mark_run_resumed (atomic awaiting_human -> running)
  RS->>CK: Command(resume=decisions) replays same thread_id = run_id
  Note over RS,MW: ReAct continues from the exact pause point
  RS->>DB: finalize run (succeeded), tokens = checkpoint root + accumulated sub-agent
```

**Behind the scenes — pausing:**
1. You enable it two ways: a general **"ask a human" tool** the agent uses whenever *it* feels stuck, or a list of **specific tools that always need sign-off** before they run.
2. When the agent reaches one of those, a built-in guard **freezes the run right before the tool executes** and takes a **snapshot of the run's exact state** into the save-point store (the checkpointer).
3. The system marks the run **"waiting for human,"** stores the pending question, and — importantly — **does not treat this as finished or failed.** It won't be timed out or garbage-collected; it can wait for hours. It also doesn't count against your "runs in flight" limit.
4. The question is delivered wherever you are: pushed to the web feed, or sent as a Slack/WhatsApp reply.

**Behind the scenes — resuming:**
5. You answer. On the web you can approve / edit the tool's inputs / reject with a reason / or just answer the question. On Slack/WhatsApp — where there are no buttons — your **next message** is taken as the answer ("yes"/"ok" = approve; anything else = reject-with-that-text, or the literal answer).
6. A safety step: the system **flips the run from "waiting" to "running" in one atomic move.** If two answers race in, only one wins and actually resumes; the other harmlessly does nothing.
7. It **rebuilds the same agent and loads the snapshot,** feeds in your decision, and the loop **continues from the exact point it paused** — as if it never stopped.
8. Token counting stays honest across the pause: the main agent's usage is recovered from the snapshot; sub-agent usage from before the pause was stashed and is added back — so the final bill is correct even if it paused several times.

**Why snapshots (the "checkpointer"):** you can't hold a live program frozen in memory for hours across server restarts. Saving the run's state to Redis lets *any* worker pick it up later and continue precisely — that's what makes hours-long human waits survivable, and it needs Redis (the app refuses to build a human-in-the-loop agent without it, loudly, rather than crashing mid-run).
**Code map:** flags `ask_human_enabled`/`hil_tools` (`domain.py`) → `build_middleware` wires LangChain's `HumanInTheLoopMiddleware` (`runtime/middleware.py`); pause/resume in `_drive`/`resume_run` with `pause_run_for_human`/`mark_run_resumed` (`run_service.py` / `repos.py`); snapshot store in `runtime/checkpointer.py`.

---

### Workflow 9 — Forced chains: steps the AI is not allowed to skip

**In one line:** sometimes you must *guarantee* an order ("never answer before the validator checks it") — forced rules make those steps mandatory instead of optional.

**Behind the scenes:**
1. A normal agent is free to skip a tool. Forced rules remove that freedom for specific edges.
2. Two shapes: **"must run X before finishing"** — if the model tries to answer without having run X, the system injects a call to X first and routes back; and **"after X, force Y next"** — once X produces a result, Y is made the next step.
3. Each forced step is injected only once (it's tracked), and the whole thing is still bounded by the step limit — no infinite forcing.

**Why:** free-form reasoning is great until you need a hard guarantee (a validation, a review, a required tool). This adds deterministic guardrails without giving up the flexible loop everywhere else.
**Code map:** `forced_rules` (`domain.py`) → `ForcedChainMiddleware` (`runtime/middleware.py`), applied at the root agent only.

---

### Workflow 10 — Talking to it from Slack / WhatsApp

**In one line:** the same agents you built for the web answer in Slack DMs and WhatsApp messages, and they all land in one inbox.

**Behind the scenes:**
1. **Slack** keeps **one** live socket (Slack's protocol allows only one consumer), so across many server copies a **leader election** picks exactly one copy to hold it. An inbound DM is matched to a user by their linked Slack id, routed to their agent, given a chat keyed by the Slack thread, run through the same core loop, and the reply is posted back on that thread.
2. **WhatsApp:** each user connects their **own** Twilio account. One public webhook receives everything; it figures out *which user* from the Twilio account id in the payload, then routes to that user's agent. It beats Twilio's tight timeout by acknowledging immediately and sending the real answer a moment later.
3. Both channels reuse the exact same run machinery — including pausing for a human: the question arrives as a normal message, and your next message resumes the run.

**Why one bot vs per-user differs:** Slack's single-socket model makes a shared platform bot simplest for v1; WhatsApp's webhook model naturally supports many users at once, each with their own credentials.
**Code map:** `integrations/channels/slack_adapter.py` (+ `leader.py`), `whatsapp_adapter.py`; shared `format_reply`/`wait_for_reply`.

---

### Workflow 11 — You edit something that's already live

**In one line:** changes take effect on the next message, and a run that's already going keeps the settings it started with.

**Behind the scenes:**
1. Because an agent is rebuilt from its config at the **start of every run,** editing the config is visible to the *very next* message — there's no cached copy to clear.
2. A run already in progress is unaffected — it photographed its settings when it started; a run is a consistent unit.
3. Edits to a shared sub-agent or skill ripple to **every** pipeline that references it, on their next run.
4. Deleting something still referenced doesn't crash a run — the builder logs it and continues without that piece (detaching in the UI is the clean path; this is the safety net).

**Why:** "rebuild fresh each run" trades a microscopic bit of speed for zero cache-invalidation bugs and instant propagation.
**Code map:** `build_agent_tree` reads rows per run; missing refs logged as `subagent.missing` / `mcp_server.missing`.

---

### Workflow 12 — Under a million requests (scale, throughput, fairness)

**In one line:** the system stays up under load by buffering work, draining it at a safe rate, and turning away excess politely instead of collapsing.

**Behind the scenes — the control points (what · where · why):**

| Control | What it does | Where / value | Why it exists |
|---|---|---|---|
| **Accept ≠ execute** | posting a message = "write a row + enqueue," answered in ms | `start_run` | a million posts = a million cheap ops, not a million AI calls |
| **Capped worker rate** | each worker runs at most N runs at once | `worker.py`, default **10** | the cap *is* the backpressure; stops you hammering the model into rate-limits |
| **Independent scaling** | API scales on CPU; workers scale on **queue depth** | HPA / KEDA | when you're waiting on the AI, backlog is the only meaningful signal |
| **Per-user fairness** | cap on simultaneous runs per user | free=**1**, paid=**10** → 429 | one person can't hog the queue |
| **Per-user cost cap** | daily token budget | free=**50k/day** → 429 | bounds spend/abuse with no billing system |
| **Overload valve** | shed load when backlog blows past a limit | `max_queue_depth` → 503 | fast "try later" beats latency creeping up forever |
| **Runaway guard** | per-run wall-clock timeout | **300s** | a stuck tool can't pin a worker |
| **Crash safety** | at-least-once delivery + idempotent execution + startup sweep | arq + reconciler | a worker dying mid-run re-runs safely; no double-charge, no run stuck forever |

**The honest caveats (read the code, not the marketing):** the per-user caps are "check then act," so two *simultaneous* requests can both slip past at the very edge — they're guardrails, not hard ceilings (the code says exactly this, with the stricter atomic-Redis upgrade noted). The circuit breaker (Workflow 13) is **per-worker,** not global.
**Code map:** `run_service.py` (`start_run`, `_enforce_concurrency`, `_check_load_shed`), `plans.py`, `quota.py`, `worker.py`, Helm HPA/KEDA.

---

### Workflow 13 — When things break (and why one bad provider doesn't take you down)

**In one line:** failures are expected and handled — retried when it makes sense, given up on fast when it doesn't, and always turned into a clear message instead of a hang.

**Behind the scenes:**
1. Every failure is sorted into a **known category** with a stable code, a human message, and a "should we retry?" flag.
2. **Transient** hiccups (rate-limits, 5xx, network blips) are retried — first by the provider's own SDK (respecting its "retry after"), then by an outer retry with **randomized backoff** so many workers don't all retry in lockstep and spike the provider again.
3. If a provider is **hard-down,** a **circuit breaker** trips after several failures and **fails fast** for a cooldown — no point piling requests on something clearly broken.
4. **Your fault vs their fault:** a bad API key or bad input fails immediately (never retried, never trips the breaker); only real infrastructure failures do.
5. If the agent burns its whole step budget, it returns a **clean apology,** not a half-finished mess — and the run is marked failed so accounting stays honest.
6. A per-run timeout and a startup orphan-sweep are the final backstops so no run is ever stuck forever.

**Why:** at scale something is always failing somewhere; the goal is graceful degradation and honest status, never a silent hang or a cascading pile-up.
**Code map:** `errors.py` (the taxonomy), `llm.py` (`invoke_with_retry`, `invoke_with_breaker`), the exception branches in `_drive`, `reconcile_orphaned_runs`.

---

### Workflow 14 — Keeping tenants' secrets and data safe

**In one line:** your API keys and tokens are encrypted before they're stored, and nobody can read or run anyone else's things.

**Behind the scenes:**
1. Sensitive fields — your LLM key, per-tool keys, Slack/WhatsApp tokens — are **encrypted as they're written** to the database and decrypted only when needed. A stolen database dump is ciphertext, not credentials.
2. Keys can be **rotated** (add a new one; old data still decrypts) without downtime.
3. Every read is scoped to you; cross-user access returns "not found."
4. In production the app **refuses to start** with a default signing secret or missing encryption keys — an insecure deploy is impossible by construction.

**Why:** BYOK means the platform is holding users' real provider keys; encrypting at rest + hard prod checks is the minimum responsible bar.
**Code map:** `crypto.py` (encrypted column types + key rotation), prod fail-fast in `main.py` lifespan, 404-not-403 throughout the repos.

---

## Production & scale

The system runs in two modes, set by `RUN_EXECUTOR`:

- **`inline`** (default) — runs execute as `asyncio` tasks in the API process. Zero infra; for dev / single-box.
- **`queue`** — `POST /chats/{id}/messages` only writes a row + enqueues to **arq** (a Redis task queue), returning `202` in milliseconds. A separate pool of **worker** processes pulls and runs them. This is the production mode.

### The decoupling (why a burst doesn't melt anything)

Accept-work is split from do-work. The API never blocks on an LLM call, so 10k enqueues are cheap Redis ops. The queue is the shock absorber — bursts buffer in Redis, not in process memory. Workers pull at *their* rate; `WORKER_MAX_JOBS` caps concurrent LLM calls per worker so you never push the model past its ceiling (no self-inflicted 429 storm). Past capacity the system **sheds load** (`MAX_QUEUE_DEPTH` → `503`) and enforces **per-plan concurrency caps** (`429`) for fairness, instead of growing latency unbounded.

### Why arq, not Celery + RabbitMQ

You'll see agent stacks reach for Celery+RabbitMQ. RabbitMQ is an **event-streaming/broker** built for complex routing, fan-out to many consumers, and million-msg pipelines; Celery is sync-first and forks worker processes. We need plain **durable request→result task dispatch** on an event loop. `arq` does exactly that — async-native, at-least-once delivery, retries, timeouts, dedup — on the **Redis we already run** for checkpoints. No second broker to operate. (If you later need topic fan-out or multi-language consumers, the queue is the seam to swap.)

### Background processes

- **arq workers** — the run executor (queue mode). Scale independently of the API.
- **Slack Socket Mode** — a single-consumer protocol, so it runs on **exactly one replica** via a Redis leader lock (`app/leader.py`); a poller fails over if the leader dies.
- **Startup reconciler** — marks runs left non-terminal by a crash as `failed(INTERRUPTED)` so no waiter hangs forever.
- **Graceful drain** — lifespan shutdown drains in-flight work and closes pools for clean rolling deploys.

### Horizontal vs vertical scaling

Horizontal is primary: API and workers are **stateless** and share Redis + Postgres, so you add replicas freely (Phase-2 work made SSE, Slack, dedup, and migrations cross-process-safe).

| Tier | Scales on | Mechanism |
|---|---|---|
| **API** | CPU / RPS | Kubernetes **HPA** (request-bound: enqueue + SSE) |
| **Workers** | **queue depth** | **KEDA** ScaledObject → `/metrics/queue-depth` (ZCARD of the arq queue). CPU is meaningless when you're I/O-bound on the LLM. Cap `maxReplicas` at your LLM's concurrent ceiling. |

Vertical = bigger pods or a higher `WORKER_MAX_JOBS`; prefer more pods for resilience. DB connections are bounded by env-tuned pool settings (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`/…), with a `DB_USE_NULL_POOL` switch to put **PgBouncer** in front when replica count is high.

### Robustness (what happens when something breaks)

Every failure maps to a stable `error_code` + user message + retry policy (`app/errors.py`). LLM calls get **provider-SDK retries** (429/5xx, respects `Retry-After`) + a **tenacity backoff with jitter** + a **circuit breaker** (fails fast as `PROVIDER_UNAVAILABLE` when a provider is hard-down). Runs are **idempotent** (safe under at-least-once redelivery), have a **per-run wall-clock timeout**, and are backstopped by the reconciler. See the full failure taxonomy in `app/errors.py`.

### Security

JWT auth (fastapi-users), per-user/IP rate limiting (Redis-backed in prod), per-plan concurrency caps, **per-user daily token quotas** (Redis counter, free=50k/day → `429 QUOTA_EXCEEDED`) with per-model **cost metering** (`total_cost` from a static price table at finalize), attachment size limits (413 before base64 decode), 404-not-403 on cross-user reads, and Twilio signature validation.

**Secrets at rest are encrypted (Fernet).** BYOK LLM keys (in `AgentDB.config`), per-tool keys (Tavily, in `UserToolConfigDB.config`), and Slack/Twilio tokens are transparently encrypted via SQLAlchemy `TypeDecorator`s (`app/crypto.py`) — a DB dump or read-replica leak exposes ciphertext, not tenant credentials. Keys come from `SECRET_ENCRYPTION_KEYS` (comma-separated, newest-first → `MultiFernet` rotation); decryption is plaintext-tolerant so enabling it on an existing DB is safe. Swapping Fernet for a cloud KMS touches only that one file.

**Prod fail-fast:** the app refuses to boot in `prod` with the default `JWT_SECRET` or with `SECRET_ENCRYPTION_KEYS` unset — accidental insecure deploys are impossible.

### CI/CD (`.github/workflows/ci.yml`)

Fail-fast gates on every PR/push, then build → scan → push:

- **static** — `uv lock --locked` (lockfile drift) + `ruff` lint
- **security** — **gitleaks** (secret scan, BYOK-critical) + **pip-audit** (dependency CVEs) + **bandit** (Python SAST)
- **test** — full suite against **real Redis + Postgres** service containers (the queue/leader/pub-sub tests run for real)
- **helm** — chart lint
- **build** — Docker build + **Trivy** image scan (fail on HIGH/CRITICAL), push to GHCR on `main`

`.pre-commit-config.yaml` mirrors the static + secret gates locally so you fail in seconds, not after a CI round-trip (`pre-commit install`).

### Deploy to Kubernetes (Helm)

`deploy/helm/orchestrator/` ships api (Deployment + Service + HPA + optional Ingress), worker (Deployment + KEDA ScaledObject), a single-runner **migration Job** (pre-install/upgrade hook — kills the multi-worker `create_all` race), and ConfigMap/Secret. Liveness `/health`, readiness `/health/ready`.

```bash
helm install mao deploy/helm/orchestrator \
  --set image.repository=ghcr.io/<owner>/<repo>/backend \
  --set secrets.data.DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/db' \
  --set secrets.data.REDIS_URL='redis://host:6379/0' \
  --set secrets.data.JWT_SECRET="$(openssl rand -hex 32)" \
  --set secrets.data.SECRET_ENCRYPTION_KEYS="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

`JWT_SECRET` and `SECRET_ENCRYPTION_KEYS` are **required** (the chart defaults `APP_ENV=prod`, and the app refuses to boot in prod without them). KEDA must be installed for worker autoscaling (`--set worker.keda.enabled=false` to fall back to fixed replicas).

### Test it locally on Kubernetes (kind)

A full multi-pod run — API + worker + migrate Job + in-cluster Postgres/Redis — on your laptop. Validated end-to-end (migrate Job → pods Ready → `/health/ready` green → register `201`).

```bash
# 1. Cluster + build/load the single image (no registry needed)
kind create cluster --name mao
docker build -t mao/backend:dev .
kind load docker-image mao/backend:dev --name mao

# 2. Throwaway in-cluster Postgres + Redis (redis-stack = RediSearch for the checkpointer)
kubectl create deployment pg --image=postgres:16-alpine
kubectl set env deployment/pg POSTGRES_USER=agent POSTGRES_PASSWORD=agent POSTGRES_DB=agent
kubectl expose deployment pg --port=5432
kubectl create deployment redis --image=redis/redis-stack-server:latest
kubectl expose deployment redis --port=6379

# 3. Install the chart (KEDA off for a smoke test; fixed worker replicas)
helm install mao deploy/helm/orchestrator \
  --set image.repository=mao/backend --set image.tag=dev \
  --set worker.keda.enabled=false \
  --set secrets.data.DATABASE_URL='postgresql+asyncpg://agent:agent@pg:5432/agent' \
  --set secrets.data.REDIS_URL='redis://redis:6379/0' \
  --set secrets.data.JWT_SECRET="$(openssl rand -hex 32)" \
  --set secrets.data.SECRET_ENCRYPTION_KEYS="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# 4. Watch the migrate Job run once, then API + worker pods come up
kubectl get pods -w

# 5. Smoke-test from your machine
kubectl port-forward svc/mao-orchestrator-api 8000:80 &
curl localhost:8000/health/ready                      # {"status":"ok","checks":{"redis":"ok","db":"ok"}}
curl localhost:8000/metrics | grep runs_total          # Prometheus metrics exposed
curl -X POST localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"email":"a@b.c","password":"longenoughpwd123"}' # 201

# 6. Tear down
helm uninstall mao && kind delete cluster --name mao
```

> minikube works the same — swap `kind load docker-image` for `minikube image load mao/backend:dev`.

---

### Prerequisites

| To… | You need |
|---|---|
| Run the full stack (recommended) | **Docker** + Docker Compose v2 (`docker compose`). Nothing else. |
| Hack on the backend directly | **Python 3.11+** and [**uv**](https://docs.astral.sh/uv/) (`pip install uv`) |
| Hack on the frontend directly | **Node 20+** |
| Run on local Kubernetes | **kind** (or minikube), **kubectl**, **helm** |

No LLM key is needed to boot — credentials are entered per-user in the browser (BYOK). `cp .env.example .env` and you're ready.

### One command (full stack)

```bash
make up        # = docker compose up -d --build   (postgres + redis + api + worker + mcp + frontend)
```

Brings up everything wired together — API in **queue mode** with a real arq worker, Postgres, Redis, the sample MCP server, and the web UI. Then:

- **Web UI:** `http://localhost`
- **API + OpenAPI docs:** `http://localhost:8000/docs`
- **Tear down:** `make down` (keep data) / `make clean` (drop volumes)
- **Logs:** `make logs`

Containers: `ca-postgres`, `ca-redis`, `ca-backend`, `ca-worker`, `ca-mcp-sample`, `ca-frontend`. Edit `.env` first for optional Slack/Twilio/Langfuse keys; LLM creds are per-user in the UI.

### Backend only (SQLite, no Docker)

```bash
cd backend
cp .env.example .env
make demo    # uv sync + uvicorn :8000 (SQLite, inline run executor)
make test    # 152 tests (Redis-gated ones skip if no Redis)
```

Postgres without Docker: set `DATABASE_URL=postgresql+asyncpg://…` in `.env`.

### Slack

Either set `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` in `.env` (auto-starts in lifespan), or paste them in the UI at `/integrations`. The first user to connect owns the platform bot; subsequent connects atomically clear the previous owner's tokens.

To link your Slack identity: `PATCH /users/me {"slack_user_id":"U..."}`.

### WhatsApp (Twilio)

Per-user, multi-user concurrent — each platform user connects their own Twilio account. External contacts message the bot owner's pipeline.

1. Create a Twilio account and enable the WhatsApp Sandbox (or a Business number).
2. In `/integrations`, enter your Account SID, Auth Token, and From Number (`whatsapp:+14155238886`).
3. Set the **Webhook Base URL** to your public URL (e.g. `https://your-app.example.com`).
4. Copy the computed webhook URL and paste it into Twilio Console → WhatsApp Sandbox → "When a message comes in".
5. (Optional) Enter the sandbox join code — the UI generates a `wa.me` deep link to share with testers.

Or bootstrap from env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `BASE_URL`.

### Deploy (single-image, any Docker host)

The root `Dockerfile` builds the frontend to `static/` and bakes it into the backend image — one container serves both. Works on any host that builds Dockerfiles. Binds to `${PORT:-8000}`.

```bash
docker build -t agent-orchestrator .
docker run -p 8000:8000 -e JWT_SECRET=$(openssl rand -hex 32) agent-orchestrator
```

**That's it.** LLM credentials (provider / base_url / api_key / model) are entered by each user in the browser when they create a pipeline (BYOK, kept in their localStorage + saved into their `AgentConfig.llm`). The server never holds them.

**Operator-set env:**
- `JWT_SECRET` — **required.** Stable secret used to sign user JWTs. Generate once with `openssl rand -hex 32` and reuse across redeploys (rotating it logs everyone out). Leaving it unset uses an insecure default and forfeits auth integrity.

**Operator-optional env:**
- `DATABASE_URL` — defaults to SQLite at `./dev.db`. For persistence across redeploys, swap to managed Postgres: `postgresql+asyncpg://user:pass@host:5432/db`.
- `REDIS_URL` — defaults to empty. Without Redis, runs still execute but lose within-run LangGraph checkpoints (fine for stateless chat; needed for future HITL).
- `TAVILY_API_KEY` — platform-wide `web_search` credential. Users can also bring their own via `/tool-configs`.
- `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` — auto-start Slack at boot. If unset, the first user to connect via `/integrations` owns the platform bot.
- `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `TWILIO_WHATSAPP_FROM` — optional bootstrap for WhatsApp. Per-user creds entered via `/integrations` take priority.
- `BASE_URL` — public URL of this server (e.g. `https://your-app.example.com`). Used to compute webhook URLs. Per-user `webhook_base_url` overrides this.

**Caveats:**
- SQLite + ephemeral container disk = data lost on every redeploy. Attach a volume at `/app/` (or `/app/dev.db`) or use managed Postgres.
- Slack uses outbound WebSocket (Socket Mode) — needs a host that keeps long-running processes alive (no idle-sleep tiers).
- WhatsApp uses inbound webhooks — needs a publicly reachable URL (ngrok for dev, any host for prod). Twilio sandbox sessions expire after 3 days.

---

## API

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | /auth/register | — | Create user |
| POST | /auth/jwt/login | — | Returns JWT |
| GET/PATCH | /users/me | JWT | Profile + `slack_user_id` |
| GET/POST/PUT/DELETE | /agents | JWT | Full `AgentConfig` CRUD; POST/PUT validate sub-agent tree |
| POST | /agents/{id}/deploy | JWT | Validate config + mark Deployed |
| GET | /providers | JWT | LLM provider catalogue (id + label) |
| GET/POST/PUT/DELETE | /personas, /skills | JWT | Owned + read-only globals |
| GET/POST/DELETE | /mcp-servers | JWT | + `GET /mcp-servers/{id}/tools` for live discovery |
| GET/PUT/DELETE | /tool-configs | JWT | + `POST /tool-configs/{tool}/validate` |
| GET | /tools | — | Built-in tool catalogue |
| GET/POST/PATCH/DELETE | /chats | JWT | PATCH reassigns to a Deployed pipeline only |
| POST | /chats/{id}/messages | JWT | Schedule a run; accepts file attachments |
| GET | /chats/{id}/messages | JWT | History |
| GET | /runs/{id} | JWT | Status + tokens + cost + tool_calls |
| GET | /runs/{id}/events | JWT or `?token=` | SSE (backlog + live) |
| POST | /runs/{id}/resume | JWT | Resume an `awaiting_human` run with structured decisions (409 if not paused) |
| POST | /runs/{id}/feedback | JWT | Thumbs up/down (+ comment); mirrors to Langfuse score |
| GET | /stats | JWT | Per-user usage: runs, reviews, thumbs, top tools |
| GET | /metrics/queue-depth | — | arq backlog (ZCARD) for KEDA autoscaling |
| GET | /health/ready | — | Readiness: Redis ping + DB SELECT 1 (503 if degraded) |
| GET | /slack/status | JWT | Per-user `{connected, active_agent_id}` |
| POST | /slack/connect, /slack/active, /slack/disconnect | JWT | Single-owner platform bot |
| GET | /whatsapp/status | JWT | `{connected, active_agent_id, webhook_url, from_number}` |
| POST | /whatsapp/connect, /whatsapp/active, /whatsapp/disconnect | JWT | Per-user Twilio creds (multi-user) |
| POST | /whatsapp/webhook | — | Twilio inbound (public, signature-validated) |
| GET | /health | — | `{"status": "ok"}` |

---

## Notable design decisions

- **Pipeline = root agent.** Computed (`pipeline = agent NOT referenced as subagent`), no `is_pipeline` flag. Stable under in-place edits.
- **Supervisor tree, not DAG.** The agent IS the workflow. Sub-agents are LangChain tools, depth-4 capped, cycles rejected at save.
- **Multi-provider, one config.** `LLMConfig.provider` → dispatch in `build_chat_model`. UI shows uniform base-url / api-key / model fields; provider quirks handled in backend.
- **No chat memory in the checkpointer.** History rebuilt from `MessageDB` each turn (+ rolling summary on `ChatDB`). Redis is reserved for within-run state.
- **No Alembic in v1.** `create_all()` + Inspector-driven additive migrations (no startup ERROR logs).
- **Draft → Deployed is explicit.** No auto re-Draft on edit; deploy validates and sets `deployed_at` once.
- **Single-owner platform Slack bot.** `POST /slack/connect` clears any prior user's tokens before saving yours. Per-user `/slack/status` so new users see "Connect", not "Connected".
- **Per-user WhatsApp (multi-user concurrent).** Each user connects their own Twilio account; the single `/whatsapp/webhook` routes by `AccountSid` in the POST body. External contacts → pipeline owner's bot. Signature validation uses the user's `webhook_base_url` (not `request.url`) for proxy compatibility. Background `asyncio.create_task` returns empty TwiML within Twilio's 15s timeout; reply sent async via REST.
- **404 not 403 on cross-user reads.** No existence leak.
- **SSE dual auth.** EventSource can't send headers → `?token=` accepted alongside `Authorization: Bearer`.
- **Tool credentials never echo back.** `validate` returns generic errors, logs only exception class.
- **Reusable everything has symmetric UI.** Sub-agents and MCP servers both expose `Attach` / `Detach` / `× Delete` with consistent colors (emerald / amber-blue / red).

---

## Roadmap

Ordered by likely sequence.

- [ ] **Subscription tier with server-side LLM defaults.** Free = BYOK (current). Paid = backend-managed key pool, no user setup. The free/paid flag already exists on `UserDB.plan`.
- [ ] **Swarm-style collaboration.** Peer-to-peer agent handoff (not just supervisor → sub). Complements the current hierarchy.
- [ ] **Scheduled runs.** Cron-style triggers — fire a pipeline on a schedule, post output to Slack / web / webhook.
- [x] **WhatsApp integration.** Per-user Twilio WhatsApp — webhook-based, multi-user concurrent, external contacts route to pipeline owner's bot.
- [ ] **Open-source pipeline catalogue.** Browse + import community pipelines (similar to GPT Store / Replit templates, but pipelines and skills).
- [ ] **Planner-decider node.** A router in front of the supervisor that classifies queries before dispatching.
- [ ] **Streaming tokens.** SSE already streams events; pipe per-token deltas to the UI for faster perceived latency.
- [x] **Langfuse tracing + Prometheus metrics.** Drop-in Langfuse callback (per-run traces) + `/metrics` for Grafana. See **Observability & metrics** below.
- [x] **Human-in-the-loop.** LangGraph interrupt → `awaiting_human` run state → resume on the same Redis checkpoint (`thread_id = run_id`). `ask_human` (agent-initiated) + `hil_tools` (forced sign-off). Web resumes with structured decisions; Slack/WhatsApp resume from the next message. See **Workflow 8** above.
- [x] **Forced agent chains.** Deterministic edges the LLM can't skip (`require_before_finish`, `force_after`) via `ForcedChainMiddleware`. See **Workflow 9** above.
- [ ] **Per-user Slack BYOK.** Drop the single-owner platform bot model; each user gets their own Socket Mode connection.

---

## Tests

```bash
cd backend && make test   # 152 tests (Redis-gated ones skip if no Redis; 1 live-LLM test skips without creds)
```

Unit + integration: auth, agent CRUD with sub-agent tree validation (cycle / depth / cross-user / tool-name collision), Draft-rejection gating, persona/skill globals + ownership, tool-config validation flow, MCP discovery, chat + run lifecycle, Slack inbound dispatch, WhatsApp webhook dispatch (routing, chat reuse, dedup), memory rolling summary, multimodal file handling, sub-agent recursive build, **live LLM end-to-end** (skipped without creds).

Production-hardening coverage: failure taxonomy, run idempotency + crash reconciler, per-run timeout, **arq queue end-to-end** (real worker drains a real Redis queue), cross-process SSE pub/sub, Redis leader lock, LLM retry + circuit breaker, DB pool config, load-shed + concurrency caps + **daily token quota** (413/429/503 at the HTTP boundary), **secrets-at-rest encryption** (round-trip + ciphertext-at-rest DB proof), **cost accounting** (per-model price table), **Prometheus metrics** (counter funnel + `/metrics` render), prod fail-fast. Redis-gated tests run against a real Redis (skipped if none reachable). The Helm chart is validated end-to-end on a local `kind` cluster.

Frontend: `npx tsc --noEmit` clean. Backend: `ruff check` + `bandit` + `pip-audit` + `gitleaks` clean.

---

## Observability & metrics

**Three planes, by design** (off-the-shelf for tracing + metrics, our DB for the product):

- **Langfuse (optional, off-the-shelf LLM tracing).** Set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`) and every run is traced — tool calls (**including MCP tools**, since they're standard LangChain tools), token usage, latency, and nested sub-agent spans — via the drop-in `CallbackHandler`. No tracing is hand-rolled. Unset = disabled (no-op), so dev/tests need nothing. Each run uses a deterministic Langfuse trace id derived from `run_id` so feedback attaches to the right trace.

- **Prometheus + Grafana (app/infra metrics, open-source — no New Relic, no vendor lock-in).** `GET /metrics` is exposed by [`prometheus-fastapi-instrumentator`](https://github.com/trallnag/prometheus-fastapi-instrumentator) — one line in `create_app` gives HTTP RED metrics (request rate / error rate / latency-histogram → p95) for free. On top, `app/metrics.py` adds the domain signals: `runs_total{status,error_code}` (incremented at the single `finalize_run` funnel) and a `queue_depth` gauge (mirrored from the KEDA endpoint). The arq worker — a non-HTTP process — exposes its own `/metrics` via `start_http_server` so worker-side counters are scrapable too. The Helm chart annotates both pods (`prometheus.io/scrape`) for zero-CRD discovery. Extend with another `Counter`/`Gauge`/`Histogram` — that's the whole pattern.

- **In-app metrics (our DB, the product/billing data plane).**
  - **Feedback:** `POST /runs/{id}/feedback` `{rating: up|down, comment?}` — thumbs up/down, one per (user, run). Stored in `FeedbackDB` (source of truth) and **mirrored to a Langfuse BOOLEAN score** (`user-thumbs`) when Langfuse is on.
  - **Usage:** a single `UsageCounter` callback (one integration point, not per-tool decorators) counts every tool/sub-agent/MCP call per run into `RunDB.tool_calls`.
  - **Stats:** `GET /stats` → `{questions_asked, reviews_given, thumbs_up, thumbs_down, top_tools}` per user — the foundation for a usage dashboard / billing.

This is a minimal foundation, intended to extend (time ranges, charts, per-tool cost). Alerting/SLOs ride on the Prometheus metrics via Grafana (no extra code).
