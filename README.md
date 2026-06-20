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
      health.py
    runtime/
      agent.py                    build_agent_tree() — recursive ReAct, contextvar token aggregation
      tools.py                    Built-in tool registry (calculator, web_search, html_to_md, pdf_to_text, python_sandbox)
      checkpointer.py             Redis checkpointer (within-run state)
    services/run_service.py       Schedule + execute + persist + emit
    integrations/
      channels/slack_adapter.py   Bolt Socket Mode adapter
      channels/whatsapp_adapter.py Twilio WhatsApp adapter (per-user, stateless REST)
      sample_mcp_server.py        Demo MCP server (timestamp + word_count tools)
    db/
      models.py                   SQLAlchemy 2.0 async ORM
      repos.py                    Plain async helpers (caller owns the session)
      seeds/personas.yaml         Default personas, loaded on startup
    llm.py                        build_chat_model() — provider dispatch (openai/anthropic/google/vllm)
    domain.py                     Pydantic schemas (AgentConfig, LLMConfig, MemoryConfig, RunEvent)
    main.py                       FastAPI app + lifespan (Slack autostart, Redis checkpointer)

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

docker-compose.yml                postgres + redis + backend + frontend + mcp-sample
```

---

## Architecture

### System flow

```mermaid
flowchart LR
  subgraph FE["Frontend (React + ReactFlow + TanStack Query)"]
    PG[/Pipelines list/]
    CV[Canvas editor]
    CH[Chat + SSE]
    IN[Integrations]
    PS[Personas / Skills]
  end

  subgraph BE["Backend (FastAPI + asyncio + LangGraph)"]
    API[REST routers]
    RS[run_service]
    BA[build_agent_tree<br/>recursive ReAct, depth-4]
    LL[ChatOpenAI / Anthropic<br/>Gemini / vLLM dispatch]
    TR[Built-in tools<br/>+ MCP discovery]
    SL[Slack Socket Mode<br/>single-owner bot]
    WA[WhatsApp webhook<br/>per-user Twilio]
    EM[RunEventEmitter<br/>SSE fan-out]
  end

  subgraph DATA["Persistence"]
    DB[("SQLAlchemy 2.0 async<br/>SQLite ↔ Postgres<br/>UserDB · AgentDB · ChatDB<br/>MessageDB · RunDB · RunEventDB<br/>PersonaDB · SkillDB · MCPServerDB<br/>ToolConfigDB")]
    RD[(Redis<br/>within-run checkpointer)]
  end

  FE -->|REST + SSE| API
  API --> RS --> BA --> LL
  BA --> TR
  RS <-->|sessions| DB
  RS --> EM -->|SSE backlog + live| FE
  SL -->|inbound DM ⇒ ChatDB| RS
  RS -->|reply ⇒ same thread| SL
  WA -->|inbound webhook ⇒ ChatDB| RS
  RS -->|async reply via REST| WA
  BA -.checkpoint.-> RD
  IN -->|connect/disconnect/active| API
  PS -->|reusables attach to N pipelines| API
```

### Data model (multi-user → multi-pipeline → multi-chat)

```mermaid
flowchart TD
  U["UserDB<br/>(JWT identity + optional slack_user_id<br/>+ optional Twilio WhatsApp creds)"]
  A1["AgentDB<br/>(root = pipeline)"]
  A2["AgentDB<br/>(sub-agent)"]
  A3["AgentDB<br/>(sub-agent — shared)"]
  CH1["ChatDB<br/>channel='web'"]
  CH2["ChatDB<br/>channel='slack'<br/>external_thread_id=C123:1.234"]
  CH4["ChatDB<br/>channel='whatsapp'<br/>external_thread_id=whatsapp:+1234"]
  CH3["ChatDB<br/>channel='web'"]
  M["MessageDB<br/>(append-only, full audit)"]
  R["RunDB + RunEventDB<br/>(status, tokens, SSE backlog)"]
  P["PersonaDB / SkillDB<br/>(account-scoped, attach to N agents)"]
  MCP["MCPServerDB / ToolConfigDB<br/>(per-user creds, validated pre-save)"]

  U -- owns N --> A1
  U -- owns N --> A2
  U -- owns N --> A3
  A1 -- references --> A2
  A1 -- references --> A3
  A2 -- can also be a pipeline root --> CH3
  A1 -- backs N --> CH1
  A1 -- backs N (Slack threads) --> CH2
  A1 -- backs N (WhatsApp contacts) --> CH4
  CH1 -- has many --> M
  CH2 -- has many --> M
  CH1 -- triggers --> R
  CH2 -- triggers --> R
  A1 -. uses .- P
  A1 -. uses .- MCP
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

