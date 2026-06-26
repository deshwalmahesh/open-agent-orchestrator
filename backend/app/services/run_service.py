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
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from uuid import UUID

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
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
    get_awaiting_run_for_chat,
    insert_message,
    list_messages,
    list_tool_configs,
    mark_run_resumed,
    pause_run_for_human,
    run_has_user_message,
)
from app.domain import AgentConfig, LLMConfig, utcnow
from app.errors import INTERRUPTED, RUN_TIMEOUT, STEP_LIMIT, classify, info_for
from app.llm import build_chat_model, invoke_with_breaker
from app.observability import get_handler, run_span
from app.plans import limits_for
from app.quota import add_usage, cost_for, enforce_quota
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


def _merge_usage(a: dict, b: dict) -> dict:
    """Sum token usage across legs (pause/resume accumulates into RunDB.partial_tokens)."""
    return {
        k: (a.get(k, 0) or 0) + (b.get(k, 0) or 0)
        for k in ("input_tokens", "output_tokens", "total_tokens")
    }


def _pending_interrupt(snap) -> dict:
    """First interrupt value off a paused graph state snapshot (the HITLRequest dict)."""
    for task in snap.tasks:
        for intr in getattr(task, "interrupts", None) or ():
            return intr.value
    return {}


def _decisions_from_text(text: str, interrupt: dict) -> list[dict]:
    """Map a free-text channel reply to one HITL decision PER pending action.

    The middleware requires exactly one decision per interrupted tool call. ask_human is
    answered on the tool's behalf (`respond`); a forced tool approval reads an affirmative
    keyword as `approve`, anything else as `reject` (carrying the text as the reason)."""
    affirmative = {"yes", "y", "approve", "approved", "ok", "okay", "go ahead", "proceed"}
    decisions: list[dict] = []
    for req in interrupt.get("action_requests", []):
        if req.get("name") == "ask_human":
            decisions.append({"type": "respond", "message": text})
        elif text.strip().lower() in affirmative:
            decisions.append({"type": "approve"})
        else:
            decisions.append({"type": "reject", "message": text})
    return decisions


@dataclass
class _LegCtx:
    """Everything _drive needs to run one agent leg (first turn or a resume) and finalize.
    prior_tokens carries SUB-AGENT usage from earlier legs (root usage is cumulative in
    the checkpointed messages, so only sub-agent tokens need accumulating across pauses)."""

    run_id: UUID
    chat_id: UUID
    sender_id: str
    user_id: str
    cfg: AgentConfig
    emitter: RunEventEmitter
    prior_tokens: dict


async def _compile_agent(cfg: AgentConfig, system_prompt: str, summary: str, tc: dict):
    """Compile the run's agent tree: stitch the rolling summary into the prompt, apply any
    per-user tool creds, and attach the root checkpointer. One funnel for first-run and
    resume so the checkpointer/registry wiring can't drift. Returns (run_cfg, agent)."""
    run_cfg = cfg.model_copy(update={"system_prompt": _effective_prompt(system_prompt, summary)})
    user_registry = build_registry(tool_configs=tc) if tc else None
    agent = await build_agent_tree(
        run_cfg, session_factory=get_session_factory(), checkpointer=_CHECKPOINTER,
        tool_registry=user_registry,
    )
    return run_cfg, agent


