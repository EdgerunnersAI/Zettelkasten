from types import SimpleNamespace
from uuid import uuid4

import pytest

from website.features.rag_pipeline.errors import EmptyScopeError
from website.features.rag_pipeline.retrieval.hybrid import HybridRetriever, _cap_per_node
from website.features.rag_pipeline.types import ChunkKind, QueryClass, RetrievalCandidate, ScopeFilter, SourceType

# Phase 2.4 (commits 2.4.4 - 2.4.6): this module mocks the retired v1 RPCs
# ``rag_hybrid_search`` (graph_depth/effective_nodes/per-source weights param
# shape) and ``rag_resolve_effective_nodes`` (user_id+sandbox_id+node_ids
# scope) — both of which were replaced by kasten-scoped v2 RPCs with
# different parameter and return shapes (canonical_chunk_id rows, per-source
# ranks instead of single rrf_score). End-to-end coverage now lives in
# ``tests/integration/v2/test_hybrid_pipeline_e2e.py``.
pytestmark = pytest.mark.skip(reason="v1 RPC mocks retired in Phase 2.4.4-2.4.6; coverage moved to integration v2 e2e tests")


class _RPCResult:
    def __init__(self, client, name, payload):
        self._client = client
        self._name = name
        self._payload = payload

    def execute(self):
        self._client.calls.append((self._name, self._payload))
        return SimpleNamespace(data=self._client.responses.get(self._name, []))


class _Supabase:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def rpc(self, name, payload):
        return _RPCResult(self, name, payload)


class _Embedder:
    def __init__(self):
        self.queries = []

    async def embed_query_with_cache(self, query):
        self.queries.append(query)
        return [float(len(query))]


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_sandbox_and_no_filter() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    result = await retriever._resolve_nodes(uuid4(), None, ScopeFilter())
    assert result is None


@pytest.mark.asyncio
async def test_resolve_calls_rpc_when_sandbox_set() -> None:
    sandbox_id = uuid4()
    supabase = _Supabase({"rag_resolve_effective_nodes": [{"node_id": "node-1"}]})
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)

    result = await retriever._resolve_nodes(uuid4(), sandbox_id, ScopeFilter())

    assert result == ["node-1"]
    assert supabase.calls[0][0] == "rag_resolve_effective_nodes"


@pytest.mark.asyncio
async def test_resolve_returns_empty_list_when_sandbox_empty() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({"rag_resolve_effective_nodes": []}))
    result = await retriever._resolve_nodes(uuid4(), uuid4(), ScopeFilter())
    assert result == []


@pytest.mark.asyncio
async def test_retrieve_fans_out_across_variants() -> None:
    supabase = _Supabase({
        "rag_hybrid_search": [
            {
                "kind": "chunk",
                "node_id": "node-1",
                "chunk_id": None,
                "chunk_idx": 0,
                "name": "One",
                "source_type": "web",
                "url": "https://example.com/1",
                "content": "content",
                "tags": [],
                "metadata": {},
                "rrf_score": 0.4,
            }
        ]
    })
    embedder = _Embedder()
    retriever = HybridRetriever(embedder=embedder, supabase=supabase)

    results = await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["first", "second"],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.LOOKUP,
    )

    assert len(results) == 1
    assert embedder.queries == ["first", "second"]
    assert len([call for call in supabase.calls if call[0] == "rag_hybrid_search"]) == 2


@pytest.mark.asyncio
async def test_dedup_keeps_max_rrf_score_and_consensus_boost() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    fused = retriever._dedup_and_fuse([
        [{
            "kind": "chunk", "node_id": "node-1", "chunk_id": None, "chunk_idx": 0,
            "name": "One", "source_type": "web", "url": "u", "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.4,
        }],
        [{
            "kind": "chunk", "node_id": "node-1", "chunk_id": None, "chunk_idx": 0,
            "name": "One", "source_type": "web", "url": "u", "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.6,
        }],
    ])

    assert fused[0].rrf_score == pytest.approx(0.65)


