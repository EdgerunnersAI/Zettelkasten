"""iter-10 P5: dense-only fallback when hybrid recall@K misses every kasten
member.

Triggers a single high-precision dense pass scoped to ALL kasten members so
q6/q7-shape recall failures still surface SOMETHING for rerank.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from website.features.rag_pipeline.retrieval.hybrid import HybridRetriever
from website.features.rag_pipeline.types import QueryClass, ScopeFilter

# Phase 2.4.4: this module mocks the retired v1 RPC ``rag_dense_recall`` and
# the v1 ``rag_hybrid_search`` invocation pattern. The v2 replacement is
# ``content.search_chunks_enriched_kasten`` (kasten-scoped, different param
# shape). Coverage moved to integration v2 tests.
pytestmark = pytest.mark.skip(reason="v1 RPC retired in Phase 2.4.4; coverage moved to integration v2 tests")


class _StubEmbedder:
    async def embed_query_with_cache(self, q: str) -> list[float]:
        return [0.0] * 768


def _build_supabase(rpc_responses: dict[str, object]) -> MagicMock:
    """Build a chained MagicMock that returns the per-RPC stub via name lookup."""
    sb = MagicMock()

    def _rpc(name: str, params: dict):
        node = MagicMock()
        node.execute = MagicMock(return_value=rpc_responses.get(name))
        return node

    sb.rpc = _rpc
    return sb


@pytest.mark.asyncio
async def test_fallback_fires_when_pool_empty_and_kasten_nonempty(monkeypatch):
    """When `rag_hybrid_search` returns [] for every variant AND
    effective_nodes is non-empty, a SECOND RPC call to `rag_dense_recall`
    must fire and its rows seed the candidate pool."""
    rpc_log: list[str] = []

    def _make_response(name: str, data):
        m = MagicMock()
        m.data = data
        rpc_log.append(name)
        return m

    sb = MagicMock()

    def _rpc(name: str, params: dict):
        node = MagicMock()
        if name == "rag_resolve_effective_nodes":
            node.execute = MagicMock(return_value=_make_response(name, [
                {"node_id": "n1"}, {"node_id": "n2"},
            ]))
        elif name == "rag_hybrid_search":
            node.execute = MagicMock(return_value=_make_response(name, []))
        elif name == "rag_dense_recall":
            node.execute = MagicMock(return_value=_make_response(name, [
                {
                    "kind": "chunk",
                    "node_id": "n1",
                    "chunk_id": "00000000-0000-0000-0000-000000000001",
                    "chunk_idx": 0,
                    "name": "n1",
                    "source_type": "web",
                    "url": "",
                    "content": "x",
                    "tags": [],
                    "rrf_score": 0.42,
                },
            ]))
        else:
            node.execute = MagicMock(return_value=_make_response(name, []))
        return node

    sb.rpc = _rpc

    retriever = HybridRetriever(embedder=_StubEmbedder(), supabase=sb)
    candidates = await retriever.retrieve(
        user_id=UUID("00000000-0000-0000-0000-000000000aaa"),
        sandbox_id=UUID("00000000-0000-0000-0000-000000000bbb"),
        scope_filter=ScopeFilter(),
        query_variants=["whatever"],
        query_class=QueryClass.THEMATIC,
        limit=5,
    )
    assert "rag_dense_recall" in rpc_log, f"fallback did not fire; rpc log={rpc_log}"
    assert candidates and candidates[0].node_id == "n1"


@pytest.mark.asyncio
async def test_fallback_skipped_when_pool_nonempty(monkeypatch):
    """Hybrid returns at least one row → fallback MUST NOT fire."""
    rpc_log: list[str] = []

    def _make_response(name: str, data):
        m = MagicMock()
        m.data = data
        rpc_log.append(name)
        return m

    sb = MagicMock()

    def _rpc(name: str, params: dict):
        node = MagicMock()
        if name == "rag_resolve_effective_nodes":
            node.execute = MagicMock(return_value=_make_response(name, [
                {"node_id": "n1"},
            ]))
        elif name == "rag_hybrid_search":
            node.execute = MagicMock(return_value=_make_response(name, [
                {
                    "kind": "chunk",
                    "node_id": "n1",
                    "chunk_id": "00000000-0000-0000-0000-000000000001",
                    "chunk_idx": 0,
                    "name": "n1",
                    "source_type": "web",
                    "url": "",
                    "content": "x",
                    "tags": [],
                    "rrf_score": 0.55,
                },
            ]))
        elif name == "rag_dense_recall":
            node.execute = MagicMock(return_value=_make_response(name, []))
        else:
            node.execute = MagicMock(return_value=_make_response(name, []))
        return node

    sb.rpc = _rpc

    retriever = HybridRetriever(embedder=_StubEmbedder(), supabase=sb)
    await retriever.retrieve(
        user_id=UUID("00000000-0000-0000-0000-000000000aaa"),
        sandbox_id=UUID("00000000-0000-0000-0000-000000000bbb"),
        scope_filter=ScopeFilter(),
        query_variants=["whatever"],
        query_class=QueryClass.LOOKUP,
        limit=5,
    )
    assert "rag_dense_recall" not in rpc_log, (
        f"fallback fired despite non-empty pool; rpc log={rpc_log}"
    )


@pytest.mark.asyncio
async def test_fallback_disabled_via_env(monkeypatch):
    monkeypatch.setenv("RAG_DENSE_FALLBACK_ENABLED", "false")
    # Re-import to pick up env change
    import importlib
    from website.features.rag_pipeline.retrieval import hybrid as hybrid_mod
    importlib.reload(hybrid_mod)

    rpc_log: list[str] = []
    sb = MagicMock()

    def _rpc(name: str, params: dict):
        node = MagicMock()
        rpc_log.append(name)
        if name == "rag_resolve_effective_nodes":
            node.execute = MagicMock(return_value=MagicMock(data=[{"node_id": "n1"}]))
        else:
            node.execute = MagicMock(return_value=MagicMock(data=[]))
        return node

    sb.rpc = _rpc

    retriever = hybrid_mod.HybridRetriever(embedder=_StubEmbedder(), supabase=sb)
    await retriever.retrieve(
        user_id=UUID("00000000-0000-0000-0000-000000000aaa"),
        sandbox_id=UUID("00000000-0000-0000-0000-000000000bbb"),
        scope_filter=ScopeFilter(),
        query_variants=["whatever"],
        query_class=QueryClass.LOOKUP,
        limit=5,
    )
    assert "rag_dense_recall" not in rpc_log, (
        "fallback fired with env disabled; rpc log=" + str(rpc_log)
    )
    # Restore default for downstream tests
    monkeypatch.setenv("RAG_DENSE_FALLBACK_ENABLED", "true")
    importlib.reload(hybrid_mod)
