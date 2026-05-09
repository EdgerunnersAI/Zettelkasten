from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skip(reason="v1 upsert_chunks surface (kg_node_chunks) retired in Phase 2.5; replaced by tests/unit/rag_pipeline/test_ingest_upsert_v2.py")

from website.features.rag_pipeline.ingest.chunker import Chunk
from website.features.rag_pipeline.ingest.upsert import upsert_chunks
from website.features.rag_pipeline.types import ChunkType


class _TableQuery:
    def __init__(self, client, table_name: str):
        self._client = client
        self._table_name = table_name

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def insert(self, rows):
        self._client.operations.append(("insert", self._table_name, rows))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))

    def execute(self):
        return SimpleNamespace(data=self._client.existing_rows)


class _RPCQuery:
    def __init__(self, client, name: str, payload: dict):
        self._client = client
        self._name = name
        self._payload = payload

    def execute(self):
        self._client.operations.append(("rpc", self._name, self._payload))
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self, existing_rows):
        self.existing_rows = existing_rows
        self.operations = []

    def table(self, table_name: str):
        return _TableQuery(self, table_name)

    def rpc(self, name: str, payload: dict):
        return _RPCQuery(self, name, payload)


class _FakeEmbedder:
    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(index)] * 3 for index, _ in enumerate(texts, start=1)]

    @staticmethod
    def content_hash(text: str) -> bytes:
        return text.encode("utf-8").ljust(32, b"0")[:32]


@pytest.mark.asyncio
async def test_upsert_skips_unchanged_chunks(monkeypatch) -> None:
    embedder = _FakeEmbedder()
    chunks = [
        Chunk(chunk_idx=0, content="alpha", chunk_type=ChunkType.ATOMIC, token_count=1),
        Chunk(chunk_idx=1, content="beta", chunk_type=ChunkType.ATOMIC, token_count=1),
    ]
    client = _FakeSupabase(
        existing_rows=[
            {"chunk_idx": 0, "content_hash": embedder.content_hash("alpha"), "embedding": [0.1, 0.1, 0.1]},
            {"chunk_idx": 1, "content_hash": embedder.content_hash("beta"), "embedding": [0.2, 0.2, 0.2]},
        ]
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.get_supabase_client",
        lambda: client,
    )

    embedded = await upsert_chunks(
        user_id=uuid4(),
        node_id="node-1",
        chunks=chunks,
        embedder=embedder,
    )

    assert embedded == 0
    assert embedder.calls == []


@pytest.mark.asyncio
async def test_upsert_re_embeds_only_changed_chunks(monkeypatch) -> None:
    embedder = _FakeEmbedder()
    chunks = [
        Chunk(chunk_idx=0, content="alpha", chunk_type=ChunkType.ATOMIC, token_count=1),
        Chunk(chunk_idx=1, content="beta-new", chunk_type=ChunkType.ATOMIC, token_count=1),
        Chunk(chunk_idx=2, content="gamma", chunk_type=ChunkType.ATOMIC, token_count=1),
    ]
    client = _FakeSupabase(
        existing_rows=[
            {"chunk_idx": 0, "content_hash": embedder.content_hash("alpha"), "embedding": [0.1, 0.1, 0.1]},
            {"chunk_idx": 1, "content_hash": embedder.content_hash("beta-old"), "embedding": [0.2, 0.2, 0.2]},
            {"chunk_idx": 2, "content_hash": embedder.content_hash("gamma"), "embedding": [0.3, 0.3, 0.3]},
        ]
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.get_supabase_client",
        lambda: client,
    )

    embedded = await upsert_chunks(
        user_id=uuid4(),
        node_id="node-1",
        chunks=chunks,
        embedder=embedder,
    )

    assert embedded == 1
    assert embedder.calls == [["beta-new"]]


@pytest.mark.asyncio
async def test_upsert_calls_rag_replace_node_chunks_rpc_before_insert(monkeypatch) -> None:
    embedder = _FakeEmbedder()
    chunks = [
        Chunk(chunk_idx=0, content="alpha", chunk_type=ChunkType.ATOMIC, token_count=1),
    ]
    client = _FakeSupabase(existing_rows=[])
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.get_supabase_client",
        lambda: client,
    )

    await upsert_chunks(
        user_id=uuid4(),
        node_id="node-1",
        chunks=chunks,
        embedder=embedder,
    )

    assert [operation[0] for operation in client.operations] == ["rpc", "insert"]

