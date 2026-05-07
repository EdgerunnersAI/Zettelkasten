"""iter-12 Phase 5 / Task 9: Q5 percentile-based title-overlap exemption."""
import pytest


def _cand(node_id, base_rrf, final_rrf, title_boost=0.0):
    from website.features.rag_pipeline.types import RetrievalCandidate, SourceType, ChunkKind
    return RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_idx=0,
        name=node_id.replace("-", " ").title(),
        source_type=SourceType.WEB,
        url=f"https://example.com/{node_id}",
        content=f"content for {node_id}",
        metadata={
            "_base_rrf_score": base_rrf,
            "_title_overlap_boost": title_boost,
        },
        rrf_score=final_rrf,
    )


def test_percentile_helper_basic():
    from website.features.rag_pipeline.retrieval.hybrid import _percentile
    assert _percentile([], 75) == 0.0
    assert _percentile([0.5], 75) == 0.5
    # P75 of [0.0, 0.1, 0.2, 0.3, 0.4, 0.5] should be in (0.3, 0.5)
    p75 = _percentile([0.0, 0.1, 0.2, 0.3, 0.4, 0.5], 75)
    assert 0.3 < p75 < 0.5


def test_incidental_token_overlap_does_not_exempt_magnet():
    """The q5 forensic case: a magnet with boost 0.05 in a pool where
    siblings have 0.22-0.30 must NOT be exempted (P75 around 0.27 floor 0.10)."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    from website.features.rag_pipeline.types import QueryClass
    cands = [
        _cand("magnet", 0.10, 0.65, title_boost=0.05),
        _cand("a", 0.55, 0.60, title_boost=0.30),
        _cand("b", 0.50, 0.55, title_boost=0.25),
        _cand("c", 0.45, 0.50, title_boost=0.22),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # Magnet should be demoted; another candidate now has top score
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "magnet"


def test_earned_title_overlap_exempts():
    """A 0.40 verbatim-title boost in a pool of low-boost siblings is exempted."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    from website.features.rag_pipeline.types import QueryClass
    cands = [
        _cand("named", 0.10, 0.65, title_boost=0.40),
        _cand("a", 0.55, 0.60, title_boost=0.05),
        _cand("b", 0.50, 0.55, title_boost=0.0),
        _cand("c", 0.45, 0.50, title_boost=0.05),
    ]
    pre_score = cands[0].rrf_score
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    assert cands[0].rrf_score == pre_score, "earned title-overlap candidate must NOT be demoted"


def test_zero_boosts_never_exempt_no_op():
    """All boosts 0.0 → P75 = 0.0; floor fallback 0.10 binds. No exemption.
    The magnet (lowest base_rrf, highest final_rrf) gets demoted normally."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    from website.features.rag_pipeline.types import QueryClass
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("b", 0.55, 0.60),
        _cand("c", 0.50, 0.55),
        _cand("d", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "magnet"


def test_anchor_node_exempted_unconditionally():
    """Anchored candidates exempt regardless of percentile."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    from website.features.rag_pipeline.types import QueryClass
    cands = [
        _cand("anchored-magnet", 0.10, 0.65, title_boost=0.0),
        _cand("a", 0.55, 0.60, title_boost=0.30),
        _cand("b", 0.50, 0.55, title_boost=0.25),
        _cand("c", 0.45, 0.50, title_boost=0.22),
    ]
    pre_score = cands[0].rrf_score
    _apply_score_rank_demote(
        cands, query_class=QueryClass.THEMATIC, query_text="topic",
        anchor_nodes={"anchored-magnet"},
    )
    assert cands[0].rrf_score == pre_score, "anchored candidate must not demote"
