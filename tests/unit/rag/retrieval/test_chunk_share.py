import time

import pytest

pytestmark = pytest.mark.skip(reason="v1 chunk_share surface retired in Phase 2.1; replaced by tests/unit/rag_pipeline/test_chunk_share_v2.py")

import pytest
from unittest.mock import MagicMock
from website.features.rag_pipeline.retrieval.chunk_share import ChunkShareStore


def test_chunk_share_returns_per_kasten_counts():
    fake_supabase = MagicMock()
    fake_supabase.rpc.return_value.execute.return_value.data = [
        {"node_id": "a", "chunk_count": 16},
        {"node_id": "b", "chunk_count": 6},
        {"node_id": "c", "chunk_count": 2},
    ]
    store = ChunkShareStore(supabase=fake_supabase)
    import asyncio
    result = asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    assert result == {"a": 16, "b": 6, "c": 2}


def test_chunk_share_penalty_factor_inverse_sqrt():
    from website.features.rag_pipeline.retrieval.chunk_share import compute_chunk_share_penalty
    # 16-chunk node → 1/sqrt(16) = 0.25
    assert abs(compute_chunk_share_penalty(16) - 0.25) < 0.001
    assert compute_chunk_share_penalty(1) == 1.0
    assert compute_chunk_share_penalty(0) == 1.0


# iter-08 G4: TTLCache for ChunkShareStore --------------------------------

def test_chunk_share_caches_within_ttl():
    """iter-08 G4: two reads within ttl → 1 Supabase call."""
    fake_supabase = MagicMock()
    fake_supabase.rpc.return_value.execute.return_value.data = [
        {"node_id": "a", "chunk_count": 5},
    ]
    store = ChunkShareStore(supabase=fake_supabase, ttl_seconds=60.0)
    import asyncio
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    assert fake_supabase.rpc.call_count == 1, "two reads within ttl should hit cache"


def test_chunk_share_cache_expires_after_ttl():
    """iter-08 G4: read after ttl → 2nd Supabase call."""
    fake_supabase = MagicMock()
    fake_supabase.rpc.return_value.execute.return_value.data = [
        {"node_id": "a", "chunk_count": 5},
    ]
    store = ChunkShareStore(supabase=fake_supabase, ttl_seconds=0.05)
    import asyncio
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    time.sleep(0.1)
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    assert fake_supabase.rpc.call_count == 2, "read after ttl should re-fetch"


# iter-09 RES-2: class-conditional chunk-share gate + ratio-to-median magnet detection.
from website.features.rag_pipeline.retrieval.chunk_share import should_apply_chunk_share
from website.features.rag_pipeline.types import QueryClass


KM_COUNTS = {
    "yt-effective-public-speakin": 16,
    "yt-steve-jobs-2005-stanford": 13,
    "nl-the-pragmatic-engineer-t": 10,
    "yt-programming-workflow-is": 6,
    "web-transformative-tools-for": 6,
    "yt-matt-walker-sleep-depriv": 3,
    "gh-zk-org-zk": 2,
}


def test_lookup_class_skips_damp(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    apply, _reason = should_apply_chunk_share(QueryClass.LOOKUP, KM_COUNTS)
    assert apply is False


def test_thematic_with_outlier_applies_damp(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    monkeypatch.setenv("RAG_CHUNK_SHARE_MAGNET_RATIO", "2.0")
    apply, _reason = should_apply_chunk_share(QueryClass.THEMATIC, KM_COUNTS)
    assert apply is True


def test_thematic_uniform_distribution_no_damp(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    monkeypatch.setenv("RAG_CHUNK_SHARE_MAGNET_RATIO", "2.0")
    uniform = {f"n{i}": 5 for i in range(7)}
    apply, _reason = should_apply_chunk_share(QueryClass.THEMATIC, uniform)
    assert apply is False


def test_cold_start_kasten_skips_damp(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    apply, _reason = should_apply_chunk_share(QueryClass.THEMATIC, {"a": 16, "b": 1})
    assert apply is False


def test_multi_hop_with_outlier_applies_damp(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    apply, _reason = should_apply_chunk_share(QueryClass.MULTI_HOP, KM_COUNTS)
    assert apply is True


def test_vague_class_skips_damp_even_with_outlier(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true")
    apply, _reason = should_apply_chunk_share(QueryClass.VAGUE, KM_COUNTS)
    assert apply is False


def test_class_gate_disabled_falls_back_to_iter08_behaviour(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "false")
    apply, _reason = should_apply_chunk_share(QueryClass.LOOKUP, KM_COUNTS)
    assert apply is True