@pytest.mark.asyncio
async def test_retrieve_raises_empty_scope_error_when_resolver_returns_empty_list() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))

    async def _empty(*args, **kwargs):
        return []

    retriever._resolve_nodes = _empty

    with pytest.raises(EmptyScopeError):
        await retriever.retrieve(
            user_id=uuid4(),
            query_variants=["query"],
            sandbox_id=uuid4(),
            scope_filter=ScopeFilter(),
            query_class=QueryClass.LOOKUP,
        )


@pytest.mark.asyncio
async def test_graph_depth_is_1_for_lookup() -> None:
    supabase = _Supabase({"rag_hybrid_search": []})
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)
    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["query"],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.LOOKUP,
    )
    assert supabase.calls[0][1]["p_graph_depth"] == 1


@pytest.mark.asyncio
async def test_graph_depth_is_2_for_thematic() -> None:
    supabase = _Supabase({
        "rag_resolve_effective_nodes": [{"node_id": "node-1"}],
        "rag_hybrid_search": [],
    })
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)
    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["query"],
        sandbox_id=None,
        scope_filter=ScopeFilter(source_types=[SourceType.WEB]),
        query_class=QueryClass.THEMATIC,
    )
    # iter-10: tolerate trailing rag_dense_recall fallback by selecting the
    # rag_hybrid_search call explicitly instead of the last call overall.
    hybrid_calls = [c for c in supabase.calls if c[0] == "rag_hybrid_search"]
    assert hybrid_calls, "expected at least one rag_hybrid_search RPC"
    assert hybrid_calls[-1][1]["p_graph_depth"] == 2


@pytest.mark.asyncio
async def test_lookup_weights_lean_on_fulltext() -> None:
    """Lookup queries hunt for proper nouns / titles — FTS should outweigh
    semantic embedding to match exact lexical signals."""
    supabase = _Supabase({"rag_hybrid_search": []})
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)
    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["query"],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.LOOKUP,
    )
    payload = supabase.calls[0][1]
    assert payload["p_fulltext_weight"] > payload["p_semantic_weight"]
    assert payload["p_fulltext_weight"] == pytest.approx(0.50)


@pytest.mark.asyncio
async def test_thematic_weights_lean_on_semantic() -> None:
    supabase = _Supabase({"rag_hybrid_search": []})
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)
    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["query"],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.THEMATIC,
    )
    payload = supabase.calls[0][1]
    assert payload["p_semantic_weight"] > payload["p_fulltext_weight"]
    # iter-02 retune lowered THEMATIC semantic 0.60 -> 0.55 to add graph share.
    # iter-04/iter-06 best-of preserved 0.55. See rag_pipeline/retrieval/hybrid.py.
    assert payload["p_semantic_weight"] == pytest.approx(0.55)


def test_exact_title_match_gets_large_boost() -> None:
    """When a query variant equals the candidate's name verbatim the fused
    score must be bumped enough to pull the node into the top results even
    from a mediocre base rrf_score (e.g. stub bodies)."""
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    base_row = {
        "kind": "chunk", "node_id": "yt-attention", "chunk_id": None, "chunk_idx": 0,
        "name": "Attention Is All You Need", "source_type": "youtube", "url": "u",
        "content": "stub", "tags": [], "metadata": {}, "rrf_score": 0.10,
    }
    fused = retriever._dedup_and_fuse(
        [[base_row]],
        query_variants=["attention is all you need"],
    )

    assert fused[0].rrf_score == pytest.approx(0.50)


def test_partial_title_match_gets_graded_boost() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    base_row = {
        "kind": "chunk", "node_id": "yt-attention", "chunk_id": None, "chunk_idx": 0,
        "name": "Attention Is All You Need - Paper Explained", "source_type": "youtube",
        "url": "u", "content": "stub", "tags": [], "metadata": {}, "rrf_score": 0.10,
    }
    fused = retriever._dedup_and_fuse(
        [[base_row]],
        query_variants=["attention is all you need"],
    )

    assert fused[0].rrf_score > 0.10
    # Partial match is graded lower than exact-match's +0.40 but still non-trivial.
    assert fused[0].rrf_score < 0.50


