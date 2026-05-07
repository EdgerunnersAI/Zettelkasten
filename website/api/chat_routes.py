"""Chat routes for the user-level RAG experience."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from website.api._citation_guard import check_cited_in_context
from website.api._concurrency import QueueFull, acquire_rerank_slot
from website.api.auth import get_current_user
from website.features.rag_pipeline.service import get_rag_runtime, load_example_queries
from website.features.rag_pipeline.types import ChatQuery, ScopeFilter, SourceType
from website.features.user_pricing.entitlements import consume_entitlement, require_entitlement
from website.features.user_pricing.models import Meter

logger = logging.getLogger("website.api.chat_routes")

router = APIRouter(prefix="/api/rag", tags=["rag-chat"])


class SessionCreateRequest(BaseModel):
    sandbox_id: UUID | None = None
    title: str = "New conversation"
    quality: str = "fast"
    scope_filter: ScopeFilter = Field(default_factory=ScopeFilter)

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"fast", "high"}:
            raise ValueError("quality must be fast or high")
        return normalized


class ChatMessageRequest(BaseModel):
    content: str
    quality: str = "fast"
    scope_filter: ScopeFilter = Field(default_factory=ScopeFilter)
    stream: bool = True
    client_action_id: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content is required")
        if len(cleaned) > 5000:
            raise ValueError("content is too long")
        return cleaned

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"fast", "high"}:
            raise ValueError("quality must be fast or high")
        return normalized


class AdhocChatRequest(ChatMessageRequest):
    sandbox_id: UUID | None = None
    title: str = "Quick ask"


def _runtime_for_user(user: dict):
    try:
        return get_rag_runtime(user["sub"])
    except Exception as exc:
        logger.warning("RAG runtime unavailable for %s: %s", user.get("sub"), exc)
        raise HTTPException(status_code=503, detail="RAG runtime is not available")


def _serialize_session(row: dict) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "sandbox_id": row.get("sandbox_id"),
        "title": row.get("title", "New conversation"),
        "quality_mode": row.get("quality_mode", "fast"),
        "message_count": row.get("message_count", 0),
        "last_message_at": row.get("last_message_at"),
        "last_scope_filter": row.get("last_scope_filter") or {},
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _serialize_message(row: dict) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "citations": row.get("citations") or [],
        "retrieved_node_ids": row.get("retrieved_node_ids") or [],
        "retrieved_chunk_ids": row.get("retrieved_chunk_ids") or [],
        "llm_model": row.get("llm_model") or "",
        "token_counts": row.get("token_counts") or {},
        "latency_ms": row.get("latency_ms") or 0,
        "trace_id": row.get("trace_id") or "",
        "critic_verdict": row.get("critic_verdict"),
        "critic_notes": row.get("critic_notes"),
        "query_class": row.get("query_class"),
        "created_at": row.get("created_at"),
    }


_GENERIC_USER_ERROR = "I hit a temporary error while answering. Please retry in a moment."


def _safe_error_message(exc: BaseException, *, limit: int = 320) -> str:
    """Return a user-safe error string.

    End users must NEVER see raw library exception text (httpx network errors,
    google-genai timeouts, supabase 5xx — all of which stringify in
    confusing ways like the literal "network error" the user kept seeing).
    The full traceback is captured server-side via logger.exception; the
    chat bubble only ever shows a friendly, actionable line.
    """
    del exc, limit
    return _GENERIC_USER_ERROR


def _sse_encode(event: dict[str, Any]) -> str:
    event_name = str(event.get("type") or "message")
    # default=str coerces UUID, datetime, Decimal etc. to strings — Pydantic v2
    # model_dump() returns these as native objects (not JSON-coerced), so the
    # encoder must accept them. Without this, the post-stream "done" event
    # raised "Object of type UUID is not JSON serializable" (iter-06 bug 3).
    payload = json.dumps(event, ensure_ascii=True, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


async def _post_answer_side_effects(runtime, kg_user_id: UUID, session: dict, prompt: str, scope_filter: dict) -> None:
    if session.get("title") == "New conversation":
        await runtime.sessions.auto_title_session(UUID(session["id"]), kg_user_id, prompt)
    await runtime.sessions.update_session(
        UUID(session["id"]),
        kg_user_id,
        last_scope_filter=scope_filter,
        quality_mode=session.get("quality_mode", "fast"),
    )
    if session.get("sandbox_id"):
        await runtime.sandboxes.touch_sandbox(UUID(session["sandbox_id"]), kg_user_id)


async def _safe_side_effects(
    runtime,
    kg_user_id: UUID,
    session: dict,
    prompt: str,
    scope_filter: dict,
) -> None:
    """iter-10 P2: exception-isolated wrapper for fire-and-forget side effects.

    A failed enrichment task MUST NOT crash the worker or 5xx the response;
    log and swallow."""
    try:
        await _post_answer_side_effects(runtime, kg_user_id, session, prompt, scope_filter)
    except Exception:  # noqa: BLE001 — best-effort enrichment
        logger.exception(
            "post_answer_side_effects failed (recoverable) for session %s",
            session.get("id"),
        )


async def _run_answer(runtime, kg_user_id: UUID, session: dict, body: ChatMessageRequest):
    await runtime.sessions.update_session(
        UUID(session["id"]),
        kg_user_id,
        last_scope_filter=body.scope_filter.model_dump(),
        quality_mode=body.quality,
    )
    query = ChatQuery(
        session_id=UUID(session["id"]),
        sandbox_id=UUID(session["sandbox_id"]) if session.get("sandbox_id") else None,
        content=body.content,
        scope_filter=body.scope_filter,
        quality=body.quality,
        stream=body.stream,
    )
    # iter-09 RES-4 + iter-10 P2: slot wraps orchestrator.answer ONLY.
    # _post_answer_side_effects is scheduled as asyncio.create_task AFTER the
    # slot is released so first-message-of-session enrichment (auto-title,
    # session bookkeeping, sandbox touch) never serializes the rerank queue.
    try:
        async with acquire_rerank_slot():
            turn = await runtime.orchestrator.answer(query=query, user_id=kg_user_id)
    except QueueFull as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "queue_full",
                "message": "Rerank capacity full; retry shortly.",
            },
            headers={"Retry-After": "5"},
        ) from exc

    asyncio.create_task(
        _safe_side_effects(
            runtime,
            kg_user_id,
            session,
            body.content,
            body.scope_filter.model_dump(),
        )
    )

    # iter-12 R3 T1: citation drift guard (flag only; never blocks)
    _cites = getattr(turn, "citations", None) or []
    _primary = _cites[0].node_id if _cites else None
    _retrieved = getattr(turn, "retrieved_node_ids", None) or []
    _drift = not check_cited_in_context(primary_citation=_primary, retrieved_node_ids=_retrieved)
    payload: dict = {"session_id": session["id"], "turn": turn.model_dump()}
    if _drift:
        payload["_citation_drift"] = True
    return payload


SSE_HEARTBEAT_INTERVAL_SECONDS = 10.0


async def _heartbeat_wrapper(inner: AsyncIterator[str]) -> AsyncIterator[str]:
    """Emit ``: heartbeat`` SSE comment every 10s alongside the real stream.

    Keeps idle connections warm through Cloudflare/Caddy intermediaries during
    long synthesizer waits (multi-hop ``high`` quality answers can stall 30+s
    before the first token). The client treats ``:`` lines as no-ops.
    """
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def _consume() -> None:
        try:
            async for event in inner:
                await queue.put(event)
        finally:
            await queue.put(sentinel)

    consumer = asyncio.create_task(_consume())
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=SSE_HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is sentinel:
                return
            yield item
    finally:
        if not consumer.done():
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):
                pass


async def _stream_answer_with_backpressure(
    runtime,
    kg_user_id: UUID,
    session: dict,
    body: ChatMessageRequest,
) -> AsyncIterator[str]:
    """Acquire a rerank slot before streaming; emit an SSE error if shed.

    Wrapping inside the generator keeps the existing 200 SSE response shape:
    when capacity is exhausted we surface the 503 metadata as an SSE ``error``
    event so the browser handles it consistently with other late failures.
    """
    try:
        async with acquire_rerank_slot():
            async for event in _stream_answer(runtime, kg_user_id, session, body):
                yield event
    except QueueFull as exc:
        logger.warning("RAG queue full -- shedding request: %s", exc)
        yield _sse_encode(
            {
                "type": "error",
                "code": "queue_full",
                "retry_after_seconds": 5,
                "message": "Server is busy. Please retry in a few seconds.",
            }
        )


async def _stream_answer(
    runtime,
    kg_user_id: UUID,
    session: dict,
    body: ChatMessageRequest,
) -> AsyncIterator[str]:
    # Yield a sentinel SSE frame FIRST so the response headers + first byte
    # flush within milliseconds. Without this, every byte is held back until
    # update_session() + _prepare_query() (which calls Gemini for query
    # rewriting on `high`-quality turns and can take 5–30 s) complete; some
    # browser/proxy combos surface that long header-stall as a generic
    # "network error" before the real answer stream begins.
    yield _sse_encode({"type": "status", "stage": "queued"})

    # Wrap EVERYTHING after the sentinel in one try/except so any failure —
    # update_session DB error, ChatQuery construction error, orchestrator
    # exception, post-answer side-effect exception — surfaces to the client
    # as an SSE `error` event on the already-200 response, never as a 5xx
    # mid-stream connection drop. The latter is what the browser renders as
    # the generic "network error" the user has been seeing.
    try:
        await runtime.sessions.update_session(
            UUID(session["id"]),
            kg_user_id,
            last_scope_filter=body.scope_filter.model_dump(),
            quality_mode=body.quality,
        )
        query = ChatQuery(
            session_id=UUID(session["id"]),
            sandbox_id=UUID(session["sandbox_id"]) if session.get("sandbox_id") else None,
            content=body.content,
            scope_filter=body.scope_filter,
            quality=body.quality,
            stream=True,
        )

        # Server-side retry on the orchestrator iter. The first call after a
        # cold container reliably fails on "network error" — supabase-py /
        # google-genai connection-pool warmup races with the first request.
        # One automatic retry with a short backoff hides that from the user.
        last_exc: Exception | None = None
        produced_any = False
        for attempt in range(2):
            try:
                async for event in runtime.orchestrator.answer_stream(
                    query=query, user_id=kg_user_id
                ):
                    produced_any = True
                    if event.get("type") == "done":
                        # iter-10 P2: schedule side effects fire-and-forget so
                        # they don't hold the rerank slot (the outer
                        # _stream_answer_with_backpressure releases the slot
                        # when this generator exits — anything still awaited
                        # here pins it).
                        asyncio.create_task(
                            _safe_side_effects(
                                runtime,
                                kg_user_id,
                                session,
                                body.content,
                                body.scope_filter.model_dump(),
                            )
                        )
                        # iter-12 R3 T1: citation drift guard (flag only; never blocks)
                        turn_data = (event.get("turn") or {})
                        _primary = (turn_data.get("citations") or [{}])[0].get("node_id") if (turn_data.get("citations") or []) else None
                        if not check_cited_in_context(primary_citation=_primary, retrieved_node_ids=turn_data.get("retrieved_node_ids") or []):
                            event = dict(event, _citation_drift=True)
                    yield _sse_encode(event)
                last_exc = None
                break
            except Exception as inner_exc:
                last_exc = inner_exc
                logger.warning(
                    "answer_stream attempt %d/2 failed for session %s: %r",
                    attempt + 1,
                    session["id"],
                    inner_exc,
                )
                if produced_any:
                    # Already streamed tokens to the client; cannot rewind.
                    break
                if attempt + 1 < 2:
                    await asyncio.sleep(0.8)
                    continue

        if last_exc is not None:
            raise last_exc
    except Exception as exc:
        logger.exception(
            "Streaming answer failed for session %s: %r", session["id"], exc
        )
        yield _sse_encode(
            {
                "type": "error",
                "code": "chat_failed",
                "message": _safe_error_message(exc) or "The pipeline hit an error. Please retry.",
            }
        )


@router.get("/example-queries")
async def example_queries(user: Annotated[dict, Depends(get_current_user)]):
    del user
    return {"queries": load_example_queries()}


@router.get("/sessions")
async def list_sessions(
    user: Annotated[dict, Depends(get_current_user)],
    sandbox_id: UUID | None = None,
    limit: int = 50,
):
    runtime = _runtime_for_user(user)
    rows = await runtime.sessions.list_sessions(runtime.kg_user_id, sandbox_id=sandbox_id, limit=limit)
    return {"sessions": [_serialize_session(row) for row in rows]}


@router.post("/sessions")
async def create_session(
    body: SessionCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    session_id = await runtime.sessions.create_session(
        user_id=runtime.kg_user_id,
        sandbox_id=body.sandbox_id,
        title=body.title,
        initial_scope_filter=body.scope_filter.model_dump(),
        quality_mode=body.quality,
    )
    row = await runtime.sessions.get_session(session_id, runtime.kg_user_id)
    return {"session": _serialize_session(row)}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    row = await runtime.sessions.get_session(session_id, runtime.kg_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": _serialize_session(row)}


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 100,
):
    runtime = _runtime_for_user(user)
    session = await runtime.sessions.get_session(session_id, runtime.kg_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    rows = await runtime.sessions.list_messages(session_id, runtime.kg_user_id, limit=limit)
    return {"messages": [_serialize_message(row) for row in rows]}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    deleted = await runtime.sessions.delete_session(session_id, runtime.kg_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok", "session_id": str(session_id)}


@router.post("/sessions/{session_id}/messages")
async def create_message(
    session_id: UUID,
    body: ChatMessageRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    session = await runtime.sessions.get_session(session_id, runtime.kg_user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    action_id = body.client_action_id or str(session_id)
    await require_entitlement(Meter.RAG_QUESTION, user, action_id=action_id)

    if body.stream:
        from website.api._concurrency import _get_state

        state = _get_state()
        if state.depth >= state.queue_max:
            raise HTTPException(
                status_code=503,
                detail={"reason": "queue_full", "retry_after_seconds": 5},
                headers={"Retry-After": "5"},
            )
        return StreamingResponse(
            _heartbeat_wrapper(
                _stream_answer_with_backpressure(runtime, runtime.kg_user_id, session, body)
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    payload = await _run_answer(runtime, runtime.kg_user_id, session, body)
    await consume_entitlement(Meter.RAG_QUESTION, user, action_id=action_id)
    return payload


@router.post("/adhoc")
async def adhoc_message(
    body: AdhocChatRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    action_id = body.client_action_id or body.content[:160]
    await require_entitlement(Meter.RAG_QUESTION, user, action_id=action_id)
    runtime = _runtime_for_user(user)
    session_id = await runtime.sessions.create_session(
        user_id=runtime.kg_user_id,
        sandbox_id=body.sandbox_id,
        title=body.title,
        initial_scope_filter=body.scope_filter.model_dump(),
        quality_mode=body.quality,
    )
    session = await runtime.sessions.get_session(session_id, runtime.kg_user_id)
    if session is None:
        raise HTTPException(status_code=500, detail="Session could not be created")

    # iter-04: admission gate applied to BOTH stream and non-stream paths.
    # Previously only the stream branch checked queue depth — burst-12 to
    # /api/rag/adhoc with stream=False produced 12/12 = 502 because the
    # gate never fired and gunicorn workers blocked behind the OS accept
    # queue. Non-stream now returns 503 Retry-After:5 the same way.
    from website.api._concurrency import _get_state

    state = _get_state()
    if state.depth >= state.queue_max:
        raise HTTPException(
            status_code=503,
            detail={"reason": "queue_full", "retry_after_seconds": 5},
            headers={"Retry-After": "5"},
        )

    if body.stream:
        return StreamingResponse(
            _heartbeat_wrapper(
                _stream_answer_with_backpressure(runtime, runtime.kg_user_id, session, body)
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    payload = await _run_answer(runtime, runtime.kg_user_id, session, body)
    await consume_entitlement(Meter.RAG_QUESTION, user, action_id=action_id)
    payload["session"] = _serialize_session(session)
    return payload

