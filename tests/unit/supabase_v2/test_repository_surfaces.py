from __future__ import annotations

from uuid import UUID

from website.core.supabase_v2.repositories.chat_repository import ChatRepository
from website.core.supabase_v2.repositories.kg_repository import KGRepository
from website.core.supabase_v2.repositories.rag_repository import RAGRepository


class _Execute:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return type("Resp", (), {"data": self.data})()


class _Table:
    def __init__(self, calls, schema, table):
        self.calls = calls
        self.schema = schema
        self.table = table

    def insert(self, payload):
        self.calls.append(("insert", self.schema, self.table, payload))
        return _Execute([{"id": 7 if self.schema == "kg" else "00000000-0000-0000-0000-000000000007"}])

    def upsert(self, payload, **kwargs):
        self.calls.append(("upsert", self.schema, self.table, payload, kwargs))
        return _Execute([{"id": 8 if self.schema == "kg" else "00000000-0000-0000-0000-000000000008"}])


class _Schema:
    def __init__(self, calls, schema):
        self.calls = calls
        self.schema = schema

    def table(self, table):
        self.calls.append(("table", self.schema, table))
        return _Table(self.calls, self.schema, table)

    def rpc(self, name, params):
        self.calls.append(("rpc", self.schema, name, params))
        return _Execute([{"id": 1}, {"id": 2}])


class _Client:
    def __init__(self):
        self.calls = []

    def schema(self, schema):
        self.calls.append(("schema", schema))
        return _Schema(self.calls, schema)

    def table(self, name):  # pragma: no cover
        raise AssertionError(f"unscoped table call: {name}")


def test_kg_repository_uses_kg_schema() -> None:
    client = _Client()
    repo = KGRepository(client)
    node_id = repo.upsert_node(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        node_type="tag",
        canonical_name="AI",
        slug="ai",
    )
    assert node_id == 8
    assert ("table", "kg", "kg_nodes") in client.calls


def test_rag_repository_uses_rag_schema() -> None:
    client = _Client()
    repo = RAGRepository(client)
    kasten_row = repo.create_kasten(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        name="Research",
    )
    assert kasten_row["id"] == "00000000-0000-0000-0000-000000000007"
    assert ("table", "rag", "kastens") in client.calls


def test_chat_repository_uses_rag_schema() -> None:
    client = _Client()
    repo = ChatRepository(client)
    session_id = repo.create_session(
        workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
        profile_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    assert str(session_id) == "00000000-0000-0000-0000-000000000007"
    assert ("table", "rag", "chat_sessions") in client.calls

