"""v2 unit tests for `graph_score.py` (Phase 2.3 — supabase_kg purge).

Verifies the refactored `LocalizedPageRankScorer`:

  1. delegates the usage-edge bonus to the v2 RPC `rag.search_signal_weights`
     via `RAGRepository.search_signal_weights` (NOT the retired
     `public.kg_usage_edges_agg` MV);
  2. preserves the decay-weight scoring math byte-for-byte
     (`0.10 / (1.0 + exp(-weight / 5.0)) - 0.05`);
  3. no longer imports from ``website.core.supabase_kg``.

Mocks follow the `_Client` / `_Schema` idiom from
`tests/unit/supabase_v2/test_repositories.py`.
"""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from website.features.rag_pipeline.retrieval import graph_score as graph_score_module
from website.features.rag_pipeline.retrieval.graph_score import (
    LocalizedPageRankScorer,
    _usage_weight_bonus,
)
from website.features.rag_pipeline.types import (
    ChunkKind,
    QueryClass,
    RetrievalCandidate,
    SourceType,
)


# ---------------------------------------------------------------------------
# v2 supabase-py client mocks (sync) — surface graph_score touches:
#   * client.rpc("rag_subgraph_for_pagerank", ...).execute()
#   * client.schema("rag").rpc("search_signal_weights", ...).execute()
# ---------------------------------------------------------------------------


class _Execute:
    def __init__(self, data, raise_exc: BaseException | None = None):
        self._data = data
        self._raise = raise_exc

    def execute(self):
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(data=self._data)


class _RagSchema:
    def __init__(self, calls, schema, signal_weight_rows, signal_raise):
        self.calls = calls
        self.schema = schema
        self._rows = signal_weight_rows
        self._raise = signal_raise

    def rpc(self, name, params):
        self.calls.append(("schema_rpc", self.schema, name, params))
        # Filter by p_target_chunk_ids so each per-target lookup gets only its
        # rows (matches the SQL `target = ANY (...)` filter and lets the
        # _usage_weight_bonus per-target call return a deterministic scalar).
        targets = set(params.get("p_target_chunk_ids") or [])
        filtered = [r for r in self._rows if str(r["target_canonical_chunk_id"]) in targets]
        return _Execute(filtered, self._raise)


class _Client:
    """Mimics the bits of `supabase.Client` LocalizedPageRankScorer touches."""

    def __init__(
        self,
        *,
        edges=None,
        signal_weight_rows=None,
        signal_raise: BaseException | None = None,
    ):
        self.calls: list = []
        self._edges = edges or []
        self._signal_rows = signal_weight_rows or []
        self._signal_raise = signal_raise

    # Unscoped rpc for the legacy public schema RPC `rag_subgraph_for_pagerank`.
    # Phase 2.3 brief is scoped to the usage-edge MV purge — the PageRank RPC
    # is not in the v2 schema yet, so this path is unchanged.
    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        assert name == "rag_subgraph_for_pagerank", (
            f"unexpected unscoped rpc({name!r}); v2 reads must go through schema('rag')"
        )
        return _Execute(self._edges)

    # v2 contract: usage-edge weights via schema('rag').rpc('search_signal_weights', ...)
    def schema(self, name):
        self.calls.append(("schema", name))
        return _RagSchema(self.calls, name, self._signal_rows, self._signal_raise)


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


# ---------------------------------------------------------------------------
# Static / file-level guarantees
# ---------------------------------------------------------------------------


def test_graph_score_module_does_not_import_supabase_kg():
    """File-level grep: `supabase_kg` and the legacy MV must not appear."""
    src_path = Path(graph_score_module.__file__)
    text = src_path.read_text(encoding="utf-8")
    assert "from website.core.supabase_kg" not in text, (
        "graph_score.py must not import from supabase_kg after v2 purge"
    )
    assert "kg_usage_edges_agg" not in text, (
        "graph_score.py must not reference the legacy v1 MV after v2 purge"
    )


# ---------------------------------------------------------------------------
# Decay-weight math snapshot — captured from the original v1 implementation
# (sigmoid: 0.10 / (1 + exp(-w / 5.0)) - 0.05). Byte-for-byte preservation
# requirement from Phase 2.3 brief.
# ---------------------------------------------------------------------------


def _expected_bonus(total_weight: float) -> float:
    return 0.10 / (1.0 + math.exp(-total_weight / 5.0)) - 0.05