JWT auth (fastapi-users), per-user/IP rate limiting (Redis-backed in prod), per-plan concurrency caps, attachment size limits (413 before base64 decode), 404-not-403 on cross-user reads, Twilio signature validation, and a **prod fail-fast** that refuses to boot with the default `JWT_SECRET`. BYOK credentials are never held server-side.

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
  --set secrets.data.JWT_SECRET="$(openssl rand -hex 32)"
```

KEDA must be installed for worker autoscaling (`--set worker.keda.enabled=false` to fall back to fixed replicas). Validated end-to-end on a local `kind` cluster (migrate Job → pods Ready → `/health/ready` green).

---

## Setup

### Full stack (Docker — recommended)

```bash
cp .env.example .env   # set LLM creds (any OpenAI-compatible provider), optional Slack tokens
docker compose up
```

Services: `ca-postgres`, `ca-redis`, `ca-backend`, `ca-frontend`, `ca-mcp-sample`.

- Backend: `http://localhost:8000` (OpenAPI at `/docs`)
- Frontend: `http://localhost`
- Truly-fresh `down && up` reinstalls deps; quick `up` reuses cached volumes.

### Backend only

```bash
cd backend
cp .env.example .env
make demo    # uv sync + uvicorn :8000 (SQLite)
make test    # 73 tests
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
- [ ] **Langfuse tracing.** Drop-in callback handler — already designed (see notes below).
- [ ] **Human-in-the-loop.** LangGraph interrupt → DB-backed approval queue → resume.
- [ ] **Per-user Slack BYOK.** Drop the single-owner platform bot model; each user gets their own Socket Mode connection.

---

## Tests

```bash
cd backend && make test   # 129 tests
```

Unit + integration: auth, agent CRUD with sub-agent tree validation (cycle / depth / cross-user / tool-name collision), Draft-rejection gating, persona/skill globals + ownership, tool-config validation flow, MCP discovery, chat + run lifecycle, Slack inbound dispatch, WhatsApp webhook dispatch (routing, chat reuse, dedup), memory rolling summary, multimodal file handling, sub-agent recursive build, **live LLM end-to-end** (skipped without creds).

Production-hardening coverage: failure taxonomy, run idempotency + crash reconciler, per-run timeout, **arq queue end-to-end** (real worker drains a real Redis queue), cross-process SSE pub/sub, Redis leader lock, LLM retry + circuit breaker, DB pool config, load-shed + concurrency caps (413/429/503 at the HTTP boundary), prod fail-fast. Redis-gated tests run against a real Redis (skipped if none reachable). The Helm chart is validated end-to-end on a local `kind` cluster.

Frontend: `npx tsc --noEmit` clean. Backend: `ruff check` + `bandit` + `pip-audit` + `gitleaks` clean.

---

## Observability & metrics

**Two planes, by design** (off-the-shelf for observability, our DB for the product):

- **Langfuse (optional, off-the-shelf tracing).** Set `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`) and every run is traced — tool calls (**including MCP tools**, since they're standard LangChain tools), token usage, latency, and nested sub-agent spans — via the drop-in `CallbackHandler`. No tracing is hand-rolled. Unset = disabled (no-op), so dev/tests need nothing. Each run uses a deterministic Langfuse trace id derived from `run_id` so feedback attaches to the right trace.

- **In-app metrics (our DB, the product/billing data plane).**
  - **Feedback:** `POST /runs/{id}/feedback` `{rating: up|down, comment?}` — thumbs up/down, one per (user, run). Stored in `FeedbackDB` (source of truth) and **mirrored to a Langfuse BOOLEAN score** (`user-thumbs`) when Langfuse is on.
  - **Usage:** a single `UsageCounter` callback (one integration point, not per-tool decorators) counts every tool/sub-agent/MCP call per run into `RunDB.tool_calls`.
  - **Stats:** `GET /stats` → `{questions_asked, reviews_given, thumbs_up, thumbs_down, top_tools}` per user — the foundation for a usage dashboard / billing.

This is a minimal foundation, intended to extend (time ranges, charts, per-tool cost). New Relic APM (auto-instrumentation, zero hand-written metrics) is the planned ops-metrics layer.