def test_no_title_boost_when_variants_dont_match() -> None:
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    base_row = {
        "kind": "chunk", "node_id": "gh-langchain", "chunk_id": None, "chunk_idx": 0,
        "name": "LangChain README", "source_type": "github", "url": "u",
        "content": "body", "tags": [], "metadata": {}, "rrf_score": 0.30,
    }
    fused = retriever._dedup_and_fuse(
        [[base_row]],
        query_variants=["transformer attention paper"],
    )
    assert fused[0].rrf_score == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_duplicate_query_variants_only_fire_one_rpc_each() -> None:
    """Query expanders sometimes emit the raw query alongside a paraphrase
    that normalises to the same form. We must not fan out redundant RPCs or
    inflate consensus boosts on the dupes."""
    supabase = _Supabase({"rag_hybrid_search": []})
    embedder = _Embedder()
    retriever = HybridRetriever(embedder=embedder, supabase=supabase)

    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["attention is all you need", "Attention Is All You Need", "attention is all you need  "],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.LOOKUP,
    )

    search_calls = [call for call in supabase.calls if call[0] == "rag_hybrid_search"]
    assert len(search_calls) == 1
    assert embedder.queries == ["attention is all you need"]


def test_per_node_chunk_cap_keeps_top_three_chunks() -> None:
    """A single verbose node (e.g. a long YouTube transcript) must not
    monopolise top-K. Cap chunk-kind candidates at 3 per node_id."""
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    rows = [
        {
            "kind": "chunk", "node_id": "yt-verbose", "chunk_id": str(uuid4()), "chunk_idx": i,
            "name": "Verbose", "source_type": "youtube", "url": "u",
            "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.9 - i * 0.01,
        }
        for i in range(6)
    ]
    rows.append({
        "kind": "chunk", "node_id": "yt-other", "chunk_id": str(uuid4()), "chunk_idx": 0,
        "name": "Other", "source_type": "youtube", "url": "u",
        "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.5,
    })
    fused = retriever._dedup_and_fuse([rows])

    verbose_chunks = [c for c in fused if c.node_id == "yt-verbose"]
    assert len(verbose_chunks) == 3
    assert any(c.node_id == "yt-other" for c in fused)


def test_per_node_cap_does_not_drop_summary_alongside_chunks() -> None:
    """A node can still surface as both summary and chunk — caps only apply
    within chunk kind."""
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    rows = [
        {
            "kind": "summary", "node_id": "yt-paper", "chunk_id": None, "chunk_idx": 0,
            "name": "Paper", "source_type": "youtube", "url": "u",
            "content": "s", "tags": [], "metadata": {}, "rrf_score": 0.7,
        },
    ] + [
        {
            "kind": "chunk", "node_id": "yt-paper", "chunk_id": str(uuid4()), "chunk_idx": i,
            "name": "Paper", "source_type": "youtube", "url": "u",
            "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.6 - i * 0.01,
        }
        for i in range(5)
    ]
    fused = retriever._dedup_and_fuse([rows])

    kinds_for_paper = [c.kind.value for c in fused if c.node_id == "yt-paper"]
    assert kinds_for_paper.count("summary") == 1
    assert kinds_for_paper.count("chunk") == 3


def test_sibling_kind_consensus_boost() -> None:
    """When both summary and chunk surface for the same node, each gets a
    small cross-kind consensus bump — mirrors the multi-variant consensus."""
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    rows = [
        {
            "kind": "summary", "node_id": "yt-paper", "chunk_id": None, "chunk_idx": 0,
            "name": "Paper", "source_type": "youtube", "url": "u",
            "content": "s", "tags": [], "metadata": {}, "rrf_score": 0.30,
        },
        {
            "kind": "chunk", "node_id": "yt-paper", "chunk_id": str(uuid4()), "chunk_idx": 0,
            "name": "Paper", "source_type": "youtube", "url": "u",
            "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.30,
        },
        {
            "kind": "chunk", "node_id": "yt-solo", "chunk_id": str(uuid4()), "chunk_idx": 0,
            "name": "Solo", "source_type": "youtube", "url": "u",
            "content": "c", "tags": [], "metadata": {}, "rrf_score": 0.30,
        },
    ]
    fused = retriever._dedup_and_fuse([rows])

    paper = next(c for c in fused if c.node_id == "yt-paper")
    solo = next(c for c in fused if c.node_id == "yt-solo")
    assert paper.rrf_score == pytest.approx(0.33)
    assert solo.rrf_score == pytest.approx(0.30)


