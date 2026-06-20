"""Background run execution.

Flow per turn:
  POST /chats/{id}/messages → create Run row → spawn asyncio.create_task → return run_id
  The task: emit run.started, build messages from history (last window N), invoke agent,
  persist agent reply, emit run.finished with usage, close emitter, drop from registry.

Cross-turn memory is DB-based (MessageDB + rolling summary on ChatDB).
Within-run graph state uses LangGraph's Redis checkpointer (thread_id = run_id),
enabling mid-graph interrupts and multi-step ReAct replay.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from io import BytesIO
from uuid import UUID

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session_factory
from app.db.models import AgentDB, ChatDB, RunDB, SkillDB, UserDB
from app.db.repos import (
    count_active_runs,
    create_run,
    finalize_run,
    insert_message,
    list_messages,
    list_tool_configs,
    run_has_user_message,
)
from app.domain import AgentConfig, LLMConfig, utcnow
from app.errors import INTERRUPTED, RUN_TIMEOUT, STEP_LIMIT, classify, info_for
from app.llm import build_chat_model, invoke_with_breaker
from app.observability import get_handler, run_span
from app.plans import limits_for
from app.runtime.agent import _SUBAGENT_USAGE, _accumulate_usage, build_agent_tree
from app.runtime.events import EMITTERS, RunEventEmitter
from app.runtime.tools import build_registry
from app.runtime.usage_callback import UsageCounter

log = structlog.get_logger()

# Set once at startup from main.py lifespan. None = Redis unavailable (runs still work,
# but no within-run graph checkpointing for HITL/interrupts).
_CHECKPOINTER = None

# arq pool, set by the API lifespan in "queue" mode. None = inline execution
# (dev/test, or queue mode with Redis unreachable → graceful fallback to inline).
_ARQ_POOL = None


def set_checkpointer(saver) -> None:
    global _CHECKPOINTER
    _CHECKPOINTER = saver


def set_arq_pool(pool) -> None:
    global _ARQ_POOL
    _ARQ_POOL = pool


def _to_lc_messages(rows) -> list:
    """MessageDB rows → LangChain Human/AI messages. System prompt is wired into
    create_agent at build time — DO NOT prepend SystemMessage here (some providers,
    e.g. vLLM/Qwen, reject mid-conversation system roles)."""
    msgs: list = []
    for r in rows:
        if r.sender == "user":
            msgs.append(HumanMessage(content=r.content))
        else:
            msgs.append(AIMessage(content=r.content))
    return msgs


def _effective_prompt(base: str, summary: str) -> str:
    """Stitch rolling summary into the system prompt (single SystemMessage at boundary)."""
    if not summary:
        return base
    return f"{base}\n\nEarlier conversation summary:\n{summary}"


async def _summarize(llm_cfg: LLMConfig, prior: str, batch) -> str:
    """One LLM call: fold `batch` (oldest unsummarized) into `prior` rolling summary."""
    rendered = "\n".join(f"{r.sender}: {r.content}" for r in batch)
    prompt = (
        "Update this rolling conversation summary with the new turns below. "
        "Be terse — keep only facts the agent needs to remain coherent. "
        "Return ONLY the updated summary text, no preamble.\n\n"
        f"Prior summary:\n{prior or '(none)'}\n\n"
        f"New turns:\n{rendered}"
    )
    model = build_chat_model(llm_cfg)
    result = await model.ainvoke([HumanMessage(content=prompt)])
    return (getattr(result, "content", "") or "").strip()


async def _resolve_context(session, chat: ChatDB, cfg: AgentConfig):
    """Return (summary_text, verbatim_rows_to_feed). May update chat.summary in-place."""
    all_msgs = await list_messages(session, chat_id=chat.id)
    if cfg.memory.type == "none":
        return "", all_msgs
    if cfg.memory.type == "buffer":
        return "", all_msgs[-cfg.memory.window:]

    # type == "summary"
    n, m = cfg.memory.window, cfg.memory.summary_threshold
    unsummarized = all_msgs[chat.summary_count:]
    if len(unsummarized) <= n + m:
        return chat.summary, unsummarized

    to_summarize = unsummarized[:m]
    remaining = unsummarized[m:]
    new_summary = await _summarize(cfg.llm, chat.summary, to_summarize)
    chat.summary = new_summary
    chat.summary_count += m
    await session.commit()
    log.info(
        "memory.summarized",
        chat_id=str(chat.id),
        folded=m,
        kept_verbatim=len(remaining),
        summary_chars=len(new_summary),
    )
    return new_summary, remaining


def _extract_usage(result_messages: list) -> dict:
    """Sum usage_metadata across any AIMessages in the result (covers multi-step ReAct)."""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    _accumulate_usage(totals, result_messages)
    return totals


async def _load_chat_and_agent(
    session: AsyncSession, *, chat_id: UUID
) -> tuple[ChatDB, AgentConfig, str]:
    """Resolve chat → agent → effective system prompt (persona override or agent's)."""
    chat = await session.get(ChatDB, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    if chat.agent_id is None:
        raise ValueError(f"chat {chat_id} has no agent assigned — reassign via PATCH /chats/{{id}}")
    agent_row = await session.get(AgentDB, chat.agent_id)
    if agent_row is None:
        raise ValueError(f"agent not found: {chat.agent_id}")
    cfg = AgentConfig.model_validate(agent_row.config)

    # Persona belongs to the agent (its system_prompt was already set from the
    # picked persona at form-save time). No chat-level persona override.
    effective_prompt = cfg.system_prompt

    # Inject skill documents into the prompt
    for skill_id in cfg.skills:
        skill = await session.get(SkillDB, skill_id)
        if skill is not None:
            effective_prompt += f"\n\n---\nSkill: {skill.name}\n{skill.content}\n---"

    return chat, cfg, effective_prompt


def _process_files(files: list[dict]) -> tuple[str, list[dict]]:
    """Process file attachments. Returns (text_to_prepend, image_content_blocks).
    PDF → extracted text prepended. Image → content block for multimodal LLM."""
    text_parts: list[str] = []
    image_blocks: list[dict] = []

    for f in files:
        mime = f.get("mime_type", "")
        raw = base64.b64decode(f["content_base64"])

        if mime == "application/pdf":
            reader = PdfReader(BytesIO(raw))
            pages = "\n\n".join((p.extract_text() or "") for p in reader.pages)
            text_parts.append(f"[Attached PDF: {f['name']}]\n{pages}")

        elif mime.startswith("image/"):
            b64 = f["content_base64"]
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })

        else:
            log.warning("file.unsupported", name=f.get("name"), mime=mime)

    return "\n\n".join(text_parts), image_blocks


