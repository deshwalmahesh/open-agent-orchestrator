"""Background run execution.

Flow per turn:
  POST /chats/{id}/messages → create Run row → spawn asyncio.create_task → return run_id
  The task: emit run.started, build messages from history (last window N), invoke agent,
  persist agent reply, emit run.finished with usage, close emitter, drop from registry.

No checkpointer is wired here — chat memory is rebuilt from MessageDB each turn
(simpler, no Redis dependency). The checkpointer seam in build_agent stays for
future HITL/interrupt work.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.db import get_session_factory
from app.db.models import AgentDB, ChatDB, PersonaDB
from app.db.repos import (
    create_run,
    finalize_run,
    insert_message,
    list_messages,
)
from app.domain import AgentConfig, LLMConfig
from app.llm import build_chat_model
from app.runtime.agent import build_agent
from app.runtime.events import EMITTERS, RunEventEmitter

log = structlog.get_logger()


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
    for m in result_messages:
        usage = getattr(m, "usage_metadata", None)
        if not usage:
            continue
        for k in totals:
            totals[k] += usage.get(k, 0) or 0
    return totals


async def _load_chat_and_agent(
    session: AsyncSession, *, chat_id: UUID
) -> tuple[ChatDB, AgentConfig, str]:
    """Resolve chat → agent → effective system prompt (persona override or agent's)."""
    chat = await session.get(ChatDB, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    agent_row = await session.get(AgentDB, chat.agent_id)
    if agent_row is None:
        raise ValueError(f"agent not found: {chat.agent_id}")
    cfg = AgentConfig.model_validate(agent_row.config)

    effective_prompt = cfg.system_prompt
    if chat.persona_id is not None:
        persona = await session.get(PersonaDB, chat.persona_id)
        if persona is not None:
            effective_prompt = persona.system_prompt

    return chat, cfg, effective_prompt


async def _execute(run_id: UUID, chat_id: UUID, user_text: str) -> None:
    """The actual run, scheduled via asyncio.create_task."""
    session_factory = get_session_factory()
    emitter = RunEventEmitter(run_id, session_factory)
    EMITTERS[run_id] = emitter

    try:
        await emitter.emit("run.started", {"chat_id": str(chat_id), "input": user_text})

        async with session_factory() as session:
            chat, cfg, system_prompt = await _load_chat_and_agent(session, chat_id=chat_id)
            # User turn persisted first so the agent sees its own newest input.
            await insert_message(
                session, chat_id=chat_id, run_id=run_id, sender="user", content=user_text
            )
            summary, verbatim = await _resolve_context(session, chat, cfg)

        lc_messages = _to_lc_messages(verbatim)
        run_cfg = cfg.model_copy(update={"system_prompt": _effective_prompt(system_prompt, summary)})
        agent = build_agent(run_cfg)
        # recursion_limit caps tool-loop iterations; LangGraph raises if exceeded.
        result = await agent.ainvoke(
            {"messages": lc_messages},
            config={"recursion_limit": max(2, cfg.limits.max_steps)},
        )

        final = result["messages"][-1]
        reply = getattr(final, "content", "") or ""
        usage = _extract_usage(result["messages"])

        # sender = AgentDB row id (chat.agent_id), NOT cfg.id — cfg.id is the
        # AgentConfig pydantic instance UUID, regenerated on every model_validate()
        # because the field has default_factory=uuid4. Using it would make sender
        # unjoinable to the agents table.
        sender_id = str(chat.agent_id)
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
                session, run_id=run_id, status="succeeded", total_tokens=usage
            )

        await emitter.emit("agent.message", {"sender": sender_id, "content": reply})
        await emitter.emit("run.finished", {"usage": usage, "status": "succeeded"})
    except Exception as exc:  # noqa: BLE001 — top-level boundary; we log + persist
        log.exception("run.failed", run_id=str(run_id), error=str(exc))
        async with session_factory() as session:
            await finalize_run(
                session, run_id=run_id, status="failed", error=str(exc)[:1000]
            )
        await emitter.emit("run.finished", {"status": "failed", "error": str(exc)[:500]})
    finally:
        await emitter.close()
        EMITTERS.pop(run_id, None)


# Strong refs to in-flight runs so the event loop doesn't GC them mid-execution
# and lifespan shutdown can drain them cleanly (important for test isolation).
_PENDING: set[asyncio.Task] = set()


async def start_run(
    session: AsyncSession, *, chat_id: UUID, user_text: str
) -> UUID:
    """Create Run row, schedule background task, return run_id immediately."""
    chat = await session.get(ChatDB, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    run = await create_run(session, chat_id=chat_id, agent_id=chat.agent_id)
    task = asyncio.create_task(_execute(run.id, chat_id, user_text))
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