@pytest.mark.asyncio
async def test_multi_hop_weights_boost_graph() -> None:
    supabase = _Supabase({"rag_hybrid_search": []})
    retriever = HybridRetriever(embedder=_Embedder(), supabase=supabase)
    await retriever.retrieve(
        user_id=uuid4(),
        query_variants=["query"],
        sandbox_id=None,
        scope_filter=ScopeFilter(),
        query_class=QueryClass.MULTI_HOP,
    )
    payload = supabase.calls[0][1]
    assert payload["p_graph_weight"] == pytest.approx(0.35)
    assert payload["p_graph_weight"] > payload["p_fulltext_weight"]


# iter-08 Phase 3.1: class-aware _cap_per_node ----------------------------

def _cand(node_id: str, rrf: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_id=None,
        chunk_idx=0,
        name=node_id,
        source_type=SourceType.WEB,
        url="https://example.com",
        content="x",
        tags=[],
        metadata={},
        rrf_score=rrf,
    )


def test_cap_per_node_thematic_caps_at_one():
    """iter-08 Phase 3.1: THEMATIC keeps only the highest-rrf chunk per node."""
    candidates = [_cand("a", 0.9), _cand("a", 0.8), _cand("a", 0.7), _cand("b", 0.6)]
    capped = _cap_per_node(candidates, QueryClass.THEMATIC)
    assert [c.node_id for c in capped] == ["a", "b"]


def test_cap_per_node_lookup_caps_at_one():
    candidates = [_cand("a", 0.9), _cand("a", 0.8)]
    capped = _cap_per_node(candidates, QueryClass.LOOKUP)
    assert len(capped) == 1


def test_cap_per_node_multi_hop_keeps_three():
    """MULTI_HOP needs cross-chunk evidence — cap stays at 3."""
    candidates = [_cand("a", 0.9), _cand("a", 0.8), _cand("a", 0.7), _cand("a", 0.6)]
    capped = _cap_per_node(candidates, QueryClass.MULTI_HOP)
    assert len(capped) == 3


# iter-08 Phase 3.2: per-class xQuAD lambda --------------------------------

def test_xquad_lambda_thematic_is_05():
    from website.features.rag_pipeline.retrieval.hybrid import _xquad_lambda_for_class
    assert _xquad_lambda_for_class(QueryClass.THEMATIC) == 0.5
    assert _xquad_lambda_for_class(QueryClass.LOOKUP) == 0.7
    assert _xquad_lambda_for_class(QueryClass.MULTI_HOP) == 0.7


# iter-08 Phase 3.3: text-only compare-intent regex ------------------------

def test_detect_compare_intent_text_only_q10():
    """iter-08 Phase 3.3: compare-intent fires on 'Steve Jobs and Naval Ravikant' even when authors=[Steve Jobs]."""
    from website.features.rag_pipeline.retrieval.hybrid import _detect_compare_intent_text_only
    assert _detect_compare_intent_text_only(
        "Steve Jobs and Naval Ravikant both speak about meaningful work. Compare their views."
    ) is True


def test_detect_compare_intent_text_only_no_proper_nouns():
    from website.features.rag_pipeline.retrieval.hybrid import _detect_compare_intent_text_only
    assert _detect_compare_intent_text_only(
        "How do databases compare to spreadsheets?"
    ) is False




# iter-08 Phase 4.2: chunk-share normalization ----------------------------