async def _finalize_failure(session_factory, emitter, run_id: UUID, info) -> None:
    """Persist a terminal failure and emit run.finished. Shared by the timeout and
    generic-exception boundaries so the failure contract can't drift between them."""
    async with session_factory() as session:
        await finalize_run(
            session, run_id=run_id, status="failed",
            error=info.user_message, error_code=info.code,
        )
    await emitter.emit(
        "run.finished",
        {"status": "failed", "error_code": info.code, "error": info.user_message},
    )


async def _execute(run_id: UUID, chat_id: UUID, user_text: str, files: list[dict] | None = None) -> None:
    """The actual run. Runs inline (asyncio.create_task) or in an arq worker.

    Idempotent: the queue path is at-least-once, so a worker crash can redeliver
    a run. We no-op if it already finished, and guard the user-message insert so a
    retry never double-writes or double-counts tokens."""
    # Bind run_id so every log line for this run (inline or worker) carries it.
    structlog.contextvars.bind_contextvars(run_id=str(run_id))
    log.info("run.start", run_id=str(run_id), chat_id=str(chat_id))
    session_factory = get_session_factory()

    # Idempotency gate: if a prior attempt already reached a terminal state, this is
    # a duplicate delivery — do nothing (don't re-emit, re-bill, or re-reply).
    # Otherwise mark running (queued → running) so the lifecycle is observable.
    async with session_factory() as _s:
        _existing = await _s.get(RunDB, run_id)
        if _existing is not None and _existing.status in ("succeeded", "failed"):
            log.info("run.skip_duplicate", run_id=str(run_id), status=_existing.status)
            return
        if _existing is not None and _existing.status != "running":
            _existing.status = "running"
            await _s.commit()

    emitter = RunEventEmitter(run_id, session_factory)
    EMITTERS[run_id] = emitter

    try:
        await emitter.emit("run.started", {"chat_id": str(chat_id), "input": user_text})

        # Phase 1: all pre-LLM DB work in one short-lived session, then close.
        # Holding a session across the LLM round-trip would pin a connection from the
        # pool for the duration of the call (60s+ with retries).
        async with session_factory() as session:
            chat, cfg, system_prompt = await _load_chat_and_agent(session, chat_id=chat_id)
            # sender_id captured here so we don't touch chat.agent_id after session close
            # (scalar columns survive close because expire_on_commit=False, but explicit is safer)
            sender_id = str(chat.agent_id)
            user_id = str(chat.user_id)  # for Langfuse trace attribution

            file_text, image_blocks = _process_files(files or [])
            full_user_text = f"{file_text}\n\n{user_text}".strip() if file_text else user_text

            # Guarded against duplicate delivery: only insert the user turn once.
            if not await run_has_user_message(session, run_id=run_id):
                await insert_message(
                    session, chat_id=chat_id, run_id=run_id, sender="user", content=full_user_text
                )
            summary, verbatim = await _resolve_context(session, chat, cfg)

            user_configs = await list_tool_configs(session, user_id=chat.user_id)
            tc = {r.tool_name: r.config for r in user_configs}

        # Phase 2: build LangChain messages + agent tree + LLM call — no session held.
        lc_messages = _to_lc_messages(verbatim)
        # For multimodal: replace the last HumanMessage with image content blocks
        if image_blocks and lc_messages:
            last_msg = lc_messages[-1]
            if hasattr(last_msg, "content") and isinstance(last_msg.content, str):
                lc_messages[-1] = HumanMessage(content=[
                    {"type": "text", "text": last_msg.content},
                    *image_blocks,
                ])
        user_registry = build_registry(tool_configs=tc) if tc else None
        run_cfg = cfg.model_copy(update={"system_prompt": _effective_prompt(system_prompt, summary)})
        agent = await build_agent_tree(
            run_cfg, session_factory=session_factory, checkpointer=_CHECKPOINTER,
            tool_registry=user_registry,
        )
        # thread_id = run_id (not chat_id) — each run gets its own checkpoint so
        # within-run graph state doesn't conflict with our DB-based cross-turn history.
        # Sub-agent token usage is collected via _SUBAGENT_USAGE contextvar (root's
        # result["messages"] only sees ToolMessages for sub-agent calls, no usage).
        sub_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        usage_token = _SUBAGENT_USAGE.set(sub_usage)
        usage_counter = UsageCounter()  # standardized tool/sub-agent call capture (incl MCP)
        lf_handler = get_handler()      # off-the-shelf Langfuse tracing (None if disabled)
        callbacks = [usage_counter] + ([lf_handler] if lf_handler else [])
        try:
            # Per-run wall-clock cap OUTSIDE the retry, so a hung multi-step loop or
            # stuck tool can't pin a worker (queue) or leak forever (inline). arq's
            # job_timeout only covers queue mode; this covers both. run_span pins the
            # Langfuse trace id to the run (nullcontext when Langfuse is off).
            async with asyncio.timeout(get_settings().run_timeout_s):
                with run_span(run_id):
                    result = await invoke_with_breaker(
                        agent,
                        {"messages": lc_messages},
                        config={
                            "recursion_limit": max(2, cfg.limits.max_steps),
                            "configurable": {"thread_id": str(run_id)},
                            "callbacks": callbacks,  # one slot: usage counter + Langfuse
                            "metadata": {
                                "langfuse_user_id": user_id,
                                "langfuse_session_id": str(chat_id),
                                "langfuse_tags": [cfg.llm.provider, get_settings().app_env],
                            },
                        },
                        # Breaker keyed by root provider endpoint (sub-agents may differ;
                        # the common case is one provider per run).
                        breaker_key=f"{cfg.llm.provider}:{cfg.llm.base_url}",
                    )
        finally:
            _SUBAGENT_USAGE.reset(usage_token)

        final = result["messages"][-1]
        reply = getattr(final, "content", "") or ""
        usage = _extract_usage(result["messages"])
        for k in usage:
            usage[k] += sub_usage.get(k, 0)

        # Phase 3: post-LLM persistence — fresh session, sender_id captured in Phase 1.
        async with session_factory() as session:
            await insert_message(
                session,
                chat_id=chat_id,
                run_id=run_id,
                sender=sender_id,
                recipient="user",
                content=reply,
            )
            await finalize_run(
                session, run_id=run_id, status="succeeded", total_tokens=usage,
                tool_calls=usage_counter.tool_calls,
            )

        await emitter.emit("agent.message", {"sender": sender_id, "content": reply})
        await emitter.emit("run.finished", {"usage": usage, "status": "succeeded"})
        log.info("run.succeeded", run_id=str(run_id), tokens=usage.get("total_tokens", 0))
    except GraphRecursionError:
        # Bounded step budget reached. Don't leave the chat silent — persist a clean
        # reply so the user sees something actionable. Run is still marked failed
        # so usage/billing accounting stays honest.
        limit = cfg.limits.max_steps  # captured during Phase 1
        log.warning("run.recursion_limit", run_id=str(run_id), limit=limit)
        reply = (
            f"I couldn't finish this within {limit} reasoning steps. "
            f"Could you simplify the request or split it into smaller parts?"
        )
        async with session_factory() as session:
            await insert_message(
                session, chat_id=chat_id, run_id=run_id,
                sender=sender_id, recipient="user", content=reply,
            )
            await finalize_run(
                session, run_id=run_id, status="failed",
                error=info_for(STEP_LIMIT).user_message, error_code=STEP_LIMIT,
            )
        await emitter.emit("agent.message", {"sender": sender_id, "content": reply})
        await emitter.emit("run.finished", {"status": "failed", "error_code": STEP_LIMIT})
    except TimeoutError:
        # Our per-run wall-clock cap (asyncio.timeout). A builtin TimeoutError at this
        # boundary is the run budget — provider timeouts surface as SDK-specific types.
        log.warning("run.timeout", run_id=str(run_id), limit=get_settings().run_timeout_s)
        await _finalize_failure(session_factory, emitter, run_id, info_for(RUN_TIMEOUT))
    except asyncio.CancelledError:
        # Cancellation is control flow (shutdown). Re-raise so arq can re-queue
        # (pessimistic execution); the startup reconciler is the final backstop.
        raise
    except Exception as exc:  # noqa: BLE001 — top-level boundary; we log + persist
        # Centralized failure taxonomy: classify() picks the user message + machine code.
        # Raw exception goes to logs only (may carry detail we don't surface to the user).
        info = classify(exc)
        log.exception("run.failed", run_id=str(run_id), error=str(exc), error_code=info.code)
        await _finalize_failure(session_factory, emitter, run_id, info)
    finally:
        await emitter.close()
        EMITTERS.pop(run_id, None)


