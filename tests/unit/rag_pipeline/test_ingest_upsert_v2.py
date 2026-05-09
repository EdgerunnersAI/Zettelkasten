"""Unit tests for the v2 chunk-upsert path (Phase 2.5).

Verifies that ``upsert_chunks`` lands chunks in BOTH
``content.canonical_chunks`` AND ``content.workspace_chunk_membership``
via the ContentRepository, with intra-call dedup, halfvec embedding
shape, and the embedding model version stamp.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from website.features.rag_pipeline.ingest.chunker import Chunk
from website.features.rag_pipeline.ingest.upsert import upsert_chunks
from website.features.rag_pipeline.types import ChunkType


def _make_chunk(idx: int, content: str) -> Chunk:
    return Chunk(
        chunk_idx=idx,
        content=content,
        chunk_type=ChunkType.SEMANTIC,
        start_offset=0,
        end_offset=len(content),
        token_count=max(1, len(content.split())),
    )


def _embedder_returning(vectors: list[list[float]]) -> MagicMock:
    """Mock ChunkEmbedder whose ``embed`` returns ``vectors``."""
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=vectors)
    return embedder


@pytest.mark.asyncio
async def test_upsert_chunks_empty_returns_zero():
    repo = MagicMock()
    embedder = _embedder_returning([])
    n = await upsert_chunks(
        workspace_id=uuid4(),
        canonical_zettel_id=uuid4(),
        workspace_zettel_id=uuid4(),
        chunks=[],
        embedder=embedder,
        repo=repo,
    )
    assert n == 0
    repo.upsert_chunks.assert_not_called()
    repo.upsert_workspace_chunk_membership.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_chunks_lands_canonical_and_membership():
    """Both repo writes (canonical + membership) must fire with correct args."""
    workspace_id = uuid4()
    canonical_zettel_id = uuid4()
    workspace_zettel_id = uuid4()
    chunk_ids = [uuid4(), uuid4()]

    repo = MagicMock()
    repo.upsert_chunks.return_value = chunk_ids
    repo.upsert_workspace_chunk_membership.return_value = None

    embedder = _embedder_returning([
        [0.1] * 768,
        [0.2] * 768,
    ])

    chunks = [_make_chunk(0, "alpha bravo"), _make_chunk(1, "charlie delta")]
    n = await upsert_chunks(
        workspace_id=workspace_id,
        canonical_zettel_id=canonical_zettel_id,
        workspace_zettel_id=workspace_zettel_id,
        chunks=chunks,
        embedder=embedder,
        repo=repo,
    )
    assert n == 2

    # canonical chunks payload assertions
    repo.upsert_chunks.assert_called_once()
    args, kwargs = repo.upsert_chunks.call_args
    assert args[0] == canonical_zettel_id
    payload = args[1]
    assert len(payload) == 2
    assert payload[0].chunk_idx == 0
    assert payload[0].content == "alpha bravo"
    assert payload[0].embedding == [0.1] * 768
    assert payload[0].embedding_model_version == "gemini-001-mrl-768"
    assert payload[0].chunk_type == "semantic"
    assert payload[1].chunk_idx == 1
    assert payload[1].embedding == [0.2] * 768

    # membership payload assertions
    repo.upsert_workspace_chunk_membership.assert_called_once_with(
        workspace_id=workspace_id,
        workspace_zettel_id=workspace_zettel_id,
        canonical_chunk_ids=chunk_ids,
    )


@pytest.mark.asyncio
async def test_upsert_chunks_intra_call_dedupe_collapses_duplicates():
    """Two identical chunks within one call must collapse to a single row."""
    repo = MagicMock()
    repo.upsert_chunks.return_value = [uuid4()]
    repo.upsert_workspace_chunk_membership.return_value = None

    embedder = _embedder_returning([[0.5] * 768])

    chunks = [
        _make_chunk(0, "same content"),
        _make_chunk(1, "same content"),  # duplicate by content_hash
    ]
    n = await upsert_chunks(
        workspace_id=uuid4(),
        canonical_zettel_id=uuid4(),
        workspace_zettel_id=uuid4(),
        chunks=chunks,
        embedder=embedder,
        repo=repo,
    )
    assert n == 1
    args, _ = repo.upsert_chunks.call_args
    payload = args[1]
    assert len(payload) == 1
    assert payload[0].chunk_idx == 0  # renumbered compactly


@pytest.mark.asyncio
async def test_upsert_chunks_no_supabase_kg_import():
    """Forensic guard — phase 2.5 grep gate."""
    import website.features.rag_pipeline.ingest.upsert as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "from website.core.supabase_kg" not in src
