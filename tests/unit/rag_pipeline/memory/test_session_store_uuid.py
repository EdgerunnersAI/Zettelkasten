"""Bug 3 regression: UUID serialization in chat message persist.

Iter-06 production observation: post-stream save of the assistant message
threw `Object of type UUID is not JSON serializable` because:

1. Pydantic v2 ``model_dump()`` (used in chat_routes._sse_encode and the
   orchestrator stream "done" event) returns UUID objects as UUID instances,
   which ``json.dumps`` cannot encode by default.
2. Supabase-py performs ``json.dumps`` internally on the insert payload, so any
   UUID-typed value reaching ``insert(payload).execute()`` blows up.

Fix targets the boundary: ``append_assistant_message`` casts every UUID-bearing
field to ``str`` before insert, and the SSE encoder uses ``default=str``.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from website.features.rag_pipeline.memory.session_store import ChatSessionStore
from website.features.rag_pipeline.types import (
    AnswerTurn,
    Citation,
    QueryClass,
    SourceType,
)

pytestmark = pytest.mark.skip(
    reason="v1 session_store retired in Phase 2.7; replaced by tests/unit/rag_pipeline/test_session_store_v2.py"
)


def _make_turn_with_uuid_chunks() -> AnswerTurn:
    return AnswerTurn(
        content="answer body",
        citations=[
            Citation(
                id="c1",
                node_id="yt-x",
                title="X",
                source_type=SourceType.YOUTUBE,
                url="https://youtu.be/x",
                snippet="snip",
                rerank_score=0.9,
            )
        ],
        query_class=QueryClass.LOOKUP,
        critic_verdict="supported",
        critic_notes=None,
        trace_id="trace-1",
        latency_ms=100,
        token_counts={"input": 10, "output": 5},
        llm_model="gemini-2.5-flash",
        retrieved_node_ids=["yt-x"],
        retrieved_chunk_ids=[uuid4(), uuid4()],
    )


@pytest.mark.asyncio
async def test_append_assistant_message_serializes_uuid_chunk_ids():
    """The payload sent to supabase.insert must be JSON-serializable, i.e.
    every UUID is already a string."""
    fake_supabase = MagicMock()
    fake_table = MagicMock()
    fake_supabase.table.return_value = fake_table
    fake_insert = MagicMock()
    fake_table.insert.return_value = fake_insert
    fake_insert.execute.return_value = MagicMock(data=[{"id": "msg-1"}])

    store = ChatSessionStore(supabase=fake_supabase)
    turn = _make_turn_with_uuid_chunks()
    session_id = uuid4()
    user_id = uuid4()

    await store.append_assistant_message(
        session_id=session_id, user_id=user_id, turn=turn
    )

    # The exact payload passed to supabase .insert must round-trip through
    # json.dumps without raising — that is the production failure mode.
    payload = fake_table.insert.call_args.args[0]
    json.dumps(payload)  # MUST NOT raise

    # And explicitly: chunk ids are strings, not UUID objects.
    assert all(isinstance(c, str) for c in payload["retrieved_chunk_ids"])
    assert payload["session_id"] == str(session_id)
    assert payload["user_id"] == str(user_id)


def test_sse_encode_handles_uuid_in_event_payload():
    """The SSE encoder must tolerate UUID-typed values inside the event dict
    (Pydantic v2 model_dump() returns UUIDs as-is)."""
    from website.api.chat_routes import _sse_encode

    event = {
        "type": "done",
        "turn": {
            "retrieved_chunk_ids": [uuid4(), uuid4()],
            "session_id": uuid4(),
            "content": "x",
        },
    }
    encoded = _sse_encode(event)  # MUST NOT raise
    assert "event: done" in encoded
    assert "retrieved_chunk_ids" in encoded
