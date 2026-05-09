from __future__ import annotations

from uuid import UUID

from website.core.supabase_v2.models import (
    CanonicalChunkCreate,
    CanonicalZettelCreate,
    QuotaDebitRequest,
    WorkspaceZettelCreate,
)
from website.core.supabase_v2.repositories.billing_repository import BillingRepository
from website.core.supabase_v2.repositories.content_repository import ContentRepository


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

    def upsert(self, payload, **kwargs):
        self.calls.append(("upsert", self.schema, self.table, payload, kwargs))
        return _Execute([{"id": "00000000-0000-0000-0000-000000000101", "was_new": True}])

    def insert(self, payload):
        self.calls.append(("insert", self.schema, self.table, payload, {}))
        return _Execute([payload])


class _Schema:
    def __init__(self, calls, schema):
        self.calls = calls
        self.schema = schema

    def table(self, table):
        self.calls.append(("table", self.schema, table))
        return _Table(self.calls, self.schema, table)

    def rpc(self, name, params):
        self.calls.append(("rpc", self.schema, name, params))
        # Phase 1.C `content.upsert_canonical_zettel` returns (id, was_new).
        # Other v2 RPCs (e.g. billing.consume_quota) historically returned a
        # boolean; preserve that shape for non-canonical RPCs.
        if name == "upsert_canonical_zettel":
            return _Execute(
                [{"id": "00000000-0000-0000-0000-000000000101", "was_new": True}]
            )
        return _Execute(True)


class _Client:
    def __init__(self):
        self.calls = []

    def schema(self, schema):
        self.calls.append(("schema", schema))
        return _Schema(self.calls, schema)

    def table(self, name):  # pragma: no cover - should never be used by v2 repos
        raise AssertionError(f"unexpected unscoped table call: {name}")


def test_content_repository_uses_schema_table_form() -> None:
    fake = _Client()
    repo = ContentRepository(fake)

    result = repo.upsert_canonical_zettel(
        CanonicalZettelCreate(
            normalized_url="https://example.com/a",
            content_hash=b"abc",
            source_type="web",
            title="A",
        )
    )

    assert result.canonical_zettel_id == UUID("00000000-0000-0000-0000-000000000101")
    assert result.was_new is True
    assert ("schema", "content") in fake.calls
    # Phase 3.1: canonical upsert flows through the race-safe Phase 1.C RPC,
    # not a plain `.table("canonical_zettels").upsert(...)`.
    rpc_calls = [c for c in fake.calls if c[0] == "rpc"]
    assert any(c[1] == "content" and c[2] == "upsert_canonical_zettel" for c in rpc_calls)


def test_content_repository_links_workspace_chunks_for_search() -> None:
    fake = _Client()
    repo = ContentRepository(fake)

    repo.upsert_canonical_zettel(
        CanonicalZettelCreate(
            normalized_url="https://example.com/a",
            content_hash=b"abc",
            source_type="web",
            title="A",
        ),
        workspace=WorkspaceZettelCreate(
            workspace_id=UUID("00000000-0000-0000-0000-000000000201"),
            added_via="website",
        ),
        chunks=[
            CanonicalChunkCreate(
                chunk_idx=0,
                content="chunk",
                content_hash=b"chunk",
            )
        ],
    )

    membership_upserts = [
        call
        for call in fake.calls
        if call[0:3] == ("upsert", "content", "workspace_chunk_membership")
    ]
    assert membership_upserts
    payload = membership_upserts[0][3][0]
    assert payload["workspace_id"] == "00000000-0000-0000-0000-000000000201"
    assert payload["workspace_zettel_id"] == "00000000-0000-0000-0000-000000000101"
    assert membership_upserts[0][4]["on_conflict"] == (
        "workspace_id,canonical_chunk_id,workspace_zettel_id"
    )


def test_billing_repository_uses_typed_consume_quota_rpc() -> None:
    fake = _Client()
    repo = BillingRepository(fake)
    ok = repo.consume_quota(
        QuotaDebitRequest(
            workspace_id=UUID("00000000-0000-0000-0000-000000000001"),
            feature="rag_query",
            unit="query",
            period_start="2026-05-01T00:00:00Z",
        )
    )

    assert ok is True
    assert fake.calls[-1][0:3] == ("rpc", "core", "consume_quota")