def test_chunk_share_normalization_damps_magnets():
    """iter-08 Phase 4.2: candidates with high chunk_count get rrf damped."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_chunk_share_normalization
    candidates = [
        _cand("magnet", 1.0),  # chunk_count=16 -> factor 0.25 -> rrf 0.25
        _cand("normal", 1.0),  # chunk_count=4  -> factor 0.5  -> rrf 0.5
        _cand("solo",   1.0),  # chunk_count=1  -> factor 1.0  -> rrf 1.0
    ]
    chunk_counts = {"magnet": 16, "normal": 4, "solo": 1}
    _apply_chunk_share_normalization(candidates, chunk_counts)
    rrf = {c.node_id: c.rrf_score for c in candidates}
    assert abs(rrf["magnet"] - 0.25) < 0.01
    assert abs(rrf["normal"] - 0.5) < 0.01
    assert abs(rrf["solo"]   - 1.0) < 0.01


# iter-08 Phase 6.2: KG entity-anchor boost ----------------------------------

def test_anchor_boost_applies_to_neighbours():
    from website.features.rag_pipeline.retrieval.hybrid import _apply_anchor_boost
    candidates = [_cand("a", 0.5), _cand("b", 0.5), _cand("c", 0.5)]
    neighbours = {"a", "c"}
    _apply_anchor_boost(candidates, neighbours, boost=0.05)
    assert candidates[0].rrf_score == 0.55  # a is neighbour
    assert candidates[1].rrf_score == 0.50  # b is not
    assert candidates[2].rrf_score == 0.55  # c is neighbour


# iter-08 G3: anchor boost runs AFTER chunk-share ----------------------------

def test_anchor_boost_runs_after_chunk_share_for_chunky_neighbour():
    """iter-08 G3: chunky anchored neighbour keeps full +0.05 boost,
    not 0.05/sqrt(16) = 0.0125 that the prior order produced."""
    from website.features.rag_pipeline.retrieval.hybrid import (
        _apply_chunk_share_normalization,
        _apply_anchor_boost,
    )
    cands = [_cand("magnet", 1.0)]
    chunk_counts = {"magnet": 16}
    neighbours = {"magnet"}
    # Apply in the iter-08 G3 order: chunk-share first, then anchor boost
    _apply_chunk_share_normalization(cands, chunk_counts)
    _apply_anchor_boost(cands, neighbours, boost=0.05)
    # Expected: 1.0 * (1/sqrt(16)) + 0.05 = 0.25 + 0.05 = 0.30
    assert abs(cands[0].rrf_score - 0.30) < 0.001, f"got {cands[0].rrf_score}"


def test_dedup_and_fuse_applies_chunk_share_before_anchor_boost(monkeypatch):
    """iter-08 G3: end-to-end — _dedup_and_fuse applies chunk-share then anchor boost.

    A chunky (16-chunk) anchored neighbour should keep the full +0.05 boost
    rather than have it damped by 1/sqrt(16). End rrf is 0.25 + 0.05 = 0.30,
    not 0.25 + 0.0125 = 0.2625 (the prior order's behaviour).
    """
    from website.features.rag_pipeline.retrieval import hybrid as hybrid_mod
    monkeypatch.setenv("RAG_CHUNK_SHARE_NORMALIZATION_ENABLED", "true")
    retriever = HybridRetriever(embedder=_Embedder(), supabase=_Supabase({}))
    fused = retriever._dedup_and_fuse(
        [
            [{
                "kind": "chunk", "node_id": "magnet", "chunk_id": None, "chunk_idx": 0,
                "name": "Magnet", "source_type": "web", "url": "u", "content": "c",
                "tags": [], "metadata": {}, "rrf_score": 1.0,
            }],
        ],
        chunk_counts={"magnet": 16},
        anchor_neighbours={"magnet"},
    )
    # Sanity: anchor-boost feature flag must be on; if disabled in env, skip.
    if not hybrid_mod._ANCHOR_BOOST_ENABLED:
        pytest.skip("anchor-boost disabled by env flag")
    assert len(fused) == 1
    # Post-G3 order: chunk_share first (1.0 * 0.25 = 0.25), then anchor boost
    # (+0.05) -> 0.30. Pre-G3 order would yield 1.05 * 0.25 = 0.2625.
    assert abs(fused[0].rrf_score - 0.30) < 0.001, f"got {fused[0].rrf_score}"