async def _drive(agent, invoke_input, ctx: _LegCtx) -> None:
    """Invoke one agent leg, then finalize / pause-for-human / finalize-failed.

    Shared by first-run (_execute) and resume_run so the timeout, breaker, usage
    accounting, pause detection, and failure taxonomy can't drift between them.
    On a human-in-the-loop interrupt the run is left non-terminal (awaiting_human) with
    its checkpoint held in Redis; a later resume_run picks it up on the same thread_id."""
    run_id, chat_id, cfg, emitter = ctx.run_id, ctx.chat_id, ctx.cfg, ctx.emitter
    session_factory = get_session_factory()
    thread_cfg = {"configurable": {"thread_id": str(run_id)}}
    sub_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    usage_token = _SUBAGENT_USAGE.set(sub_usage)
    usage_counter = UsageCounter()  # standardized tool/sub-agent call capture (incl MCP)
    lf_handler = get_handler()      # off-the-shelf Langfuse tracing (None if disabled)
    callbacks = [usage_counter] + ([lf_handler] if lf_handler else [])
    try:
        # Per-run wall-clock cap OUTSIDE the retry, so a hung loop or stuck tool can't pin
        # a worker (queue) or leak forever (inline). run_span pins the Langfuse trace id.
        async with asyncio.timeout(get_settings().run_timeout_s):
            with run_span(run_id):
                result = await invoke_with_breaker(
                    agent,
                    invoke_input,
                    config={
                        "recursion_limit": max(2, cfg.limits.max_steps),
                        **thread_cfg,
                        "callbacks": callbacks,
                        "metadata": {
                            "langfuse_user_id": ctx.user_id,
                            "langfuse_session_id": str(chat_id),
                            "langfuse_tags": [cfg.llm.provider, get_settings().app_env],
                        },
                    },
                    breaker_key=f"{cfg.llm.provider}:{cfg.llm.base_url}",
                )

        # Token accounting across pause/resume legs:
        #  - root LLM usage lives in result["messages"], which is the CUMULATIVE
        #    checkpointed history — so _extract_usage already spans every leg. Don't add
        #    prior legs' root usage again or it double-counts.
        #  - sub-agent usage is per-leg (collected in the _SUBAGENT_USAGE contextvar, reset
        #    each leg) and is NOT in messages, so it must be accumulated across legs.
        root_usage = _extract_usage(result["messages"])
        cumulative_sub = _merge_usage(ctx.prior_tokens, sub_usage)
        total = _merge_usage(root_usage, cumulative_sub)

        # Paused on a human-in-the-loop interrupt? Persist the pending request + the
        # cumulative sub-agent usage and stop here — DO NOT finalize. The next inbound
        # answer resumes this run (root usage is recovered from the checkpoint).
        # A pause is only possible WITH a checkpointer, and aget_state raises without one,
        # so only inspect graph state when checkpointing is enabled (no-Redis runs skip it).
        if _CHECKPOINTER is not None:
            pending = _pending_interrupt(await agent.aget_state(thread_cfg))
            if pending:
                async with session_factory() as session:
                    await pause_run_for_human(
                        session, run_id=run_id, interrupt=pending, partial_tokens=cumulative_sub
                    )
                await emitter.emit("human.requested", {"request": pending})
                log.info("run.awaiting_human", run_id=str(run_id))
                return

        final = result["messages"][-1]
        reply = getattr(final, "content", "") or ""
        cost = cost_for(cfg.llm.model, total)  # USD, from the static price table
        async with session_factory() as session:
            await insert_message(
                session, chat_id=chat_id, run_id=run_id,
                sender=ctx.sender_id, recipient="user", content=reply,
            )
            await finalize_run(
                session, run_id=run_id, status="succeeded", total_tokens=total,
                total_cost=cost, tool_calls=usage_counter.tool_calls,
            )
        # Meter the full run total once, at the terminal leg (best-effort; run committed).
        await add_usage(ctx.user_id, total.get("total_tokens", 0))

        await emitter.emit("agent.message", {"sender": ctx.sender_id, "content": reply})
        await emitter.emit("run.finished", {"usage": total, "status": "succeeded"})
        log.info("run.succeeded", run_id=str(run_id), tokens=total.get("total_tokens", 0))
    except GraphRecursionError:
        # Bounded step budget reached. Persist a clean reply so the chat isn't silent;
        # run is marked failed so usage/billing accounting stays honest.
        limit = cfg.limits.max_steps
        log.warning("run.recursion_limit", run_id=str(run_id), limit=limit)
        reply = (
            f"I couldn't finish this within {limit} reasoning steps. "
            f"Could you simplify the request or split it into smaller parts?"
        )
        async with session_factory() as session:
            await insert_message(
                session, chat_id=chat_id, run_id=run_id,
                sender=ctx.sender_id, recipient="user", content=reply,
            )
            await finalize_run(
                session, run_id=run_id, status="failed",
                error=info_for(STEP_LIMIT).user_message, error_code=STEP_LIMIT,
            )
        await emitter.emit("agent.message", {"sender": ctx.sender_id, "content": reply})
        await emitter.emit("run.finished", {"status": "failed", "error_code": STEP_LIMIT})
    except TimeoutError:
        # Our per-run wall-clock cap (asyncio.timeout). A builtin TimeoutError here is the
        # run budget — provider timeouts surface as SDK-specific types.
        log.warning("run.timeout", run_id=str(run_id), limit=get_settings().run_timeout_s)
        await _finalize_failure(session_factory, emitter, run_id, info_for(RUN_TIMEOUT))
    except asyncio.CancelledError:
        # Cancellation is control flow (shutdown). Re-raise so arq can re-queue.
        raise
    except Exception as exc:  # noqa: BLE001 — top-level boundary; we log + persist
        info = classify(exc)
        log.exception("run.failed", run_id=str(run_id), error=str(exc), error_code=info.code)
        await _finalize_failure(session_factory, emitter, run_id, info)
    finally:
        _SUBAGENT_USAGE.reset(usage_token)


