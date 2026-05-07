"""iter-12 Class K3: confidence-gap bypass tests."""
import pytest
from website.features.rag_pipeline.types import (
    ChunkKind,
    QueryClass,
    RetrievalCandidate,
    SourceType,
)


def _cand(node_id, base_rrf, final_rrf, title_boost=0.0):
    """Build a minimal RetrievalCandidate that the gate accepts."""
    c = RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_idx=0,
        name=node_id,
        source_type=SourceType.WEB,
        url="",
        content="",
    )
    c.rrf_score = final_rrf
    c.metadata = {
        "_base_rrf_score": base_rrf,
        "_title_overlap_boost": title_boost,
    }
    return c


def test_top1_top2_gap_helper_basic():
    from website.features.rag_pipeline.retrieval.hybrid import _top1_top2_gap
    cands = [_cand("a", 0.10, 0.90), _cand("b", 0.55, 0.50), _cand("c", 0.45, 0.40)]
    gap = _top1_top2_gap(cands)
    assert gap is not None
    assert abs(gap - (0.90 / 0.50)) < 1e-9


def test_top1_top2_gap_undefined_for_singleton():
    from website.features.rag_pipeline.retrieval.hybrid import _top1_top2_gap
    assert _top1_top2_gap([]) is None
    assert _top1_top2_gap([_cand("only", 0.5, 0.5)]) is None


def test_top1_top2_gap_handles_zero_top2():
    """Tiny epsilon prevents division by zero; gap must be finite and very large."""
    from website.features.rag_pipeline.retrieval.hybrid import _top1_top2_gap
    cands = [_cand("a", 0.5, 0.5), _cand("b", 0.0, 0.0)]
    gap = _top1_top2_gap(cands)
    assert gap is not None and gap > 1e6


def test_clear_winner_skips_magnet_gate():
    """When top1/top2 >= 1.5, the magnet gate must early-out before demote logic."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    cands = [
        _cand("clear-winner", 0.10, 0.90),
        _cand("a", 0.55, 0.50),
        _cand("b", 0.50, 0.45),
        _cand("c", 0.45, 0.40),
    ]
    pre = [c.rrf_score for c in cands]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    post = [c.rrf_score for c in cands]
    # gap = 0.90 / 0.50 = 1.8 >= 1.5 -> bypass; nothing demoted
    assert pre == post


def test_close_competition_gate_can_fire():
    """Gap < 1.5 -> normal magnet-gate logic fires and demotes the magnet."""
    from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("a", 0.55, 0.60),
        _cand("b", 0.50, 0.55),
        _cand("c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # gap = 0.65 / 0.60 ~ 1.083 < 1.5; gate fires; magnet demoted below at least one
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "magnet"


def test_should_skip_retry_uses_confidence_gap():
    """_retry_gap_bypass_threshold() returns the 1.5 default."""
    from website.features.rag_pipeline.orchestrator import _retry_gap_bypass_threshold
    assert _retry_gap_bypass_threshold() == 1.5
