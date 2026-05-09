"""Tests for usage-edge weight bonus in LocalizedPageRankScorer (T24).

LEGACY v1: these tests mock the retired `public.kg_usage_edges_agg` MV read.
Phase 2.3 of the website-features-v2 purge replaced that read with the v2 RPC
`rag.search_signal_weights` — the v2 surface lives in
``tests/unit/rag_pipeline/test_graph_score_v2.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "v1 graph_score usage-edge surface retired in Phase 2.3; "
        "replaced by tests/unit/rag_pipeline/test_graph_score_v2.py"
    )
)

from website.features.rag_pipeline.retrieval.graph_score import LocalizedPageRankScorer  # noqa: E402
from website.features.rag_pipeline.types import ChunkKind, QueryClass, RetrievalCandidate, SourceType  # noqa: E402


def _candidate(node_id: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_id=None,
        chunk_idx=0,
        name=node_id,
        source_type=SourceType.WEB,
        url=f"https://example.com/{node_id}",
        content="content",
        rrf_score=0.5,
    )


class _UsageEdgeQuery:
    """Stub for the chained .table().select().eq().eq().eq().execute() call."""

    def __init__(self, weights_by_target: dict[str, list[float]]):
        self._weights_by_target = weights_by_target
        self._target: str | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        if col == "target_node_id":
            self._target = val
        return self

    def execute(self):
        rows = [{"weight": w} for w in self._weights_by_target.get(self._target or "", [])]
        return SimpleNamespace(data=rows)


class _SupabaseStub:
    """Stub Supabase client supporting both the rpc() PageRank call and table() usage-edge call."""

    def __init__(self, edges, weights_by_target=None, table_raises: bool = False):
        self._edges = edges
        self._weights_by_target = weights_by_target or {}
        self._table_raises = table_raises

    def rpc(self, name, payload):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self._edges))

    def table(self, name):
        if self._table_raises:
            raise RuntimeError("simulated MV missing")
        assert name == "kg_usage_edges_agg"
        return _UsageEdgeQuery(self._weights_by_target)


def _baseline_scores(supabase_edges, candidates):
    """Run scorer with no query_class to capture baseline PageRank-only scores."""
    return supabase_edges, [c.graph_score for c in candidates]


@pytest.mark.asyncio
async def test_usage_edge_weight_boosts_score_when_query_class_supplied() -> None:
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    # Baseline: no query_class -> no bonus
    baseline_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_SupabaseStub(edges)).score(
        user_id=uuid4(), candidates=baseline_candidates
    )
    baseline = {c.node_id: c.graph_score for c in baseline_candidates}

    # With query_class + usage-edge weight on node-3 -> node-3 should be boosted above baseline
    boosted_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    supa = _SupabaseStub(edges, weights_by_target={"node-3": [10.0, 8.0]})
    await LocalizedPageRankScorer(supabase=supa).score(
        user_id=uuid4(),
        candidates=boosted_candidates,
        query_class=QueryClass.MULTI_HOP,
    )
    boosted = {c.node_id: c.graph_score for c in boosted_candidates}

    assert boosted["node-3"] > baseline["node-3"], (
        f"expected usage-edge bonus to lift node-3, got baseline={baseline['node-3']} "
        f"boosted={boosted['node-3']}"
    )


@pytest.mark.asyncio
async def test_no_bonus_when_no_usage_weights() -> None:
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    baseline_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_SupabaseStub(edges)).score(
        user_id=uuid4(), candidates=baseline_candidates
    )
    baseline = {c.node_id: c.graph_score for c in baseline_candidates}

    # query_class supplied but MV returns no rows -> bonus is sigmoid(0)-0.05 = 0.0
    new_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    supa = _SupabaseStub(edges, weights_by_target={})
    await LocalizedPageRankScorer(supabase=supa).score(
        user_id=uuid4(),
        candidates=new_candidates,
        query_class=QueryClass.LOOKUP,
    )
    after = {c.node_id: c.graph_score for c in new_candidates}

    for node_id in baseline:
        assert after[node_id] == pytest.approx(baseline[node_id]), (
            f"unexpected change at {node_id}: {baseline[node_id]} -> {after[node_id]}"
        )


@pytest.mark.asyncio
async def test_falls_back_gracefully_when_mv_missing() -> None:
    """If the MV doesn't exist (cold staging), .table() raises -> no crash, baseline preserved."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    baseline_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_SupabaseStub(edges)).score(
        user_id=uuid4(), candidates=baseline_candidates
    )
    baseline = {c.node_id: c.graph_score for c in baseline_candidates}

    new_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    supa = _SupabaseStub(edges, table_raises=True)
    # Must not raise
    await LocalizedPageRankScorer(supabase=supa).score(
        user_id=uuid4(),
        candidates=new_candidates,
        query_class=QueryClass.MULTI_HOP,
    )
    after = {c.node_id: c.graph_score for c in new_candidates}

    for node_id in baseline:
        assert after[node_id] == pytest.approx(baseline[node_id])


@pytest.mark.asyncio
async def test_no_query_class_skips_usage_edge_lookup() -> None:
    """Backward compat: when query_class not passed, table() is never called."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
    ]
    candidates = [_candidate("node-1"), _candidate("node-2")]
    # If table() were called it would raise; this proves we skip the lookup
    supa = _SupabaseStub(edges, table_raises=True)
    await LocalizedPageRankScorer(supabase=supa).score(
        user_id=uuid4(), candidates=candidates
    )
    assert candidates[0].graph_score is not None
    assert candidates[1].graph_score is not None