@pytest.mark.parametrize(
    "weights,expected_total",
    [
        ([], 0.0),                  # empty rows → bonus == 0
        ([1.0], 1.0),
        ([2.5, 7.5], 10.0),
        ([10.0, 8.0], 18.0),
        ([-3.0, 1.5], -1.5),        # negative-weight rows still flow through
    ],
)
def test_usage_weight_bonus_math_byte_for_byte(weights, expected_total):
    """Decay-weight scoring math is preserved byte-for-byte against v1."""

    class _Repo:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def search_signal_weights(self, *, workspace_id, target_chunk_ids, query_class):
            self.calls.append((str(workspace_id), list(map(str, target_chunk_ids)), query_class))
            return self.rows

    rows = [
        {
            "source_canonical_chunk_id": str(uuid4()),
            "target_canonical_chunk_id": "chunk-T",
            "weight": w,
        }
        for w in weights
    ]
    repo = _Repo(rows)
    user = uuid4()

    bonus = _usage_weight_bonus(
        repo,
        user_id=user,
        target_node_id="chunk-T",
        query_class=QueryClass.MULTI_HOP,
    )

    assert bonus == pytest.approx(_expected_bonus(expected_total), abs=1e-12)
    # Single delegated call with the right shape.
    assert repo.calls == [(str(user), ["chunk-T"], "multi_hop")]


def test_usage_weight_bonus_returns_zero_on_repo_error():
    """Identity contract from v1: any failure → 0.0 bonus."""

    class _Repo:
        def search_signal_weights(self, *, workspace_id, target_chunk_ids, query_class):
            raise RuntimeError("simulated postgrest 5xx")

    bonus = _usage_weight_bonus(
        _Repo(),
        user_id=uuid4(),
        target_node_id="chunk-T",
        query_class=QueryClass.LOOKUP,
    )
    assert bonus == 0.0


def test_usage_weight_bonus_disabled_short_circuits(monkeypatch):
    """RAG_USAGE_EDGES_ENABLED=false → 0.0 bonus, no repo call."""
    monkeypatch.setattr(graph_score_module, "_USAGE_EDGES_ENABLED", False)

    class _Repo:
        def __init__(self):
            self.called = False

        def search_signal_weights(self, **_kw):
            self.called = True
            raise AssertionError("repo must NOT be called when usage edges disabled")

    repo = _Repo()
    bonus = _usage_weight_bonus(
        repo,
        user_id=uuid4(),
        target_node_id="chunk-T",
        query_class=QueryClass.MULTI_HOP,
    )
    assert bonus == 0.0
    assert repo.called is False


# ---------------------------------------------------------------------------
# Integration through LocalizedPageRankScorer.score(): new RPC must be wired
# instead of the legacy MV read.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_delegates_to_search_signal_weights_rpc():
    """Verify the public scoring path calls schema('rag').rpc('search_signal_weights', ...)."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    signal_rows = [
        # Decayed weight for target node-3 only.
        {
            "source_canonical_chunk_id": "node-1",
            "target_canonical_chunk_id": "node-3",
            "weight": 10.0,
        },
        {
            "source_canonical_chunk_id": "node-2",
            "target_canonical_chunk_id": "node-3",
            "weight": 8.0,
        },
    ]
    fake = _Client(edges=edges, signal_weight_rows=signal_rows)

    # Baseline: no query_class → no signal-weight RPC must be called.
    baseline_candidates = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    scorer_baseline = LocalizedPageRankScorer(supabase=fake)
    await scorer_baseline.score(user_id=uuid4(), candidates=baseline_candidates)
    schema_calls_baseline = [c for c in fake.calls if c[0] == "schema_rpc"]
    assert schema_calls_baseline == [], (
        f"no signal-weight RPC expected without query_class, got {schema_calls_baseline!r}"
    )
    baseline_scores = {c.node_id: c.graph_score for c in baseline_candidates}

    # Boosted: query_class supplied → schema('rag').rpc('search_signal_weights', ...)
    fake2 = _Client(edges=edges, signal_weight_rows=signal_rows)
    boosted = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    workspace_id = uuid4()
    await LocalizedPageRankScorer(supabase=fake2).score(
        user_id=workspace_id,
        candidates=boosted,
        query_class=QueryClass.MULTI_HOP,
    )
    schema_calls = [c for c in fake2.calls if c[0] == "schema_rpc"]
    assert schema_calls, "expected schema('rag').rpc(search_signal_weights) under query_class"
    # Every signal-weight RPC must hit the v2 schema + name + workspace_id binding.
    for kind, schema_name, rpc_name, params in schema_calls:
        assert schema_name == "rag"
        assert rpc_name == "search_signal_weights"
        assert params["p_workspace_id"] == str(workspace_id)
        assert params["p_query_class"] == "multi_hop"
        assert isinstance(params["p_target_chunk_ids"], list)
    # Boost lifts node-3 above its PageRank-only baseline (sum weight 18 → +ve sigmoid bonus).
    boosted_scores = {c.node_id: c.graph_score for c in boosted}
    assert boosted_scores["node-3"] > baseline_scores["node-3"]
    assert boosted_scores["node-3"] - baseline_scores["node-3"] == pytest.approx(
        _expected_bonus(18.0), abs=1e-9,
    )


@pytest.mark.asyncio
async def test_score_with_no_signal_rows_preserves_baseline():
    """Empty RPC rows → bonus == sigmoid(0) - 0.05 == 0; baseline unchanged."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    baseline = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_Client(edges=edges)).score(
        user_id=uuid4(), candidates=baseline,
    )
    base_scores = {c.node_id: c.graph_score for c in baseline}

    new = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_Client(edges=edges, signal_weight_rows=[])).score(
        user_id=uuid4(),
        candidates=new,
        query_class=QueryClass.LOOKUP,
    )
    new_scores = {c.node_id: c.graph_score for c in new}
    for node_id in base_scores:
        assert new_scores[node_id] == pytest.approx(base_scores[node_id])


