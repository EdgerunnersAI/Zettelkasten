"""iter-10 P3: score-rank-correlation magnet gate.

A node is a magnet if its top-1 ranking is disproportionate to its retrieval
percentile. We compute the percentile rank of each candidate's BASE rrf
(BEFORE all class boosts) and demote any candidate whose post-boost rank is
> 1 quartile higher than its base percentile in THEMATIC/STEP_BACK class.
"""
from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
from website.features.rag_pipeline.types import (
    ChunkKind,
    QueryClass,
    RetrievalCandidate,
    SourceType,
)


def _cand(node_id: str, base_rrf: float, final_rrf: float) -> RetrievalCandidate:
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
    c.metadata = {"_base_rrf_score": base_rrf}
    return c


def test_thematic_demotes_magnet_with_low_base_rrf():
    """Magnet narrowly led on final rrf; the 0.85 demote factor must reverse
    the order so a real thematic candidate wins top-1."""
    cands = [
        _cand("magnet-2chunk", 0.10, 0.65),  # narrow lead from a +0.55 boost
        _cand("real-thematic-a", 0.55, 0.60),
        _cand("real-thematic-b", 0.50, 0.55),
        _cand("real-thematic-c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="general topic")
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id != "magnet-2chunk"


def test_lookup_does_not_demote_magnet():
    """LOOKUP must NEVER apply the gate; proper-noun lookups legitimately
    boost a single high-relevance node to top-1."""
    cands = [
        _cand("magnet-2chunk", 0.10, 0.65),
        _cand("other-a", 0.55, 0.60),
        _cand("other-b", 0.50, 0.55),
        _cand("other-c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.LOOKUP, query_text="proper noun query")
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id == "magnet-2chunk"


def test_thematic_no_demote_when_no_disproportion():
    cands = [
        _cand("a", 0.80, 0.85),
        _cand("b", 0.70, 0.75),
        _cand("c", 0.60, 0.65),
        _cand("d", 0.50, 0.55),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id == "a"


def test_step_back_class_also_gated():
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("real-a", 0.55, 0.60),
        _cand("real-b", 0.50, 0.55),
        _cand("real-c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.STEP_BACK, query_text="step back")
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id != "magnet"


def test_vague_class_NOT_gated():
    """VAGUE has its own vague_low_entity gate; do not double-gate."""
    cands = [_cand("magnet", 0.10, 0.85)]
    _apply_score_rank_demote(cands, query_class=QueryClass.VAGUE, query_text="vague")
    assert cands[0].rrf_score == 0.85


def test_multi_hop_NOT_gated():
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("a", 0.55, 0.60),
        _cand("b", 0.50, 0.55),
        _cand("c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.MULTI_HOP, query_text="step back")
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id == "magnet"


def test_title_overlap_above_floor_is_exempted_iter11():
    """iter-11 Class A: superseded the iter-10 'title secondary demote on
    boost>=0.10' behaviour with an earned-exemption carve-out. ANY positive
    title overlap (including the iter-10 0.15 case) means the user named the
    zettel verbatim — that's signal, not a magnet to damp. Both the primary
    score-rank demote and the title secondary demote are skipped.
    """
    cands = [
        _cand("title-magnet", 0.30, 0.80),
        _cand("a", 0.32, 0.34),
        _cand("b", 0.31, 0.33),
        _cand("c", 0.30, 0.32),
    ]
    cands[0].metadata["_title_overlap_boost"] = 0.15
    before = cands[0].rrf_score
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    assert cands[0].rrf_score == before


def test_pool_too_small_skips_gate():
    """Pools <4 candidates have unstable rank percentile; skip gate."""
    cands = [_cand("magnet", 0.10, 0.85), _cand("other", 0.50, 0.55)]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    assert cands[0].rrf_score == 0.85  # unchanged


def test_anchored_candidate_skips_demote():
    """iter-11 Class A: a candidate whose node_id is in anchor_nodes (the
    resolved-entity set) MUST NOT be demoted by the score-rank gate, even
    if it scores as a statistical magnet. The gate damps ONLY 'unearned'
    magnets; entity-anchored candidates have earned the top-1 slot."""
    cands = [
        _cand("legit-magnet", 0.10, 0.65),
        _cand("real-a", 0.55, 0.60),
        _cand("real-b", 0.50, 0.55),
        _cand("real-c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(
        cands,
        query_class=QueryClass.THEMATIC,
        query_text="topic",
        anchor_nodes={"legit-magnet"},
    )
    # legit-magnet stays at 0.65 because it's anchored.
    assert cands[0].rrf_score == 0.65


def test_title_overlap_candidate_also_skips_score_rank_demote():
    """iter-12 Q5: replaces iter-11 binary > 0.0 with percentile threshold.
    A boost of 0.05 in an all-zero-boost pool: P75=0.0, floor 0.10 binds,
    so 0.05 < 0.10 threshold → NOT exempted. Incidental overlap no longer
    earns a free pass — only earned (above-P75) title overlap does."""
    cands = [
        _cand("named-magnet", 0.10, 0.65),
        _cand("real-a", 0.55, 0.60),
        _cand("real-b", 0.50, 0.55),
        _cand("real-c", 0.45, 0.50),
    ]
    cands[0].metadata["_title_overlap_boost"] = 0.05  # below the 0.10 floor
    _apply_score_rank_demote(
        cands,
        query_class=QueryClass.THEMATIC,
        query_text="topic",
        anchor_nodes=set(),
    )
    # 0.05 < floor 0.10 → demoted; another candidate now leads.
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id != "named-magnet"


def test_unanchored_magnet_still_gets_demoted():
    """Sanity: with anchor_nodes={} and no title boost, the gate still fires
    on the disproportional candidate (no behaviour change for the iter-10 case)."""
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("real-a", 0.55, 0.60),
        _cand("real-b", 0.50, 0.55),
        _cand("real-c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(
        cands,
        query_class=QueryClass.THEMATIC,
        query_text="topic",
        anchor_nodes=set(),
    )
    cands_sorted = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert cands_sorted[0].node_id != "magnet"
