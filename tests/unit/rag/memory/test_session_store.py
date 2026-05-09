from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from website.features.rag_pipeline.memory.session_store import ChatSessionStore
from website.features.rag_pipeline.types import AnswerTurn, Citation, QueryClass, SourceType

pytestmark = pytest.mark.skip(
    reason="v1 session_store retired in Phase 2.7; replaced by tests/unit/rag_pipeline/test_session_store_v2.py"
)


class _TableQuery:
    def __init__(self, client, table_name):
        self._client = client
        self._table_name = table_name
        self._filters = {}
        self._limit = None
        self._order_desc = False
        self._payload = None
        self._op = "select"

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def order(self, *_args, desc=False, **_kwargs):
        self._order_desc = desc
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def execute(self):
        return self._client.execute(self)


class _Supabase:
    def __init__(self):
        self.sessions = []
        self.messages = []

    def table(self, name):
        return _TableQuery(self, name)

    def execute(self, query):
        if query._table_name == "chat_sessions":
            dataset = self.sessions
        else:
            dataset = self.messages

        if query._op == "insert":
            row = dict(query._payload)
            row.setdefault("id", str(uuid4()))
            dataset.append(row)
            return SimpleNamespace(data=[row])

        if query._op == "delete":
            kept = []
            deleted = []
            for row in dataset:
                if all(str(row.get(key)) == str(value) for key, value in query._filters.items()):
                    deleted.append(row)
                else:
                    kept.append(row)
            if query._table_name == "chat_sessions":
                self.sessions = kept
            else:
                self.messages = kept
            return SimpleNamespace(data=deleted)

        if query._op == "update":
            updated = []
            for row in dataset:
                if all(str(row.get(key)) == str(value) for key, value in query._filters.items()):
                    row.update(query._payload)
                    updated.append(row)
            return SimpleNamespace(data=updated)

        filtered = [
            row for row in dataset
            if all(str(row.get(key)) == str(value) for key, value in query._filters.items())
        ]
        if query._table_name == "chat_messages":
            filtered.sort(key=lambda row: row.get("created_at", ""), reverse=query._order_desc)
        elif query._order_desc:
            filtered.reverse()
        if query._limit is not None:
            filtered = filtered[: query._limit]
        return SimpleNamespace(data=filtered)


@pytest.mark.asyncio
async def test_create_get_list_delete_session() -> None:
    supabase = _Supabase()
    store = ChatSessionStore(supabase=supabase)
    user_id = uuid4()
    sandbox_id = uuid4()

    session_id = await store.create_session(user_id=user_id, sandbox_id=sandbox_id)
    session = await store.get_session(session_id, user_id)
    sessions = await store.list_sessions(user_id, sandbox_id=sandbox_id)
    deleted = await store.delete_session(session_id, user_id)

    assert session["id"] == session_id
    assert len(sessions) == 1
    assert deleted is True


@pytest.mark.asyncio
async def test_load_recent_turns_returns_oldest_first() -> None:
    supabase = _Supabase()
    store = ChatSessionStore(supabase=supabase)
    session_id = uuid4()
    user_id = uuid4()
    now = datetime.now(tz=timezone.utc)
    supabase.messages.extend([
        {"session_id": str(session_id), "user_id": str(user_id), "role": "user", "content": "second", "created_at": (now + timedelta(seconds=2)).isoformat()},
        {"session_id": str(session_id), "user_id": str(user_id), "role": "assistant", "content": "first", "created_at": now.isoformat()},
    ])

    turns = await store.load_recent_turns(session_id, user_id)

    assert [turn.content for turn in turns] == ["first", "second"]


@pytest.mark.asyncio
async def test_append_user_and_assistant_message() -> None:
    supabase = _Supabase()
    store = ChatSessionStore(supabase=supabase)
    session_id = uuid4()
    user_id = uuid4()
    await store.append_user_message(session_id=session_id, user_id=user_id, content="hello")
    turn = AnswerTurn(
        content="answer",
        citations=[Citation(id="node-1", node_id="node-1", title="Node", source_type=SourceType.WEB, url="https://example.com", snippet="snippet")],
        query_class=QueryClass.LOOKUP,
        critic_verdict="supported",
        trace_id="trace-1",
        latency_ms=100,
        token_counts={"total": 10},
        llm_model="gemini-2.5-flash",
        retrieved_node_ids=["node-1"],
        retrieved_chunk_ids=[],
    )
    await store.append_assistant_message(session_id=session_id, user_id=user_id, turn=turn)

    assert len(supabase.messages) == 2
    assert supabase.messages[1]["critic_verdict"] == "supported"


@pytest.mark.asyncio
async def test_auto_title_uses_first_line_up_to_60_chars() -> None:
    supabase = _Supabase()
    store = ChatSessionStore(supabase=supabase)
    user_id = uuid4()
    session_id = await store.create_session(user_id=user_id, sandbox_id=None)

    title = await store.auto_title_session(session_id, user_id, "This is a very long first line that should be trimmed down to fit neatly into the UI\nrest")

    assert len(title) <= 63
    assert supabase.sessions[0]["title"] == title

