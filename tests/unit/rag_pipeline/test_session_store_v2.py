"""Unit tests for the v2 ChatSessionStore (Phase 2.7).

CRITICAL invariant: every insert into rag.chat_sessions / rag.chat_messages
must include a non-null workspace_id (v2 NOT NULL constraint). These
tests verify the store derives workspace_id from the user's profile via
core.workspace_members and propagates it to every repo call.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from website.features.rag_pipeline.memory.session_store import (
    ChatSessionStore,
    _UnknownWorkspaceError,
)
from website.features.rag_pipeline.types import (
    AnswerTurn,
    Citation,
    QueryClass,
    SourceType,
)


def _store(workspace_id: UUID) -> tuple[ChatSessionStore, MagicMock, MagicMock]:
    repo = MagicMock()
    core = MagicMock()
    core.get_default_workspace_id.return_value = workspace_id
    return ChatSessionStore(repo=repo, core_repo=core), repo, core


@pytest.mark.asyncio
async def test_create_session_passes_workspace_id():
    workspace_id = uuid4()
    store, repo, _ = _store(workspace_id)
    profile_id = uuid4()
    repo.create_chat_session.return_value = uuid4()

    await store.create_session(
        user_id=profile_id,
        sandbox_id=None,
    )
    repo.create_chat_session.assert_called_once()
    kw = repo.create_chat_session.call_args.kwargs
    assert kw["workspace_id"] == workspace_id  # NOT NULL constraint honoured
    assert kw["profile_id"] == profile_id
    assert kw["kasten_id"] is None


@pytest.mark.asyncio
async def test_append_user_message_passes_workspace_id():
    workspace_id = uuid4()
    store, repo, _ = _store(workspace_id)
    repo.append_chat_message.return_value = {"id": str(uuid4())}

    profile_id = uuid4()
    session_id = uuid4()
    await store.append_user_message(
        session_id=session_id, user_id=profile_id, content="hi",
    )
    kw = repo.append_chat_message.call_args.kwargs
    assert kw["workspace_id"] == workspace_id  # NOT NULL constraint honoured
    assert kw["session_id"] == session_id
    assert kw["role"] == "user"
    assert kw["content"] == "hi"


@pytest.mark.asyncio
async def test_append_assistant_message_passes_workspace_id_and_citations():
    workspace_id = uuid4()
    store, repo, _ = _store(workspace_id)
    repo.append_chat_message.return_value = {"id": str(uuid4())}

    citation = Citation(
        id="c1",
        node_id="z1",
        title="t",
        source_type=SourceType.WEB,
        url="https://example.com",
        snippet="hello",
        rerank_score=0.9,
    )
    turn = AnswerTurn(
        content="answer",
        citations=[citation],
        retrieved_node_ids=["z1"],
        retrieved_chunk_ids=[uuid4()],
        llm_model="gemini-2.5-flash",
        token_counts={"input": 100, "output": 20},
        latency_ms=420,
        trace_id="t-1",
        critic_verdict="supported",
        critic_notes=None,
        query_class=QueryClass.LOOKUP,
    )

    await store.append_assistant_message(
        session_id=uuid4(), user_id=uuid4(), turn=turn,
    )
    kw = repo.append_chat_message.call_args.kwargs
    assert kw["workspace_id"] == workspace_id
    assert kw["role"] == "assistant"
    assert kw["content"] == "answer"
    assert kw["verdict"] == "supported"
    assert kw["latency_ms"] == 420
    assert isinstance(kw["citations"], list) and len(kw["citations"]) == 1


@pytest.mark.asyncio
async def test_unknown_workspace_raises_not_silently_swallowed():
    """The store must propagate (not swallow) unknown-workspace failures.

    Anti-pattern guard: NEVER swallow exceptions; better to raise loud than
    silently insert NULL workspace_id (which would also fail at the DB).
    """
    repo = MagicMock()
    core = MagicMock()
    core.get_default_workspace_id.return_value = None
    store = ChatSessionStore(repo=repo, core_repo=core)

    with pytest.raises(_UnknownWorkspaceError):
        await store.create_session(user_id=uuid4(), sandbox_id=None)
    repo.create_chat_session.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_id_cached_per_profile():
    """The core lookup should fire once per profile, then be reused."""
    workspace_id = uuid4()
    store, repo, core = _store(workspace_id)
    profile_id = uuid4()
    repo.create_chat_session.return_value = uuid4()
    repo.append_chat_message.return_value = {"id": str(uuid4())}

    await store.create_session(user_id=profile_id, sandbox_id=None)
    await store.append_user_message(
        session_id=uuid4(), user_id=profile_id, content="x",
    )
    await store.append_user_message(
        session_id=uuid4(), user_id=profile_id, content="y",
    )
    assert core.get_default_workspace_id.call_count == 1


@pytest.mark.asyncio
async def test_load_recent_turns_returns_chronological_tail():
    workspace_id = uuid4()
    store, repo, _ = _store(workspace_id)
    repo.list_chat_messages.return_value = [
        {"role": "user", "content": "q1", "created_at": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "content": "a1", "created_at": "2026-01-01T00:00:01Z"},
        {"role": "user", "content": "q2", "created_at": "2026-01-01T00:00:02Z"},
    ]
    turns = await store.load_recent_turns(uuid4(), uuid4(), limit=2)
    assert len(turns) == 2
    assert turns[0].content == "a1"
    assert turns[1].content == "q2"


@pytest.mark.asyncio
async def test_no_supabase_kg_import():
    import website.features.rag_pipeline.memory.session_store as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "from website.core.supabase_kg" not in src
    # Legacy v1 table names must not appear in code (docstring mentions OK).
    import re
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    assert ".table(\"chat_sessions\"" not in code_only
    assert ".table(\"chat_messages\"" not in code_only
