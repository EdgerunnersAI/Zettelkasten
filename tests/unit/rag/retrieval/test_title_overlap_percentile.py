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


def test_pick_anchor_pin_returns_anchored_with_evidence():
    """Anchored candidate with title boost >= 0.05 is the chosen pin."""
    from website.features.rag_pipeline.retrieval.hybrid import _pick_anchor_pin
    cands = [
        _cand("a", 0.55, 0.60, title_boost=0.30),
        _cand("anchored", 0.10, 0.50, title_boost=0.10),
        _cand("b", 0.50, 0.55, title_boost=0.20),
    ]
    pin = _pick_anchor_pin(cands, anchor_neighbours={"anchored"}, evidence_floor=0.05)
    assert pin is not None
    assert pin.node_id == "anchored"


def test_pick_anchor_pin_returns_none_without_evidence():
    """Anchored but boost < evidence_floor → no pin (vanilla xQuAD path)."""
    from website.features.rag_pipeline.retrieval.hybrid import _pick_anchor_pin
    cands = [
        _cand("a", 0.55, 0.60, title_boost=0.30),
        _cand("anchored", 0.10, 0.50, title_boost=0.0),
    ]
    pin = _pick_anchor_pin(cands, anchor_neighbours={"anchored"}, evidence_floor=0.05)
    assert pin is None


def test_pick_anchor_pin_returns_none_when_anchor_not_in_pool():
    """Anchor resolved but candidate not retrieved → None."""
    from website.features.rag_pipeline.retrieval.hybrid import _pick_anchor_pin
    cands = [_cand("a", 0.55, 0.60, title_boost=0.30)]
    pin = _pick_anchor_pin(cands, anchor_neighbours={"missing"}, evidence_floor=0.05)
    assert pin is None


def test_pick_anchor_pin_picks_highest_rrf_among_qualifying():
    """When multiple anchors qualify, the highest rrf wins."""
    from website.features.rag_pipeline.retrieval.hybrid import _pick_anchor_pin
    cands = [
        _cand("a1", 0.10, 0.40, title_boost=0.10),
        _cand("a2", 0.10, 0.60, title_boost=0.20),
        _cand("a3", 0.10, 0.50, title_boost=0.15),
    ]
    pin = _pick_anchor_pin(cands, anchor_neighbours={"a1", "a2", "a3"}, evidence_floor=0.05)
    assert pin is not None
    assert pin.node_id == "a2"


def test_demote_factor_percentile_top_pool_gentle():
    """Magnet at the top percentile gets a gentle factor (~0.90)."""
    from website.features.rag_pipeline.retrieval.hybrid import _demote_factor_for_candidate
    pool = [0.10, 0.30, 0.50, 0.55, 0.60]
    top_cand = _cand("magnet", 0.60, 0.65)
    factor = _demote_factor_for_candidate(top_cand, pool)
    # base 0.60 is the max → percentile ≈ 1.0 → factor ≈ 0.90
    assert 0.85 <= factor <= 0.91


def test_demote_factor_percentile_bottom_firm():
    """Magnet near the bottom of the rrf pool gets a firmer factor than the top.

    base=0.05 is below pool min 0.10 → rank_above=0.0 →
    factor = 1.0 - 0.20*(1.0-0.0) = 0.80 (firmer than top-cand 0.90).
    """
    from website.features.rag_pipeline.retrieval.hybrid import _demote_factor_for_candidate
    pool = [0.10, 0.30, 0.50, 0.55, 0.60]
    bottom_cand = _cand("magnet", 0.05, 0.65)  # base 0.05 below pool
    factor = _demote_factor_for_candidate(bottom_cand, pool)
    # rank_above=0.0, slope=0.20 → factor = 0.80
    assert 0.79 <= factor <= 0.81


def test_demote_factor_clamped_in_range():
    """Factor always in [0.70, 0.90]."""
    from website.features.rag_pipeline.retrieval.hybrid import _demote_factor_for_candidate
    cand = _cand("x", 0.5, 0.5)
    pool_normal = [0.1, 0.3, 0.5]
    f = _demote_factor_for_candidate(cand, pool_normal)
    assert 0.70 <= f <= 0.90