async def _execute(run_id: UUID, chat_id: UUID, user_text: str, files: list[dict] | None = None) -> None:
    """The first leg of a run. Runs inline (asyncio.create_task) or in an arq worker.

    Idempotent: the queue path is at-least-once, so a worker crash can redeliver a run.
    We no-op if it already finished, and guard the user-message insert so a retry never
    double-writes or double-counts tokens."""
    structlog.contextvars.bind_contextvars(run_id=str(run_id))
    log.info("run.start", run_id=str(run_id), chat_id=str(chat_id))
    session_factory = get_session_factory()

    # Idempotency gate: a duplicate delivery of a terminal run does nothing; otherwise
    # mark running (queued → running) so the lifecycle is observable.
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
        try:
            # Phase 1: pre-LLM DB work in one short-lived session, then close (don't pin a
            # pool connection across the LLM round-trip).
            async with session_factory() as session:
                chat, cfg, system_prompt = await _load_chat_and_agent(session, chat_id=chat_id)
                sender_id = str(chat.agent_id)
                user_id = str(chat.user_id)

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

            # Phase 2: build LangChain messages + agent tree — no session held.
            lc_messages = _to_lc_messages(verbatim)
            if image_blocks and lc_messages:  # multimodal: attach image blocks to last turn
                last_msg = lc_messages[-1]
                if hasattr(last_msg, "content") and isinstance(last_msg.content, str):
                    lc_messages[-1] = HumanMessage(content=[
                        {"type": "text", "text": last_msg.content},
                        *image_blocks,
                    ])
            run_cfg, agent = await _compile_agent(cfg, system_prompt, summary, tc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — prep boundary (bad config, DB, build)
            info = classify(exc)
            log.exception("run.prep_failed", run_id=str(run_id), error=str(exc), error_code=info.code)
            await _finalize_failure(session_factory, emitter, run_id, info)
            return

        # thread_id = run_id so within-run graph state is isolated from cross-turn history.
        ctx = _LegCtx(run_id, chat_id, sender_id, user_id, run_cfg, emitter, {})
        await _drive(agent, {"messages": lc_messages}, ctx)
    finally:
        await emitter.close()
        EMITTERS.pop(run_id, None)


async def resume_run(run_id: UUID, decisions: list[dict]) -> None:
    """Resume a run paused on a human-in-the-loop interrupt with the human's decisions.

    Idempotent: if the run isn't (still) awaiting_human this is a no-op (double delivery,
    or it was already resumed/cancelled). Rebuilds the same agent so the Redis checkpoint
    replays on the same thread_id, then feeds the decisions via Command(resume=...)."""
    structlog.contextvars.bind_contextvars(run_id=str(run_id))
    session_factory = get_session_factory()

    async with session_factory() as session:
        row = await mark_run_resumed(session, run_id=run_id)
        if row is None:
            log.info("run.resume_skip", run_id=str(run_id))
            return
        chat_id = row.chat_id
        prior_tokens = dict(row.partial_tokens or {})

    emitter = RunEventEmitter(run_id, session_factory)
    EMITTERS[run_id] = emitter
    try:
        await emitter.emit("human.responded", {"decisions": decisions})
        try:
            async with session_factory() as session:
                chat, cfg, system_prompt = await _load_chat_and_agent(session, chat_id=chat_id)
                sender_id = str(chat.agent_id)
                user_id = str(chat.user_id)
                summary = chat.summary or ""  # use the stored summary; don't re-summarize on resume
                user_configs = await list_tool_configs(session, user_id=chat.user_id)
                tc = {r.tool_name: r.config for r in user_configs}
            run_cfg, agent = await _compile_agent(cfg, system_prompt, summary, tc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — resume prep boundary
            info = classify(exc)
            log.exception("run.resume_prep_failed", run_id=str(run_id), error=str(exc))
            await _finalize_failure(session_factory, emitter, run_id, info)
            return

        ctx = _LegCtx(run_id, chat_id, sender_id, user_id, run_cfg, emitter, prior_tokens)
        await _drive(agent, Command(resume={"decisions": decisions}), ctx)
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


async def _enforce_concurrency(session: AsyncSession, user_id: UUID, plan: str | None) -> None:
    """Reject if the user is at their plan's concurrent-run cap. Fairness/backpressure
    so one tenant can't flood the queue and starve others.

    ponytail: count-then-create has a tiny race (two exactly-simultaneous requests can
    both pass at cap-1), so the cap is a guardrail, not a hard ceiling. Use an atomic
    Redis INCR with decrement-on-finalize only if strict enforcement is ever required."""
    cap = limits_for(plan).max_concurrent_runs
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


async def _dispatch(coro_factory, *, job_name: str, job_args: tuple, log_id: str, job_id: str | None = None) -> None:
    """Run work via the executor seam: an arq queue job (durable, bounded) or an inline
    asyncio task with crash-safe _PENDING tracking. The queue path falls back to inline if
    the pool is unavailable so a Redis blip can't drop the turn (logged loudly). The worker
    request_id is carried as the last positional so worker logs/traces correlate."""
    if get_settings().run_executor == "queue" and _ARQ_POOL is not None:
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        kwargs = {"_job_id": job_id} if job_id else {}
        await _ARQ_POOL.enqueue_job(job_name, *job_args, request_id, **kwargs)
        return
    if get_settings().run_executor == "queue":
        log.error("run.queue_unavailable_inline_fallback", run_id=log_id)
    task = asyncio.create_task(coro_factory())
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


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

    # If a run on this chat is paused waiting for a human, THIS message is the answer:
    # resume that run instead of starting a new one. Routed before fairness checks — a
    # human's reply to a pending question must never be rejected for concurrency/quota.
    paused = await get_awaiting_run_for_chat(session, chat_id=chat_id)
    if paused is not None:
        decisions = _decisions_from_text(user_text, paused.interrupt or {})
        await _dispatch_resume(paused.id, decisions)
        return paused.id

    # Per-user fairness (concurrency + daily token quota) first, then global shed —
    # all before creating the row so we never persist a run we're about to reject.
    user = await session.get(UserDB, chat.user_id)
    plan = user.plan if user else None
    await _enforce_concurrency(session, chat.user_id, plan)
    await enforce_quota(chat.user_id, plan)
    await _check_load_shed()
    run = await create_run(session, chat_id=chat_id, agent_id=chat.agent_id)
    # _job_id=run_id dedups duplicate enqueues of the same run.
    await _dispatch(
        lambda: _execute(run.id, chat_id, user_text, files=files or []),
        job_name="execute_run",
        job_args=(str(run.id), str(chat_id), user_text, files or []),
        log_id=str(run.id),
        job_id=str(run.id),
    )
    return run.id


async def _dispatch_resume(run_id: UUID, decisions: list[dict]) -> None:
    """Dispatch a resume through the executor seam. No fixed _job_id: a run can pause→resume
    several times and a stable id would let arq's result cache refuse the second resume —
    correctness comes from the atomic awaiting_human→running transition (mark_run_resumed)."""
    await _dispatch(
        lambda: resume_run(run_id, decisions),
        job_name="resume_run_job",
        job_args=(str(run_id), decisions),
        log_id=str(run_id),
    )


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
