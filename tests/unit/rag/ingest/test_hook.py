"""Tests for the canonical RAG chunk ingest hook."""

from __future__ import annotations

import asyncio
from datetime import date
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_ingest_node_chunks_returns_zero_for_totally_empty_payload() -> None:
    from website.features.rag_pipeline.ingest.hook import ingest_node_chunks

    count = await ingest_node_chunks(
        payload={"raw_text": "", "summary": "", "title": "", "url": "", "tags": []},
        user_uuid=uuid4(),
        node_id="node-empty",
    )

    assert count == 0


@pytest.mark.asyncio
async def test_ingest_node_chunks_synthesizes_fallback_from_title_when_bodies_empty(monkeypatch) -> None:
    """When raw_text and summary are both empty but metadata is present, the
    hook must still produce a searchable chunk. Without this fallback, nodes
    with stub YouTube transcripts + failed summarisation are unreachable via
    ``kg_node_chunks`` search."""
    from website.features.rag_pipeline.ingest import hook

    captured_chunk_kwargs: dict = {}

    class _Chunker:
        def __init__(self, *args, **kwargs):
            pass

        def chunk(self, **kwargs):
            captured_chunk_kwargs.update(kwargs)
            return [object()]

    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.gemini_chonkie.GeminiChonkieEmbeddings",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.pool_factory.get_embedding_pool",
        lambda: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.chunker.ZettelChunker",
        _Chunker,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.embedder.ChunkEmbedder",
        lambda *a, **kw: object(),
    )

    async def _fake_upsert(**kwargs):
        return 1

    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.upsert_chunks",
        _fake_upsert,
    )

    count = await hook.ingest_node_chunks(
        payload={
            "raw_text": "",
            "summary": "",
            "title": "Attention Is All You Need",
            "url": "https://www.youtube.com/watch?v=iDulhoQ2pro",
            "source_type": "youtube",
            "tags": ["transformers", "ml"],
            "raw_metadata": {
                "channel_name": "Yannic Kilcher",
                "description": "Paper discussion of the Transformer architecture.",
            },
        },
        user_uuid=uuid4(),
        node_id="yt-attention",
    )

    assert count == 1
    fallback = captured_chunk_kwargs["raw_text"]
    assert "Attention Is All You Need" in fallback
    assert "Yannic Kilcher" in fallback
    assert "transformers" in fallback
    assert "Paper discussion of the Transformer architecture." in fallback
    assert "youtube.com/watch" in fallback


@pytest.mark.asyncio
async def test_ingest_node_chunks_calls_upsert_with_produced_chunks(monkeypatch) -> None:
    from website.features.rag_pipeline.ingest import hook

    produced_chunks = [object(), object(), object()]
    captured_chunk_kwargs: dict = {}

    class _Chunker:
        def __init__(self, *args, **kwargs):
            pass

        def chunk(self, **kwargs):
            captured_chunk_kwargs.update(kwargs)
            return produced_chunks

    class _Embeddings:
        def __init__(self, *args, **kwargs):
            pass

    class _Embedder:
        def __init__(self, *args, **kwargs):
            pass

    captured: dict = {}

    async def _fake_upsert(*, user_id, node_id, chunks, embedder):
        captured["user_id"] = user_id
        captured["node_id"] = node_id
        captured["chunks"] = chunks
        return len(chunks)

    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.gemini_chonkie.GeminiChonkieEmbeddings",
        _Embeddings,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.pool_factory.get_embedding_pool",
        lambda: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.chunker.ZettelChunker",
        _Chunker,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.embedder.ChunkEmbedder",
        _Embedder,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.upsert_chunks",
        _fake_upsert,
    )

    user_uuid = uuid4()
    count = await hook.ingest_node_chunks(
        payload={
            "raw_text": "full article body",
            "title": "Title",
            "source_type": "web",
            "tags": ["topic/ai"],
            "raw_metadata": {"author": "Ada"},
        },
        user_uuid=user_uuid,
        node_id="node-web-1",
    )

    assert count == 3
    assert captured["user_id"] == user_uuid
    assert captured["node_id"] == "node-web-1"
    assert captured["chunks"] is produced_chunks
    assert captured_chunk_kwargs["raw_text"] == "full article body"


@pytest.mark.asyncio
async def test_ingest_node_chunks_prefers_summary_over_stub_youtube_body(monkeypatch) -> None:
    from website.features.rag_pipeline.ingest import hook

    captured_chunk_kwargs: dict = {}

    class _Chunker:
        def __init__(self, *args, **kwargs):
            pass

        def chunk(self, **kwargs):
            captured_chunk_kwargs.update(kwargs)
            return [object()]

    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.gemini_chonkie.GeminiChonkieEmbeddings",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.pool_factory.get_embedding_pool",
        lambda: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.chunker.ZettelChunker",
        _Chunker,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.embedder.ChunkEmbedder",
        lambda *a, **kw: object(),
    )
    
    async def _fake_upsert(**kwargs):
        return 1

    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.upsert_chunks",
        _fake_upsert,
    )

    count = await hook.ingest_node_chunks(
        payload={
            "raw_text": "## Transcript\n\n(Transcript not available for this video)",
            "summary": "Transformer attention replaces recurrence with self-attention and scales better on long-range dependencies.",
            "title": "Attention Is All You Need",
            "source_type": "youtube",
        },
        user_uuid=uuid4(),
        node_id="yt-attention",
    )

    assert count == 1
    assert captured_chunk_kwargs["raw_text"] == (
        "Transformer attention replaces recurrence with self-attention and scales better on long-range dependencies."
    )


