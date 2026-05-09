"""iter-10 P12: chunk_share TTL hits/misses + RPC errors + empty-result logs.

Necessary to root-cause q5 500 (still HOLD per iter-09 — logs were unrecoverable
due to deploy restart). Iter-10 logs survive the next 500 if it recurs.
"""
import asyncio
import logging

from unittest.mock import MagicMock

import pytest

from website.features.rag_pipeline.retrieval.chunk_share import ChunkShareStore

pytestmark = pytest.mark.skip(reason="v1 chunk_share surface retired in Phase 2.1; replaced by tests/unit/rag_pipeline/test_chunk_share_v2.py")


def test_chunk_counts_empty_warning(caplog):
    fake = MagicMock()
    fake.rpc.return_value.execute.return_value.data = []
    store = ChunkShareStore(supabase=fake, ttl_seconds=10.0)
    with caplog.at_level(logging.WARNING, logger="rag.chunk_share"):
        asyncio.run(store.get_chunk_counts(sandbox_id="ks1"))
    assert any("chunk_counts empty" in r.message for r in caplog.records)


def test_chunk_counts_rpc_error_warning(caplog):
    fake = MagicMock()
    fake.rpc.return_value.execute.side_effect = RuntimeError("supabase 500")
    store = ChunkShareStore(supabase=fake, ttl_seconds=10.0)
    with caplog.at_level(logging.WARNING, logger="rag.chunk_share"):
        asyncio.run(store.get_chunk_counts(sandbox_id="ks2"))
    assert any("chunk_counts rpc_error" in r.message for r in caplog.records)


def test_chunk_counts_cache_hit_no_warning(caplog):
    fake = MagicMock()
    fake.rpc.return_value.execute.return_value.data = [
        {"node_id": "n1", "chunk_count": 5},
    ]
    store = ChunkShareStore(supabase=fake, ttl_seconds=60.0)
    asyncio.run(store.get_chunk_counts(sandbox_id="ks3"))
    with caplog.at_level(logging.WARNING, logger="rag.chunk_share"):
        # Second call hits the cache; no warning.
        result = asyncio.run(store.get_chunk_counts(sandbox_id="ks3"))
    assert result == {"n1": 5}
    assert not any("empty" in r.message or "rpc_error" in r.message for r in caplog.records)