# Strong refs to in-flight runs so the event loop doesn't GC them mid-execution
# and lifespan shutdown can drain them cleanly (important for test isolation).
_PENDING: set[asyncio.Task] = set()


class QueueFull(Exception):
    """Backlog exceeds max_queue_depth — caller sheds load (503) rather than
    accepting work that would sit in an unboundedly growing queue."""


class ConcurrencyLimitExceeded(Exception):
    """User already has their plan's max concurrent runs in flight (caller → 429)."""

    def __init__(self, active: int, cap: int) -> None:
        self.active, self.cap = active, cap
        super().__init__(f"{active} active runs >= plan cap {cap}")


async def _enforce_concurrency(session: AsyncSession, user_id: UUID) -> None:
    """Reject if the user is at their plan's concurrent-run cap. Fairness/backpressure
    so one tenant can't flood the queue and starve others.

    ponytail: count-then-create has a tiny race (two exactly-simultaneous requests can
    both pass at cap-1), so the cap is a guardrail, not a hard ceiling. Use an atomic
    Redis INCR with decrement-on-finalize only if strict enforcement is ever required."""
    cap = limits_for((await session.get(UserDB, user_id)).plan).max_concurrent_runs
    if cap <= 0:  # unlimited plan
        return
    active = await count_active_runs(session, user_id=user_id)
    if active >= cap:
        log.info("run.concurrency_rejected", user_id=str(user_id), active=active, cap=cap)
        raise ConcurrencyLimitExceeded(active, cap)