@pytest.mark.asyncio
async def test_score_falls_back_gracefully_when_rpc_raises():
    """v1 identity preserved: RPC raises (e.g. PostgREST 5xx) → no crash, baseline preserved."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
        {"source_node_id": "node-2", "target_node_id": "node-3", "weight": 1.0},
    ]
    baseline = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    await LocalizedPageRankScorer(supabase=_Client(edges=edges)).score(
        user_id=uuid4(), candidates=baseline,
    )
    base_scores = {c.node_id: c.graph_score for c in baseline}

    boom = RuntimeError("simulated postgrest 5xx")
    new = [_candidate("node-1"), _candidate("node-2"), _candidate("node-3")]
    fake = _Client(edges=edges, signal_raise=boom)
    # Must not raise.
    await LocalizedPageRankScorer(supabase=fake).score(
        user_id=uuid4(),
        candidates=new,
        query_class=QueryClass.MULTI_HOP,
    )
    new_scores = {c.node_id: c.graph_score for c in new}
    for node_id in base_scores:
        assert new_scores[node_id] == pytest.approx(base_scores[node_id])


@pytest.mark.asyncio
async def test_score_no_query_class_skips_signal_weight_lookup():
    """Backward compat: when query_class not passed, no schema('rag').rpc() call is made."""
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
    ]
    candidates = [_candidate("node-1"), _candidate("node-2")]
    fake = _Client(
        edges=edges,
        signal_raise=RuntimeError("must not be called when query_class is None"),
    )
    # If schema('rag').rpc were called it would raise — proves we skip the lookup.
    await LocalizedPageRankScorer(supabase=fake).score(
        user_id=uuid4(), candidates=candidates,
    )
    assert candidates[0].graph_score is not None
    assert candidates[1].graph_score is not None
    assert [c for c in fake.calls if c[0] == "schema_rpc"] == []


@pytest.mark.asyncio
async def test_score_caches_per_node_signal_weight_lookup():
    """Bonus cache: multiple chunk-candidates sharing a node_id → 1 RPC per unique node_id."""
    # 3 candidates, 2 unique node_ids → at most 2 signal-weight RPC calls.
    edges = [
        {"source_node_id": "node-1", "target_node_id": "node-2", "weight": 1.0},
    ]
    candidates = [
        _candidate("node-1"),
        _candidate("node-1"),  # duplicate node — must hit cache, not RPC.
        _candidate("node-2"),
    ]
    fake = _Client(edges=edges, signal_weight_rows=[])
    await LocalizedPageRankScorer(supabase=fake).score(
        user_id=uuid4(),
        candidates=candidates,
        query_class=QueryClass.THEMATIC,
    )
    schema_rpc_calls = [c for c in fake.calls if c[0] == "schema_rpc"]
    # Two unique node_ids ("node-1", "node-2") → exactly two RPC dispatches.
    assert len(schema_rpc_calls) == 2, (
        f"expected 2 unique-node RPCs, got {len(schema_rpc_calls)}: {schema_rpc_calls!r}"
    )
