"""Idempotence + dedupe-on-content-hash for upsert_chunks.

Calling upsert_chunks twice with the same chunks list must result in 0
NEW rows on the second call. Within a single call, two chunks with
identical content (same content_hash) must collapse to a single row.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

pytestmark = pytest.mark.skip(reason="v1 upsert_chunks surface (kg_node_chunks) retired in Phase 2.5; replaced by tests/unit/rag_pipeline/test_ingest_upsert_v2.py")

from website.features.rag_pipeline.ingest.chunker import Chunk
from website.features.rag_pipeline.ingest.upsert import upsert_chunks
from website.features.rag_pipeline.types import ChunkType


USER_ID = UUID("00000000-0000-0000-0000-000000000001")
NODE_ID = "test-node-1"


def _make_chunks() -> list[Chunk]:
    return [
        Chunk(chunk_idx=0, content="Alpha content one.", chunk_type=ChunkType.SEMANTIC,
              start_offset=0, end_offset=18, token_count=4),
        Chunk(chunk_idx=1, content="Beta content two.", chunk_type=ChunkType.SEMANTIC,
              start_offset=19, end_offset=36, token_count=4),
    ]


class _FakeEmbedder:
    @staticmethod
    def content_hash(text: str) -> bytes:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).digest()

    async def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeSupabaseTable:
    def __init__(self, store: dict):
        self._store = store
        self._filters: dict = {}
        self._mode: str | None = None
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def insert(self, rows):
        self._mode = "insert"
        self._payload = rows
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def upsert(self, payload):
        self._mode = "upsert"
        self._payload = payload
        return self

    def eq(self, key, val):
        self._filters[key] = val
        return self

    def execute(self):
        if self._mode == "select":
            uid, nid = self._filters.get("user_id"), self._filters.get("node_id")
            data = list(self._store.get((uid, nid), []))
            return MagicMock(data=data)
        if self._mode == "insert":
            uid = self._payload[0]["user_id"]
            nid = self._payload[0]["node_id"]
            self._store.setdefault((uid, nid), []).extend(self._payload)
            self._store["__inserts__"].append(len(self._payload))
            return MagicMock(data=self._payload)
        return MagicMock(data=[])


class _FakeSupabase:
    def __init__(self):
        self._store: dict = {"__inserts__": []}

    def table(self, _name):
        return _FakeSupabaseTable(self._store)

    def rpc(self, name, params):
        # Simulate rag_replace_node_chunks: delete all rows for (user, node)
        if name == "rag_replace_node_chunks":
            uid = params["p_user_id"]
            nid = params["p_node_id"]
            self._store[(uid, nid)] = []
        return MagicMock(execute=lambda: MagicMock(data=[]))

    @property
    def inserts(self) -> list[int]:
        return self._store["__inserts__"]


@pytest.mark.asyncio
async def test_second_call_with_same_chunks_inserts_zero():
    fake = _FakeSupabase()
    embedder = _FakeEmbedder()
    chunks = _make_chunks()

    with patch("website.features.rag_pipeline.ingest.upsert.get_supabase_client", return_value=fake):
        first = await upsert_chunks(user_id=USER_ID, node_id=NODE_ID, chunks=chunks, embedder=embedder)
        second = await upsert_chunks(user_id=USER_ID, node_id=NODE_ID, chunks=chunks, embedder=embedder)

    # First call must have inserted exactly len(chunks) rows
    assert fake.inserts and fake.inserts[0] == len(chunks)
    # Second call must result in 0 new rows
    total_inserted_second_call = sum(fake.inserts[1:]) if len(fake.inserts) > 1 else 0
    assert total_inserted_second_call == 0
    assert second == 0


@pytest.mark.asyncio
async def test_intra_call_duplicate_content_hash_dedupes():
    """Two chunks with identical content (same hash) → only one row inserted."""
    fake = _FakeSupabase()
    embedder = _FakeEmbedder()
    dup_text = "Identical content for dedupe."
    chunks = [
        Chunk(chunk_idx=0, content=dup_text, chunk_type=ChunkType.SEMANTIC,
              start_offset=0, end_offset=30, token_count=4),
        Chunk(chunk_idx=1, content=dup_text, chunk_type=ChunkType.SEMANTIC,
              start_offset=30, end_offset=60, token_count=4),
        Chunk(chunk_idx=2, content="Different content.", chunk_type=ChunkType.SEMANTIC,
              start_offset=60, end_offset=78, token_count=3),
    ]

    with patch("website.features.rag_pipeline.ingest.upsert.get_supabase_client", return_value=fake):
        await upsert_chunks(user_id=USER_ID, node_id=NODE_ID, chunks=chunks, embedder=embedder)

    # The single insert call must contain only 2 rows (one per unique hash)
    assert fake.inserts and fake.inserts[0] == 2