@pytest.mark.asyncio
async def test_ingest_node_chunks_swallows_upsert_failures(monkeypatch) -> None:
    from website.features.rag_pipeline.ingest import hook

    class _Chunker:
        def __init__(self, *args, **kwargs):
            pass

        def chunk(self, **kwargs):
            return [object()]

    async def _boom(**_kwargs):
        raise RuntimeError("pgvector connection lost")

    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.gemini_chonkie.GeminiChonkieEmbeddings",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.adapters.pool_factory.get_embedding_pool",
        lambda: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.chunker.ZettelChunker",
        _Chunker,
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.embedder.ChunkEmbedder",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "website.features.rag_pipeline.ingest.upsert.upsert_chunks",
        _boom,
    )

    count = await hook.ingest_node_chunks(
        payload={"raw_text": "body", "title": "T", "source_type": "web"},
        user_uuid=uuid4(),
        node_id="node-web-2",
    )

    assert count == 0


# Integration with the persistence layer: these tests exercise the call site
# at ``nexus/service/persist.py`` to prove that ``rag_chunks_enabled`` is the
# only gate between a successful Supabase write and the canonical hook. A
# regression here would reintroduce the bug the refactor was meant to fix
# (duplicate ingest logic drifting from the canonical implementation).


@pytest.mark.skip(
    reason="v1 _persist_supabase_node + helpers retired in Phase 8.0.3 (B+ atomic "
    "migration); v2 zettel persist runs through _persist_supabase_v2_zettel. "
    "ingest_node_chunks coverage above is unaffected. See plan "
    "docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md"
)
@pytest.mark.asyncio
async def test_persist_supabase_node_invokes_ingest_when_flag_enabled(monkeypatch) -> None:
    from website.core import persist
    from website.features.rag_pipeline.ingest import hook as rag_hook

    class _Repo:
        def node_exists(self, *_args, **_kwargs):
            return False

        def add_node(self, *_args, **_kwargs):
            return None

    payload = {
        "title": "Long-form article",
        "source_type": "web",
        "source_url": "https://example.com/article",
        "summary": "Detailed summary",
        "brief_summary": "Brief summary",
        "raw_text": "Original extracted content",
        "raw_metadata": {"author": "Ada"},
        "tags": ["source/web", "topic/ai"],
    }
    user_uuid = uuid4()
    ingest_calls: dict = {}

    monkeypatch.setattr(persist, "_generate_node_embedding", lambda payload: None)
    monkeypatch.setattr(
        persist,
        "_build_supabase_node_payload",
        lambda **kwargs: type("Node", (), {"id": "web-article"})(),
    )
    monkeypatch.setattr(persist, "_create_semantic_links", lambda **kwargs: None)
    monkeypatch.setattr(persist, "_schedule_entity_extraction", lambda **kwargs: None)
    monkeypatch.setattr(
        persist,
        "get_settings",
        lambda: type("Settings", (), {"rag_chunks_enabled": True})(),
    )

    async def fake_ingest(*, payload, user_uuid, node_id):
        ingest_calls["payload"] = payload
        ingest_calls["user_uuid"] = user_uuid
        ingest_calls["node_id"] = node_id
        return 3

    monkeypatch.setattr(rag_hook, "ingest_node_chunks", fake_ingest)

    node_id, saved, duplicate = await persist._persist_supabase_node(
        payload=payload,
        repo=_Repo(),
        kg_user_id=str(user_uuid),
        captured_on=date.today(),
        brief_summary="Brief summary",
        detailed_summary="Detailed summary",
    )

    assert (node_id, saved, duplicate) == ("web-article", True, False)
    # RAG ingest is scheduled as a background task — drain it before asserting.
    pending = [
        t for t in asyncio.all_tasks() if t.get_name().startswith("rag-chunks-")
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    assert ingest_calls["node_id"] == "web-article"
    assert ingest_calls["user_uuid"] == user_uuid


@pytest.mark.skip(
    reason="v1 _persist_supabase_node + helpers retired in Phase 8.0.3 (B+ atomic "
    "migration). See plan docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md"
)
@pytest.mark.asyncio
async def test_persist_supabase_node_skips_ingest_when_flag_disabled(monkeypatch) -> None:
    from website.core import persist
    from website.features.rag_pipeline.ingest import hook as rag_hook

    class _Repo:
        def node_exists(self, *_args, **_kwargs):
            return False

        def add_node(self, *_args, **_kwargs):
            return None

    called = {"count": 0}

    async def should_not_run(**_kwargs):
        called["count"] += 1
        return 0

    monkeypatch.setattr(persist, "_generate_node_embedding", lambda payload: None)
    monkeypatch.setattr(
        persist,
        "_build_supabase_node_payload",
        lambda **kwargs: type("Node", (), {"id": "web-article"})(),
    )
    monkeypatch.setattr(persist, "_create_semantic_links", lambda **kwargs: None)
    monkeypatch.setattr(persist, "_schedule_entity_extraction", lambda **kwargs: None)
    monkeypatch.setattr(
        persist,
        "get_settings",
        lambda: type("Settings", (), {"rag_chunks_enabled": False})(),
    )
    monkeypatch.setattr(rag_hook, "ingest_node_chunks", should_not_run)

    await persist._persist_supabase_node(
        payload={
            "title": "T",
            "source_type": "web",
            "source_url": "https://example.com/x",
            "summary": "s",
            "brief_summary": "b",
            "raw_text": "body",
        },
        repo=_Repo(),
        kg_user_id=str(uuid4()),
        captured_on=date.today(),
        brief_summary="b",
        detailed_summary="s",
    )

    assert called["count"] == 0