async def _check_load_shed() -> None:
    """Reject if the arq backlog is over the cap. No-op unless we're in queue mode
    with a live pool and a positive cap. Reads ZCARD of the arq queue (atomic)."""
    s = get_settings()
    if s.run_executor != "queue" or _ARQ_POOL is None or s.max_queue_depth <= 0:
        return
    from arq.constants import default_queue_name
    depth = await _ARQ_POOL.zcard(default_queue_name)
    if depth >= s.max_queue_depth:
        log.warning("run.load_shed", depth=depth, max=s.max_queue_depth)
        raise QueueFull(f"backlog {depth} >= cap {s.max_queue_depth}")


async def start_run(
    session: AsyncSession, *, chat_id: UUID, user_text: str, files: list[dict] | None = None
) -> UUID:
    """Create the Run row and dispatch it, returning run_id immediately.

    Dispatch is a config seam: "queue" enqueues to an arq worker (durable, bounded,
    survives restarts); "inline" runs it as an asyncio task in this process. The
    queue path falls back to inline if the pool is unavailable so a Redis blip
    can't drop the turn entirely (logged loudly so it's not silent in prod)."""
    chat = await session.get(ChatDB, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    # Per-user fairness first, then global shed — both before creating the row so we
    # never persist a run we're about to reject.
    await _enforce_concurrency(session, chat.user_id)
    await _check_load_shed()
    run = await create_run(session, chat_id=chat_id, agent_id=chat.agent_id)

    if get_settings().run_executor == "queue" and _ARQ_POOL is not None:
        # Carry the request_id across the queue so worker logs + Langfuse traces tie
        # back to the originating HTTP request. _job_id=run_id dedups duplicate enqueues.
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        await _ARQ_POOL.enqueue_job(
            "execute_run", str(run.id), str(chat_id), user_text, files or [], request_id,
            _job_id=str(run.id),
        )
    else:
        if get_settings().run_executor == "queue":
            log.error("run.queue_unavailable_inline_fallback", run_id=str(run.id))
        task = asyncio.create_task(_execute(run.id, chat_id, user_text, files=files or []))
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    return run.id


async def drain_pending(timeout: float = 60.0) -> None:
    """Await any in-flight runs. Called at lifespan shutdown."""
    if not _PENDING:
        return
    pending = list(_PENDING)
    log.info("run.drain", count=len(pending))
    await asyncio.wait(pending, timeout=timeout)


async def reconcile_orphaned_runs(stale_after_s: float | None = None) -> int:
    """Mark long-stale non-terminal runs as failed(INTERRUPTED). Called at startup.

    A run can be left in 'queued'/'running' forever if the process/worker died
    mid-flight and arq's redelivery + max_tries were also exhausted. This backstop
    guarantees the contract every waiter (wait_for_reply, SSE) depends on: no run
    stays non-terminal indefinitely.

    Multi-worker safe: a genuinely in-flight run is bounded by job_timeout, so we
    only touch runs older than 2× that — those are certainly dead. This means an
    API restart never reaps a run still executing on a live worker."""
    window = stale_after_s if stale_after_s is not None else get_settings().run_timeout_s * 2
    cutoff = utcnow() - timedelta(seconds=window)
    info = info_for(INTERRUPTED)
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(RunDB).where(
                RunDB.status.in_(("queued", "running")),
                RunDB.started_at < cutoff,
            )
        )).scalars().all()
        for run in rows:
            run.status = "failed"
            run.ended_at = utcnow()
            run.error = info.user_message
            run.error_code = INTERRUPTED
        await session.commit()
    if rows:
        log.warning("run.reconciled_orphans", count=len(rows))
    return len(rows)
