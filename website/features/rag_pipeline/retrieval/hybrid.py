"""Hybrid retrieval over Supabase RPCs."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import os

from website.features.rag_pipeline.errors import EmptyScopeError
from website.features.rag_pipeline.query.metadata import QueryMetadata
from website.features.rag_pipeline.retrieval._async_helpers import rpc_call
from website.features.rag_pipeline.query.router import _COMPARE_PATTERN
from website.features.rag_pipeline.retrieval.kasten_freq import (
    KastenFrequencyStore,
)
from website.features.rag_pipeline.retrieval.chunk_share import (
    ChunkShareStore,
    compute_chunk_share_penalty,
    should_apply_chunk_share,
)

# iter-07 Fix B: COMPARE-aware anti-magnet. When the rewritten query contains
# a compare/vs pattern AND ≥2 person entities, disable the kasten-frequency
# penalty so legitimate compare-targets aren't stripped (q10 fix:
# "Steve Jobs and Naval Ravikant" lost Steve Jobs to the magnet penalty).
_COMPARE_AWARE_ANTIMAGNET_ENABLED = os.environ.get(
    "RAG_COMPARE_AWARE_ANTIMAGNET_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")

# iter-08 Phase 6: KG entity-anchor boost. When the query carries author/entity
# metadata, resolve those names to anchor zettels and boost their 1-hop
# neighbours' rrf_score so anchored multi-hop intents pull related zettels.
_ANCHOR_BOOST_ENABLED = os.environ.get(
    "RAG_ANCHOR_BOOST_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
_ANCHOR_BOOST_AMOUNT = float(os.environ.get("RAG_ANCHOR_BOOST_AMOUNT", "0.05"))

# Phase 8.5.B-4: Kasten-scoped retrieval signal boost (raw-count formula, NOT bandit
# math — see locked plan + mem-vault decision zrUWPShYIYieiXSXi1uzh-Ml).
# Reads from rag.kasten_retrieval_edge_signals MV (Kasten-scope projection over
# rag.retrieval_feedback_events). Cold-start guard: skip when Kasten total events
# < threshold. Bandit posterior math (Beta-Bernoulli TS) is deferred per phase-
# transition criterion; the env knobs RAG_ANCHOR_BANDIT_* stay in code, gated OFF.
_KASTEN_SIGNAL_BOOST_ENABLED = os.environ.get(
    "RAG_KASTEN_SIGNAL_BOOST_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
_KASTEN_SIGNAL_BOOST_SCALE = float(
    os.environ.get("RAG_KASTEN_SIGNAL_BOOST_SCALE", "0.05")
)
_KASTEN_SIGNAL_COLD_START_THRESHOLD = int(
    os.environ.get("RAG_KASTEN_SIGNAL_COLD_START_THRESHOLD", "50")
)
# Bandit posterior math (Beta-Bernoulli with hierarchical shrinkage) — DISABLED.
# Flip ON only when phase-transition criterion fires (≥1k events per
# (query_class, kasten_archetype) cell + golden-set plateau + offline counterfactual
# replay shows ≥3% nDCG@10 lift + operator approval per protected-knob rule).
_BANDIT_POSTERIOR_ENABLED = os.environ.get(
    "RAG_ANCHOR_BANDIT_POSTERIOR_ENABLED", "false"
).lower() in ("true", "1", "yes", "on")

# iter-09 RES-7 / Q10: anchor-seed injection. Pulls best chunks for resolved
# anchor zettels into the candidate pool with a floor rrf_score so the cross-
# encoder can decide final rank rather than dropping anchors entirely when the
# main hybrid search misses them.
_ANCHOR_SEED_ENABLED = os.environ.get(
    "RAG_ANCHOR_SEED_INJECTION_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
# iter-12 T31 R4: static fallback (also used when bandit is disabled/cold).
# The live floor is now provided by the Thompson-sampling bandit in
# anchor_seed_bandit.sample_floor(); _ANCHOR_SEED_FLOOR_RRF is kept as
# the module-level fallback constant so log calls that reference it remain valid.
_ANCHOR_SEED_FLOOR_RRF = float(os.environ.get("RAG_ANCHOR_SEED_FLOOR_RRF", "0.30"))
# iter-10 P4 mitigations:
#   2. Min entity-length floor — short entities like "AI"/"ML" tag-collide.
#   3. Hard top-K cap on injected seeds (RPC LIMIT 8 is too generous).
_ANCHOR_SEED_MIN_ENTITY_LENGTH = int(
    os.environ.get("RAG_ANCHOR_SEED_MIN_ENTITY_LENGTH", "4")
)
_ANCHOR_SEED_TOP_K = int(os.environ.get("RAG_ANCHOR_SEED_TOP_K", "3"))

# iter-10 P5: dense-only kasten-scoped fallback when hybrid fan-out returns
# zero rows. Guarded by env flag; defensive last-resort path only.
_DENSE_FALLBACK_ENABLED = os.environ.get(
    "RAG_DENSE_FALLBACK_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")

# iter-10 P3 magnet-gate scalar knobs (the QueryClass tuple lives after the
# types import a few lines down).
# _SCORE_RANK_DEMOTE_FACTOR removed — replaced by _demote_factor_for_candidate (iter-12 T32)
_SCORE_RANK_DISPROP_QUARTILES = float(
    os.environ.get("RAG_SCORE_RANK_DISPROP_QUARTILES", "1.0")
)
_TITLE_OVERLAP_DEMOTE_FACTOR = float(
    os.environ.get("RAG_TITLE_OVERLAP_DEMOTE_FACTOR", "0.95")
)
_TITLE_OVERLAP_DEMOTE_FLOOR = float(
    os.environ.get("RAG_TITLE_OVERLAP_DEMOTE_FLOOR", "0.10")
)
# iter-12 Q5: percentile knobs for earned-title exemption threshold.
_TITLE_OVERLAP_PERCENTILE = int(os.environ.get("RAG_TITLE_OVERLAP_PERCENTILE", "75"))
_TITLE_OVERLAP_FLOOR_FALLBACK = float(os.environ.get("RAG_TITLE_OVERLAP_FLOOR_FALLBACK", "0.10"))

# iter-12 Class K3: clear-winner confidence-gap bypass. When top1/top2 >= threshold
# the rerank ordering has already separated the winner; magnet damping is unnecessary.
_SCORE_RANK_GAP_BYPASS = float(os.environ.get("RAG_SCORE_RANK_GAP_BYPASS", "1.5"))

# iter-12 Task 32 (R2): percentile-derived demote slope; replaces static 0.85 factor.
_DEMOTE_SLOPE = float(os.environ.get("RAG_SCORE_RANK_DEMOTE_SLOPE", "0.20"))

from website.features.rag_pipeline.types import QueryClass, RetrievalCandidate, ScopeFilter, SourceType, ChunkKind

# iter-10 P3: score-rank-correlation magnet gate. THEMATIC/STEP_BACK only.
# NOT applied to LOOKUP (legitimate proper-noun magnets), VAGUE (already
# gated by vague_low_entity), or MULTI_HOP (loses hop-2 anchors).
_SCORE_RANK_GATED_CLASSES = (QueryClass.THEMATIC, QueryClass.STEP_BACK)
from website.core.supabase_v2.client import get_v2_client
from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter

_log = logging.getLogger(__name__)

# Phase 8.5.B-4: visibility on bandit-disabled state at startup.
if _BANDIT_POSTERIOR_ENABLED:
    _log.warning(
        "RAG bandit posterior ENABLED — confirm phase-transition criterion fired "
        "(≥1k events per cell, golden-set plateau, ≥3%% nDCG lift, operator approval)"
    )
else:
    _log.info(
        "RAG bandit disabled, sample threshold not met; "
        "kasten_signal_boost=%s scale=%.3f cold_start_threshold=%d",
        _KASTEN_SIGNAL_BOOST_ENABLED,
        _KASTEN_SIGNAL_BOOST_SCALE,
        _KASTEN_SIGNAL_COLD_START_THRESHOLD,
    )

_DEPTH_BY_CLASS = {
    QueryClass.LOOKUP: 1,
    QueryClass.VAGUE: 1,
    QueryClass.MULTI_HOP: 2,
    QueryClass.THEMATIC: 2,
    QueryClass.STEP_BACK: 2,
}

# Query-class-aware fusion weights (semantic, fulltext, graph). LOOKUP queries
# benefit from stronger lexical match on proper nouns and titles, MULTI_HOP
# and STEP_BACK queries benefit from graph expansion, THEMATIC leans semantic.
# Weights sum to ~1.0 per class to keep RRF score magnitudes comparable.
# iter-06 best-of: restore iter-03 THEMATIC weights (0.55, 0.20, 0.25). iter-03
# delivered synthesis 88.22 with these weights; iter-04's softer fulltext
# rebalance was probe-specific and slightly hurt synthesis because broader
# fulltext recall pulled in tag-only matches. Pair with cascade fusion below.
_WEIGHTS_BY_CLASS: dict[QueryClass, tuple[float, float, float]] = {
    QueryClass.LOOKUP: (0.35, 0.50, 0.15),
    QueryClass.VAGUE: (0.55, 0.25, 0.20),
    QueryClass.MULTI_HOP: (0.40, 0.25, 0.35),
    QueryClass.THEMATIC: (0.55, 0.20, 0.25),  # iter-06: revert to iter-03 best-of
    QueryClass.STEP_BACK: (0.50, 0.20, 0.30),
}
_DEFAULT_WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)


def _weights_for_class(
    query_class: QueryClass,
    registry_adapter: RegistryAdapter | None = None,
) -> tuple[float, float, float]:
    static_weights = _WEIGHTS_BY_CLASS.get(query_class, _DEFAULT_WEIGHTS)
    if registry_adapter is None:
        return static_weights

    sem_w = registry_adapter.get_weight("semantic", static_weights[0])
    fts_w = registry_adapter.get_weight("fts", static_weights[1])
    graph_w = registry_adapter.get_weight("kg_graph", static_weights[2])
    return sem_w, fts_w, graph_w

# iter-03 retune: revert per-node chunk cap from 2 -> 3. iter-02 showed that
# cap=2 starved the synthesis stage (faithfulness 1.0 -> 0.5, hallucination
# 0 -> 0.2) because the LLM extrapolated past shrunken contexts.
# Restoring breadth at the chunk level; precision is now policed at the
# context-assembly stage via a similarity floor (see context/assembler.py).
_MAX_CHUNKS_PER_NODE = 3

# iter-08 Phase 3.1: class-aware chunks-per-node cap. THEMATIC and LOOKUP
# get cap=1 (RES-4: kills the 16-chunk yt-effective-public-speakin magnet
# without hurting cross-source recall). MULTI_HOP and STEP_BACK keep cap=3
# (genuinely need cross-chunk evidence). VAGUE keeps cap=3 (HyDE wide net).
_MAX_CHUNKS_PER_NODE_BY_CLASS: dict[QueryClass, int] = {
    QueryClass.LOOKUP: 1,
    QueryClass.THEMATIC: 1,
    QueryClass.MULTI_HOP: 3,
    QueryClass.STEP_BACK: 3,
    QueryClass.VAGUE: 3,
}
_DEFAULT_MAX_CHUNKS_PER_NODE = 3

# Phase 2.4.5-int2: post-fusion zettel rollup cap. Under chunk-level dedup,
# candidates are unique per canonical_chunk_id, but multiple chunks can
# belong to the same canonical_zettel_id. Cap at 3 chunks per zettel by
# default (operator ruling #5) so a verbose source cannot crowd top-K
# handed to the cross-encoder. Per-class variation is deferred to Phase 7
# hardening if eval shows a need; default 3 covers all classes today.
_MAX_CHUNKS_PER_ZETTEL = 3

# iter-04: xQuAD diversity-by-construction (Abdollahpouri et al. 2017).
# After all per-candidate score adjustments, we pick top-K slot-by-slot
# greedy-maximising lambda*rel - (1-lambda)*overlap_with_already_picked,
# where overlap is per-node_id (so a magnet that already has a chunk in
# the picked set gets demoted for subsequent slots). Replaces a flat
# sort that let one node monopolize the top of the candidate list.
_XQUAD_LAMBDA = 0.7

# iter-08 Phase 3.2: per-class xQuAD lambda. THEMATIC drops to 0.5 to buy
# more diversity for cross-corpus synthesis (RES-4); other classes keep 0.7.
_XQUAD_LAMBDA_DEFAULT = 0.7
_XQUAD_LAMBDA_BY_CLASS: dict[QueryClass, float] = {
    QueryClass.THEMATIC: 0.5,
}


def _xquad_lambda_for_class(query_class: QueryClass | None) -> float:
    return _XQUAD_LAMBDA_BY_CLASS.get(query_class, _XQUAD_LAMBDA_DEFAULT)


def _pick_anchor_pin(
    candidates,
    anchor_neighbours,
    *,
    evidence_floor: float = 0.05,
):
    """iter-12 Task 32 (R2 slot-constraint pattern).

    Pick the highest-rrf anchored candidate whose `_title_overlap_boost`
    crosses `evidence_floor`. Returns None when no candidate qualifies
    (vanilla xQuAD fallback). Cap pin to slot-1 only — anchors 2/N still
    compete via xQuAD diversity.
    """
    if not anchor_neighbours:
        return None
    qualifying = [
        c for c in candidates
        if c.node_id in anchor_neighbours
        and float(c.metadata.get("_title_overlap_boost", 0.0)) >= evidence_floor
    ]
    if not qualifying:
        return None
    return max(qualifying, key=lambda c: c.rrf_score)


def _demote_factor_for_candidate(candidate, base_rrf_pool: list[float]) -> float:
    """iter-12 Task 32 (R2 percentile-derived demote).

    Replaces static 0.85 factor. Top-percentile magnet gets gentle ~0.90;
    bottom-percentile gets firmer ~0.70. Single slope knob scales per query.
    """
    base = float(candidate.metadata.get("_base_rrf_score", candidate.rrf_score))
    n = len(base_rrf_pool)
    if n == 0:
        return 0.85  # legacy fallback — edge case with empty pool
    rank_above = sum(1 for s in base_rrf_pool if s <= base) / n
    return max(0.70, min(0.90, 1.0 - _DEMOTE_SLOPE * (1.0 - rank_above)))


# iter-08 Phase 3.3: text-only compare-intent detection. Closes iter-07
# Fix B's "Naval not in Kasten" hole — fires on rewritten-query text alone,
# independent of metadata.authors count.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b")
_COMPARE_JOIN_RE = re.compile(r"\b(and|both)\b", re.IGNORECASE)
_PROPER_NOUN_BLACKLIST = {
    "What", "How", "When", "Where", "Why", "The", "A", "An", "This", "That",
}


def _detect_compare_intent_text_only(query: str) -> bool:
    if not query:
        return False
    if not _COMPARE_PATTERN.search(query):
        # Fall back to "and" + ≥2 proper-noun spans
        if not _COMPARE_JOIN_RE.search(query):
            return False
    proper_nouns = _PROPER_NOUN_RE.findall(query)
    proper_nouns = [n for n in proper_nouns if n.split()[0] not in _PROPER_NOUN_BLACKLIST]
    return len(set(proper_nouns)) >= 2

# iter-04 consensus-suppress threshold: if a candidate appears in >= this
# fraction of variants, suppress the per-variant consensus bump (it's a
# magnet, not a relevance signal). The bump is at line ~169.
_CONSENSUS_SUPPRESS_FRACTION = 0.5


from dataclasses import dataclass


@dataclass
class _AnchorSeedDecision:
    fire: bool
    reason: str


def _percentile(values: list[float], p: int) -> float:
    """iter-12 Q5: linear-interpolation percentile over a list. Empty → 0.0."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return sorted_v[0]
    rank = (p / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    return sorted_v[lo] * (1 - (rank - lo)) + sorted_v[hi] * (rank - lo)


def _top1_top2_gap(candidates) -> float | None:
    """iter-12 Class K3: relative confidence-gap between top-1 and top-2 rrf_score.

    Returns top1/top2 ratio, or None when fewer than 2 candidates exist.
    Used by _apply_score_rank_demote (magnet-gate bypass) and _retry_gap_bypass_threshold.
    """
    if not candidates or len(candidates) < 2:
        return None
    sorted_cands = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
    top1 = sorted_cands[0].rrf_score
    top2 = max(sorted_cands[1].rrf_score, 1e-9)
    return top1 / top2


_TIEBREAK_INVERT_CLASSES = (
    QueryClass.THEMATIC, QueryClass.MULTI_HOP, QueryClass.STEP_BACK,
)


def _apply_score_rank_demote(
    candidates: list[RetrievalCandidate],
    *,
    query_class: QueryClass | None,
    query_text: str = "",
    anchor_nodes: set[str] | None = None,
) -> None:
    """iter-10 P3 + iter-11 Class A: in-place rrf_score demote for
    THEMATIC/STEP_BACK magnets, with an anchor / title-overlap exemption.

    A node is a magnet if its top-1 ranking is disproportionate to its base
    retrieval percentile. We compute each candidate's percentile of
    ``_base_rrf_score`` (rrf BEFORE class boosts) and compare to its current
    rank percentile (after all boosts). When delta >= disprop_quartiles * 0.25,
    multiply rrf_score by demote_factor.

    Independently: a ``_title_overlap_boost`` >= floor triggers a secondary
    multiplicative damp — catches the "title carries the win" pattern that
    score-rank misses on small candidate pools.

    iter-11 Class A earned-exemption carve-out: a candidate whose node_id is in
    ``anchor_nodes`` (the resolved-entity set from
    ``entity_anchor.resolve_anchor_nodes``) OR whose ``_title_overlap_boost``
    is > 0 (query verbatim names this zettel) skips BOTH the primary score-rank
    demote AND the title-overlap secondary demote. Statistical detection still
    runs for unanchored candidates so unearned magnets (q5-shape) keep getting
    damped.

    Mutates ``candidates`` in place. LOOKUP / VAGUE / MULTI_HOP bypass.
    """
    del query_text  # kept for future signal extension
    if query_class not in _SCORE_RANK_GATED_CLASSES:
        return
    if not candidates or len(candidates) < 4:
        return

    # iter-12 Class K3: clear-winner bypass — skip demote when top1/top2 >= 1.5.
    gap = _top1_top2_gap(candidates)
    if gap is not None and gap >= _SCORE_RANK_GAP_BYPASS:
        _log.info("score_rank_gate bypass=clear_winner gap=%.3f class=%s",
                  gap, getattr(query_class, "value", query_class))
        return

    anchored = anchor_nodes or set()

    # iter-12 Q5: P75 of pool's title-overlap boosts; floor at 0.10 prevents
    # incidental token-overlap (e.g. 0.05) from earning exemption.
    pool_boosts = [float(c.metadata.get("_title_overlap_boost", 0.0)) for c in candidates]
    boost_p_threshold = max(
        _percentile(pool_boosts, _TITLE_OVERLAP_PERCENTILE),
        _TITLE_OVERLAP_FLOOR_FALLBACK,
    )

    base_scores = [
        float(c.metadata.get("_base_rrf_score", c.rrf_score))
        for c in candidates
    ]
    sorted_base = sorted(base_scores)
    n = len(base_scores)

    def _base_percentile(score: float) -> float:
        return sum(1 for s in sorted_base if s <= score) / n

    current_sorted = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
    current_rank = {id(c): (n - i) / n for i, c in enumerate(current_sorted)}

    delta_threshold = _SCORE_RANK_DISPROP_QUARTILES * 0.25
    # iter-12 Task 32: build base_rrf_pool once for percentile-derived factor.
    base_pool = [float(c.metadata.get("_base_rrf_score", c.rrf_score)) for c in candidates]
    n_demoted = 0
    n_title_demoted = 0
    factor_sum = 0.0
    for c in candidates:
        # iter-12 Q5: earned exemption — anchored entity OR boost >= P75(pool) floor 0.10.
        # Replaces iter-11 binary > 0.0 that let incidental overlap (~0.05) exempt magnets.
        is_anchored = c.node_id in anchored
        has_earned_title = float(c.metadata.get("_title_overlap_boost", 0.0)) >= boost_p_threshold
        if is_anchored or has_earned_title:
            continue
        base_pct = _base_percentile(float(c.metadata.get("_base_rrf_score", c.rrf_score)))
        rank_pct = current_rank[id(c)]
        delta = rank_pct - base_pct
        if delta >= delta_threshold:
            factor = _demote_factor_for_candidate(c, base_pool)
            c.rrf_score *= factor
            n_demoted += 1
            factor_sum += factor
        title_boost = float(c.metadata.get("_title_overlap_boost", 0.0))
        if title_boost >= _TITLE_OVERLAP_DEMOTE_FLOOR:
            c.rrf_score *= _TITLE_OVERLAP_DEMOTE_FACTOR
            n_title_demoted += 1
    # iter-12 Task 26/32 telemetry: margin tracks separation after demote.
    post_demote = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
    top1 = post_demote[0].rrf_score if post_demote else 0.0
    top2 = post_demote[1].rrf_score if len(post_demote) > 1 else 0.0
    margin = top1 - top2
    _log.info(
        "score_rank_demote class=%s n_cands=%d slope=%.3f post_top1=%.4f post_top2=%.4f margin=%.4f",
        getattr(query_class, "value", query_class),
        n, _DEMOTE_SLOPE, top1, top2, margin,
    )


def _tiebreak_key(
    rrf_score: float,
    chunk_count: int,
    chunk_counts: dict[str, int],
    query_class: QueryClass | None,
    title_overlap_boost: float = 0.0,
) -> tuple[float, float]:
    """iter-10 Item 3 + iter-11 Class B: deterministic tie-breaker on
    ``chunk_count_quartile`` with a name-overlap override.

    Returned tuple is sorted with reverse=True, so a higher second element
    wins when rrf_score is tied. The bias is sub-floor (×0.0001) so it can
    NEVER override real rrf differences — only resolves true ties.

    LOOKUP / VAGUE: prefer higher quartile (chunky relevant zettels).
    THEMATIC / MULTI_HOP / STEP_BACK: prefer lower quartile (broad coverage).

    iter-11 Class B: when ``title_overlap_boost > 0`` (query verbatim names
    this zettel), the THEMATIC-family inversion is BYPASSED for this
    candidate — name-overlap is a stronger signal of "this is the user's
    target" than coverage breadth. Net effect: a named multi-chunk gold
    zettel wins ties just like a LOOKUP candidate would.
    """
    if not chunk_counts or chunk_count <= 0:
        return (rrf_score, 0.0)
    counts = list(chunk_counts.values())
    n = len(counts)
    rank = sum(1 for c in counts if c <= chunk_count) / n
    invert = (
        query_class in _TIEBREAK_INVERT_CLASSES
        and title_overlap_boost <= 0.0
    )
    bias = (1.0 - rank) if invert else rank
    return (rrf_score, bias * 0.0001)


def _should_inject_anchor_seeds(
    query_class: QueryClass | None,
    compare_intent: bool,
    anchor_nodes: set[str] | list[str],
    entities_resolving: list[str],
) -> _AnchorSeedDecision:
    """iter-10 P4: anchor-seed gate after dropping the iter-09 RES-7 re-gate.

    Old re-gate ``(n_persons + n_entities) >= 1`` rejected q10's "Steve Jobs"
    when NER missed the single-name surname. anchor_nodes being non-empty
    already proves entity match at the kasten level (RPC INNER JOINs
    ``kg_nodes.name ILIKE '%' || e || '%' OR e = ANY(n.tags)``), so the count
    re-gate is double-filtering.

    Defense-in-depth ordering matters:
      1. ``no_anchor_nodes`` — nothing to seed.
      2. ``compare_intent`` — short-circuits class checks (multi-LOOKUP shape).
      3. ``thematic_excluded`` — even if router misclassifies a LOOKUP as
         THEMATIC, no inject (avoids q5-shape pulling a magnet).
      4. ``non_lookup`` — only LOOKUP fires (compare-intent already passed).
      5. ``entity_length_floor`` — short tags like 'AI'/'ML' tag-collide.
    """
    if not anchor_nodes:
        return _AnchorSeedDecision(False, "no_anchor_nodes")
    if compare_intent:
        return _AnchorSeedDecision(True, "compare_intent")
    if query_class is QueryClass.THEMATIC:
        return _AnchorSeedDecision(False, "thematic_excluded")
    if query_class is not QueryClass.LOOKUP:
        return _AnchorSeedDecision(False, "non_lookup")
    long_enough = [
        e for e in (entities_resolving or [])
        if isinstance(e, str) and len(e.strip()) >= _ANCHOR_SEED_MIN_ENTITY_LENGTH
    ]
    if not long_enough:
        return _AnchorSeedDecision(False, "entity_length_floor")
    return _AnchorSeedDecision(True, "lookup_with_long_entity")


class HybridRetriever:
    def __init__(
        self,
        embedder: Any,
        supabase: Any | None = None,
        *,
        kasten_freq_store: KastenFrequencyStore | None = None,  # deprecated (iter-08 P4.2)
        chunk_share_store: ChunkShareStore | None = None,
        registry_adapter: RegistryAdapter | None = None,
    ):
        # Phase 2.4.6: default client swap. Every RPC the retriever calls
        # (kg.*, content.*_kasten, rag.*_v2) lives in the v2 project; the
        # legacy supabase_kg fallback was removed (Phase 2 exit invariant).
        # Tests that need a stub still pass ``supabase=`` explicitly.
        self._supabase = supabase if supabase is not None else get_v2_client()
        self._embedder = embedder
        # iter-08 P4.2: kasten_freq prior bypassed (RES-2 floor=50 never crossed).
        # Kept on instance as deprecated attr so orchestrator's getattr lookup is
        # still safe; the field is no longer consulted in retrieve().
        self._kasten_freq = kasten_freq_store or KastenFrequencyStore(self._supabase)
        self._chunk_share = chunk_share_store or ChunkShareStore(supabase=self._supabase)
        self._registry_adapter = registry_adapter

    async def retrieve(
        self,
        *,
        user_id: UUID,
        query_variants: list[str],
        sandbox_id: UUID | None,
        scope_filter: ScopeFilter,
        query_class: QueryClass,
        limit: int = 30,
        query_metadata: QueryMetadata | None = None,
    ) -> list[RetrievalCandidate]:
        # Phase 2.4.1: kg.* v2 RPCs are workspace-scoped, content.*_kasten and
        # rag.*_v2 RPCs are kasten-scoped. The retriever takes ``sandbox_id`` as
        # the kasten id; we resolve the owning workspace_id once here so KG
        # anchor resolution + subgraph expansion can be tenant-scoped.
        workspace_id = await self._resolve_workspace_id(sandbox_id)

        effective_nodes = await self._resolve_nodes(user_id, sandbox_id, scope_filter)
        if effective_nodes is not None and len(effective_nodes) == 0:
            raise EmptyScopeError("Scope resolved to zero Zettels")

        query_variants = _dedupe_variants(query_variants)

        embeddings = await asyncio.gather(*[
            self._embedder.embed_query_with_cache(query) for query in query_variants
        ])
        graph_depth = _DEPTH_BY_CLASS[query_class]
        sem_w, fts_w, graph_w = _weights_for_class(query_class, self._registry_adapter)

        # Phase 2.4.5: per-variant kasten-scoped hybrid search returns
        # per-source ranks (fts_rank, semantic_rank) so the 3-source weighted
        # RRF — sem/fts/graph — can be fused in Python (Cormack 2009 RRF on
        # ranks, NEVER on raw scores). The graph signal is derived from
        # anchor_chunk_mentions (entities_to_anchor_chunks) and joined per
        # canonical_chunk_id during fusion below.
        del graph_depth  # v1 graph-depth knob; v2 graph signal is mention-based.

        async def _search(query_text: str, query_vec: list[float]) -> list[dict]:
            if sandbox_id is None:
                return []
            response = await rpc_call(self._supabase.schema("content").rpc(
                "hybrid_search_chunks_kasten",
                {
                    "p_kasten_id": str(sandbox_id),
                    "p_query_text": query_text,
                    "p_query_embedding": query_vec,
                    "p_match_count": limit,
                    "p_rrf_k": 60,
                    # Pass per-source weights through to the SQL fused score so
                    # the legacy ``rrf_score`` column stays meaningful, but the
                    # downstream Python RRF re-fuses with the graph dimension
                    # using fts_rank/semantic_rank decomposed from this RPC.
                    "p_full_text_weight": fts_w,
                    "p_semantic_weight": sem_w,
                },
            ))
            rows = response.data or []
            # Adapt v2 row -> legacy dict shape consumed by _row_to_candidate
            # and the fusion path. Preserve fts_rank / semantic_rank / raw
            # scores so the Python RRF below can decompose them.
            adapted: list[dict] = []
            for row in rows:
                adapted.append({
                    "kind": "chunk",
                    "node_id": str(row["canonical_chunk_id"]),
                    "chunk_id": row.get("canonical_chunk_id"),
                    "chunk_idx": row.get("chunk_idx"),
                    "name": row.get("title") or "",
                    "title": row.get("title") or "",
                    "source_type": row.get("source_type") or "web",
                    "url": "",
                    "content": row.get("content") or "",
                    "tags": list(row.get("user_tags") or []),
                    "metadata": {},
                    "rrf_score": float(row.get("rrf_score") or 0.0),
                    "raw_dense_score": row.get("raw_dense_score"),
                    "raw_fts_score": row.get("raw_fts_score"),
                    "fts_rank": row.get("fts_rank"),
                    "semantic_rank": row.get("semantic_rank"),
                    "canonical_chunk_id": row.get("canonical_chunk_id"),
                    "canonical_zettel_id": row.get("canonical_zettel_id"),
                })
            return adapted

        # iter-08 P4.2: per-Kasten chunk-count fetch parallel with hybrid RPCs.
        # Replaces dead kasten_freq prior (RES-2 floor=50 never crossed).
        if self._chunk_share is not None and sandbox_id is not None:
            counts_task = asyncio.create_task(
                self._chunk_share.get_chunk_counts(sandbox_id)
            )
        else:
            counts_task = None

        results = await asyncio.gather(*[
            _search(query_text, query_vec)
            for query_text, query_vec in zip(query_variants, embeddings)
        ])

        # iter-10 P5 / Phase 2.4.4: kasten-scoped dense-only fallback for
        # recall miss. When the hybrid fan-out returns zero rows AND the
        # kasten is in scope, run ONE dense pass via
        # ``content.search_chunks_enriched_kasten`` so q6/q7-shape recall
        # holes still surface SOMETHING for the cross-encoder. The kasten
        # predicate lives inside the RPC's first CTE — operator anti-pattern
        # guard: NEVER post-filter a workspace-wide RPC for kasten scope.
        # Guarded by env flag; never the primary path.
        total_rows = sum(len(r) for r in results)
        if (
            _DENSE_FALLBACK_ENABLED
            and total_rows == 0
            and sandbox_id is not None
            and embeddings
        ):
            _log.warning("dense_fallback_fire kasten=%s (hybrid recall=0)", sandbox_id)
            try:
                fallback_resp = await rpc_call(self._supabase.schema("content").rpc(
                    "search_chunks_enriched_kasten",
                    {
                        "p_kasten_id": str(sandbox_id),
                        "p_query_embedding": embeddings[0],
                        "p_match_count": min(limit, 8),
                    },
                ))
                fallback_rows = fallback_resp.data or []
                if fallback_rows:
                    # search_chunks_enriched_kasten returns ``score`` (cosine sim);
                    # adapt to the legacy ``rrf_score`` + ``raw_dense_score``
                    # shape downstream code consumes. raw_fts_score stays None
                    # (FTS did not run). Per CandidateBase, ``raw_dense_score``
                    # carries the cosine for diagnostics; ``rrf_score`` keeps
                    # the back-compat single-score field.
                    adapted: list[dict] = []
                    for row in fallback_rows:
                        score = float(row.get("score") or 0.0)
                        adapted.append({
                            "kind": "chunk",
                            "node_id": str(row["canonical_chunk_id"]),
                            "chunk_id": row.get("canonical_chunk_id"),
                            "chunk_idx": row.get("chunk_idx"),
                            "name": row.get("title") or "",
                            "title": row.get("title") or "",
                            "source_type": row.get("source_type") or "web",
                            "url": "",
                            "content": row.get("content") or "",
                            "tags": list(row.get("user_tags") or []),
                            "metadata": {},
                            "rrf_score": score,
                            "raw_dense_score": score,
                            "raw_fts_score": None,
                            "canonical_chunk_id": row.get("canonical_chunk_id"),
                            "canonical_zettel_id": row.get("canonical_zettel_id"),
                        })
                    results = [adapted]
            except Exception as exc:  # noqa: BLE001 — best-effort fallback
                _log.warning("dense_fallback_rpc_error %s: %s", type(exc).__name__, exc)

        chunk_counts: dict[str, int] = {}
        if counts_task is not None:
            try:
                chunk_counts = await counts_task
            except Exception as exc:  # noqa: BLE001 — best-effort
                _log.debug("chunk_share fetch failed: %s", exc)
                chunk_counts = {}

        # iter-10 P12: surface THEMATIC + empty-counts so the
        # _ensure_member_coverage path's recovery cost is visible — this
        # combination is the q5/q7-shape footgun where an over-promoted
        # member causes the magnet selection seen in iter-09.
        if (
            chunk_counts == {}
            and query_class is QueryClass.THEMATIC
            and sandbox_id is not None
        ):
            _log.warning(
                "thematic_empty_counts sandbox=%s — _ensure_member_coverage may overcompensate",
                sandbox_id,
            )

        # iter-08 Phase 6 / G3: resolve KG anchors from query metadata and
        # expand 1-hop. Boost is applied inside _dedup_and_fuse AFTER chunk-
        # share damping so neighbours keep the full +0.05 regardless of size.
        # Phase 2.4.2: anchor_nodes (set[int] bigint kg_node_ids) +
        # anchor_neighbour_kg_nodes (set[int]) hold the typed KG anchor set.
        # The downstream chunk-level scoring pipeline needs canonical_chunk_ids
        # (uuid), so we bridge bigint kg_node_ids -> canonical_chunk_id via
        # ``kg.entities_to_anchor_chunks``. The bridge result is stored in
        # ``anchor_neighbour_chunks`` (set[str] of canonical_chunk_id) and
        # ``anchor_chunk_mentions`` (per-chunk mention_count + distinct
        # kg_node_id count) for the graph-rank signal in 2.4.5.
        anchor_neighbour_kg_nodes: set[int] = set()
        anchor_nodes: list[int] = []
        anchor_neighbours: set[str] = set()
        anchor_chunk_mentions: list[dict] = []
        if (
            _ANCHOR_BOOST_ENABLED
            and query_metadata is not None
            and workspace_id is not None
            and (
                getattr(query_metadata, "authors", None)
                or getattr(query_metadata, "entities", None)
            )
        ):
            try:
                from website.features.rag_pipeline.retrieval.entity_anchor import (
                    resolve_anchor_nodes,
                    get_one_hop_neighbours,
                    entities_to_anchor_chunks,
                )
                entities = list(
                    (getattr(query_metadata, "authors", None) or [])
                    + (getattr(query_metadata, "entities", None) or [])
                )
                anchor_nodes = list(await resolve_anchor_nodes(
                    entities, workspace_id, self._supabase
                ))
                anchor_neighbour_kg_nodes = await get_one_hop_neighbours(
                    set(anchor_nodes), workspace_id, self._supabase
                )
                # Anti-pattern guard: NEVER directly compare bigint kg_node_id
                # to uuid canonical_chunk_id. Bridge via entities_to_anchor_chunks
                # so anchor_neighbours stays in the same ID space (uuid string)
                # as ChunkCandidate.canonical_chunk_id.
                if anchor_neighbour_kg_nodes:
                    anchor_chunk_mentions = await entities_to_anchor_chunks(
                        anchor_neighbour_kg_nodes, workspace_id, self._supabase
                    )
                    anchor_neighbours = {
                        m["canonical_chunk_id"] for m in anchor_chunk_mentions
                    }
            except Exception as exc:  # noqa: BLE001 — best-effort
                _log.debug("anchor_boost fetch failed: %s", exc)
                anchor_neighbour_kg_nodes = set()
                anchor_nodes = []
                anchor_neighbours = set()
                anchor_chunk_mentions = []

        # iter-10 P4: anchor-seed injection through pure-function gate.
        # Drops iter-09 (n_persons + n_entities) >= 1 re-gate (q10 fix);
        # adds class exclusion, entity-length floor, top-K cap, structured log.
        # iter-12 T31 R4: floor provided by Thompson-sampling bandit.
        anchor_seeds: list[dict] = []
        _bandit_floor: float = _ANCHOR_SEED_FLOOR_RRF  # static fallback default
        _bandit_telemetry: dict = {}
        if (
            _ANCHOR_SEED_ENABLED
            and sandbox_id is not None
            and embeddings
        ):
            compare = bool(getattr(query_metadata, "compare_intent", False)) if query_metadata else False
            entities_resolving = list(
                (getattr(query_metadata, "authors", None) or [])
                + (getattr(query_metadata, "entities", None) or [])
            ) if query_metadata else []
            decision = _should_inject_anchor_seeds(
                query_class=query_class,
                compare_intent=compare,
                anchor_nodes=anchor_nodes,
                entities_resolving=entities_resolving,
            )
            if decision.fire:
                from website.features.rag_pipeline.retrieval.anchor_seed import (
                    fetch_anchor_seeds,
                )
                # iter-12 T31 R4: sample the bandit floor for this request.
                try:
                    from website.features.rag_pipeline.observability.anchor_seed_bandit import (
                        sample_floor as _bandit_sample_floor,
                    )
                    _bandit_floor, _bandit_telemetry = await _bandit_sample_floor(
                        p_user_id=str(user_id),
                        kasten_id=str(sandbox_id),
                        pool_size=sum(len(r) for r in results),
                        supabase=self._supabase,
                    )
                except Exception as _be:
                    _log.debug("bandit_sample_failed: %s", _be)
                    _bandit_floor = _ANCHOR_SEED_FLOOR_RRF
                # Phase 2.4.3: rag.fetch_anchor_seeds_v2 takes canonical_chunk_ids
                # (uuid[]), not bigint kg_node_ids. Use the bridge result from
                # anchor_chunk_mentions; fall back to seed-anchor chunks when
                # neighbours-bridge missed (rare). Anti-pattern guard: never
                # pass bigint anchor_nodes directly to a uuid[]-typed RPC.
                seed_chunk_ids = list({m["canonical_chunk_id"] for m in anchor_chunk_mentions})
                raw_seeds = await fetch_anchor_seeds(
                    seed_chunk_ids, sandbox_id, embeddings[0], self._supabase
                )
                anchor_seeds = sorted(
                    raw_seeds,
                    key=lambda r: float(r.get("score") or 0.0),
                    reverse=True,
                )[:_ANCHOR_SEED_TOP_K]
                _log.info(
                    "anchor_seed_inject reason=%s class=%s n_anchors=%d n_seeds=%d "
                    "floor=%.2f fallback=%s entropy=%.4f",
                    decision.reason,
                    getattr(query_class, "value", query_class),
                    len(list(anchor_nodes)),
                    len(anchor_seeds),
                    _bandit_floor,
                    _bandit_telemetry.get("fallback_reason"),
                    _bandit_telemetry.get("posterior_entropy_nats") or 0.0,
                )
            else:
                _log.debug("anchor_seed skipped: %s", decision.reason)

        # Phase 2.4.2: anchor_chunks_for_score_rank holds the canonical_chunk_id
        # (uuid string) set used by the score-rank-correlation magnet gate's
        # earned-exemption carve-out. Built from anchor_nodes (the seed kg_node
        # set) bridged via entities_to_anchor_chunks; never compared directly
        # to bigint kg_node_id.
        anchor_chunks_for_score_rank: set[str] = (
            {m["canonical_chunk_id"] for m in anchor_chunk_mentions
             if int(m["kg_node_id"]) in set(anchor_nodes)}
            if anchor_nodes else set()
        )
        # Phase 8.5.B-4: best-effort kasten retrieval-signal fetch (silently
        # no-ops on any failure; cold-start guard inside the helper).
        kasten_signals, kasten_event_count = await self._fetch_kasten_signals(
            workspace_id=workspace_id,
            kasten_id=sandbox_id,
            anchor_node_ids=anchor_nodes if anchor_nodes else None,
        )

        fused = self._dedup_and_fuse(
            results,
            query_variants=query_variants,
            query_metadata=query_metadata,
            query_class=query_class,
            chunk_counts=chunk_counts,
            effective_nodes=effective_nodes,
            anchor_neighbours=anchor_neighbours,
            anchor_nodes=anchor_chunks_for_score_rank if anchor_chunks_for_score_rank else None,
            anchor_seeds=anchor_seeds,
            anchor_seed_floor=_bandit_floor,
            # Phase 2.4.5: per-source weighted RRF inputs.
            anchor_chunk_mentions=anchor_chunk_mentions,
            sem_weight=sem_w,
            fts_weight=fts_w,
            graph_weight=graph_w,
            # Phase 8.5.B-4: kasten retrieval signal boost.
            kasten_signals=kasten_signals,
            kasten_event_count=kasten_event_count,
        )

        # iter-12 T31 R4: record bandit reward post-fuse.
        # Only fires when bandit actually sampled an arm (fallback_reason=None)
        # and seeds were injected. Fail-open inside record_outcome.
        if (
            anchor_seeds
            and sandbox_id is not None
            and _bandit_telemetry.get("fallback_reason") is None
            and _bandit_telemetry.get("arm_sampled") is not None
        ):
            try:
                from website.features.rag_pipeline.observability.anchor_seed_bandit import (
                    record_outcome as _bandit_record,
                    bucket_pool_size as _bucket,
                    _FINAL_TOP_K,
                )
                top_k_node_ids = {c.node_id for c in fused[:_FINAL_TOP_K]}
                pool_sz = sum(len(r) for r in results)
                bucket = _bucket(pool_sz)
                arm_used = float(_bandit_telemetry["arm_sampled"])
                for seed in anchor_seeds:
                    nid = seed.get("node_id")
                    if not nid:
                        continue
                    survived = nid in top_k_node_ids
                    await _bandit_record(
                        p_user_id=str(user_id),
                        kasten_id=str(sandbox_id),
                        arm=arm_used,
                        pool_bucket=bucket,
                        seed_survived=survived,
                        supabase=self._supabase,
                    )
            except Exception as _re:
                _log.debug("bandit_record_error: %s", _re)

        return fused

    async def _resolve_workspace_id(self, sandbox_id: UUID | None) -> UUID | None:
        """Look up the workspace_id that owns ``sandbox_id`` (kasten).

        Phase 2.4.1: kg.* v2 RPCs require ``p_workspace_id``; the retriever
        only has ``sandbox_id`` (kasten UUID). One ``rag.kastens`` lookup per
        retrieve() call resolves the owning workspace. Returns ``None`` when
        no kasten is in scope (open-scope queries) or the lookup fails — the
        downstream KG paths are best-effort and short-circuit on None.
        """
        if sandbox_id is None:
            return None
        try:
            response = await rpc_call(
                self._supabase.schema("rag").table("kastens").select("workspace_id").eq("id", str(sandbox_id)).limit(1)
            )
            rows = response.data or []
            if not rows:
                return None
            return UUID(str(rows[0]["workspace_id"]))
        except Exception as exc:  # noqa: BLE001 — best-effort, never blocks retrieval
            _log.debug("resolve_workspace_id failed for kasten=%s: %s", sandbox_id, exc)
            return None

    async def _fetch_kasten_signals(
        self,
        workspace_id: UUID | None,
        kasten_id: UUID | None,
        anchor_node_ids: set[str] | None,
    ) -> tuple[dict[tuple[str, str], tuple[float, float]], int]:
        """Read (source, target) -> (positive, negative) from kasten_retrieval_edge_signals MV.

        Phase 8.5.B-4: feeds raw-count boost in _apply_kasten_signal_boost.
        Returns ({}, 0) on any failure — boost is best-effort, never blocks
        retrieval. Cold-start guard runs in the consumer.
        """
        if not _KASTEN_SIGNAL_BOOST_ENABLED or not workspace_id or not kasten_id:
            return {}, 0
        if not anchor_node_ids:
            return {}, 0
        try:
            count_resp = await rpc_call(
                self._supabase.schema("rag")
                .table("kasten_retrieval_edge_signals")
                .select("event_count")
                .eq("workspace_id", str(workspace_id))
                .eq("kasten_id", str(kasten_id))
            )
            total = sum(int(r.get("event_count") or 0) for r in (count_resp.data or []))
            if total < _KASTEN_SIGNAL_COLD_START_THRESHOLD:
                return {}, total
            sig_resp = await rpc_call(
                self._supabase.schema("rag")
                .table("kasten_retrieval_edge_signals")
                .select("source_node_id,target_node_id,positive_signal,negative_signal")
                .eq("workspace_id", str(workspace_id))
                .eq("kasten_id", str(kasten_id))
                .in_("source_node_id", [str(a) for a in anchor_node_ids])
            )
            signals: dict[tuple[str, str], tuple[float, float]] = {}
            for row in (sig_resp.data or []):
                src = row.get("source_node_id")
                tgt = row.get("target_node_id")
                if src is None or tgt is None:
                    continue
                pos = float(row.get("positive_signal") or 0.0)
                neg = float(row.get("negative_signal") or 0.0)
                signals[(str(src), str(tgt))] = (pos, neg)
            return signals, total
        except Exception as exc:  # noqa: BLE001 — best-effort
            _log.debug("kasten_signal_fetch_failed kasten=%s: %s", kasten_id, exc)
            return {}, 0

    async def _resolve_nodes(
        self,
        user_id: UUID,
        sandbox_id: UUID | None,
        scope_filter: ScopeFilter,
    ) -> list[str] | None:
        # Phase 2.4.6: kasten-scoped scope resolution via
        # ``rag.resolve_effective_nodes_v2``. Returns
        # ``[(workspace_zettel_id, canonical_zettel_id), ...]``; we surface
        # canonical_zettel_id as the effective-node id since downstream code
        # (anchor-seed RPC, _ensure_member_coverage) treats this as a
        # zettel-id list. The legacy ``rag_resolve_effective_nodes`` accepted
        # node_ids as a free-form filter; the v2 RPC retired that knob (use
        # the kasten members), so a non-empty scope_filter.node_ids degrades
        # gracefully to the unfiltered kasten member list.
        del user_id  # v2 RPC authorises via kasten ownership + JWT.
        if sandbox_id is None and not any(
            [scope_filter.node_ids, scope_filter.tags, scope_filter.source_types]
        ):
            return None
        if sandbox_id is None:
            # No kasten in scope but ScopeFilter present — v2 RPC requires
            # a kasten_id, so we cannot resolve. Surface "unfiltered" semantics
            # by returning None (caller treats None as no scope restriction).
            return None
        response = await rpc_call(self._supabase.schema("rag").rpc(
            "resolve_effective_nodes_v2",
            {
                "p_kasten_id": str(sandbox_id),
                "p_tags": scope_filter.tags,
                "p_tag_mode": scope_filter.tag_mode if scope_filter.tags else "any",
                "p_source_types": [item.value for item in scope_filter.source_types] if scope_filter.source_types else None,
            },
        ))
        return [str(row["canonical_zettel_id"]) for row in (response.data or []) if row.get("canonical_zettel_id")]

    def _dedup_and_fuse(
        self,
        multi_variant: list[list[dict]],
        *,
        query_variants: list[str] | None = None,
        query_metadata: QueryMetadata | None = None,
        query_class: QueryClass | None = None,
        chunk_counts: dict[str, int] | None = None,
        effective_nodes: list[str] | None = None,
        anchor_neighbours: set[str] | None = None,
        anchor_nodes: set[str] | None = None,
        anchor_seeds: list[dict] | None = None,
        # iter-12 T31 R4: bandit-sampled floor; falls back to static constant.
        anchor_seed_floor: float | None = None,
        # Phase 2.4.5: per-source weighted RRF inputs (sem/fts/graph) plus
        # graph-rank source data from the entities_to_anchor_chunks bridge.
        anchor_chunk_mentions: list[dict] | None = None,
        sem_weight: float = 0.5,
        fts_weight: float = 0.3,
        graph_weight: float = 0.2,
        # Phase 8.5.B-4: kasten retrieval signal boost (raw counts from
        # rag.kasten_retrieval_edge_signals MV). Cold-start guard skips boost
        # when total_event_count is below threshold.
        kasten_signals: dict[tuple[str, str], tuple[float, float]] | None = None,
        kasten_event_count: int = 0,
    ) -> list[RetrievalCandidate]:
        # Phase 2.4.5-int: chunk-level dedup. The legacy v1 (kind, node_id,
        # chunk_id) tuple is collapsed to canonical_chunk_id since v2 ChunkCandidate
        # node_id IS the canonical_chunk_id. The zettel-level _MAX_CHUNKS_PER_NODE_BY_CLASS
        # cap is removed from this function — replaced by the post-fusion
        # _apply_zettel_rollup helper (Phase 2.4.5-int2).
        by_key = {}
        variant_hits = {}
        # Phase 2.4.5: build per-source rank maps (chunk_id -> best rank seen).
        # Lower rank = better; semantic_rank=1 is the closest semantic neighbour.
        sem_rank_map: dict[str, int] = {}
        fts_rank_map: dict[str, int] = {}
        for variant_results in multi_variant:
            seen_in_variant = set()
            for row in variant_results:
                if not row.get("node_id"):
                    # Defensive: aggregate rows with null node_id can't be cited.
                    continue
                # Chunk-level dedup key — collapse legacy (kind, node_id,
                # chunk_id) tuple to canonical_chunk_id (== node_id for v2).
                key = row["node_id"]
                seen_in_variant.add(key)
                # Update per-source rank maps (best rank wins across variants).
                _sem_r = row.get("semantic_rank")
                if _sem_r is not None:
                    prev = sem_rank_map.get(key)
                    if prev is None or int(_sem_r) < prev:
                        sem_rank_map[key] = int(_sem_r)
                _fts_r = row.get("fts_rank")
                if _fts_r is not None:
                    prev = fts_rank_map.get(key)
                    if prev is None or int(_fts_r) < prev:
                        fts_rank_map[key] = int(_fts_r)
                if key not in by_key:
                    cand = _row_to_candidate(row)
                    # Propagate raw component scores onto candidate metadata so
                    # downstream code (rerank diagnostics, eval harness) can
                    # consult them without re-running RPCs.
                    if row.get("raw_dense_score") is not None:
                        cand.metadata["raw_dense_score"] = float(row["raw_dense_score"])
                    if row.get("raw_fts_score") is not None:
                        cand.metadata["raw_fts_score"] = float(row["raw_fts_score"])
                    # Phase 2.4.5-int2: stash canonical_zettel_id for the
                    # post-fusion zettel rollup; caps chunks per zettel.
                    if row.get("canonical_zettel_id") is not None:
                        cand.metadata["canonical_zettel_id"] = str(row["canonical_zettel_id"])
                    by_key[key] = cand
                    variant_hits[key] = 0
                else:
                    by_key[key].rrf_score = max(by_key[key].rrf_score, float(row.get("rrf_score") or 0.0))
                    # Keep best raw scores across duplicate hits.
                    rd = row.get("raw_dense_score")
                    if rd is not None:
                        prev = by_key[key].metadata.get("raw_dense_score")
                        if prev is None or float(rd) > float(prev):
                            by_key[key].metadata["raw_dense_score"] = float(rd)
                    rf = row.get("raw_fts_score")
                    if rf is not None:
                        prev = by_key[key].metadata.get("raw_fts_score")
                        if prev is None or float(rf) > float(prev):
                            by_key[key].metadata["raw_fts_score"] = float(rf)
            for key in seen_in_variant:
                variant_hits[key] += 1

        # Phase 2.4.5: build chunk-level graph rank from anchor_chunk_mentions.
        # Operator ruling #1 — rank chunks by sum(mention_count) DESC then by
        # count(distinct kg_node_id) DESC as deterministic tiebreak. Chunks with
        # the strongest entity-mention signal get rank 1.
        graph_rank_map: dict[str, int] = {}
        if anchor_chunk_mentions:
            agg: dict[str, dict[str, int | set]] = {}
            for m in anchor_chunk_mentions:
                cid = m.get("canonical_chunk_id")
                if not cid:
                    continue
                slot = agg.setdefault(cid, {"sum": 0, "nodes": set()})
                slot["sum"] = int(slot["sum"]) + int(m.get("mention_count") or 1)
                slot["nodes"].add(int(m.get("kg_node_id") or 0))
            ordered = sorted(
                agg.items(),
                key=lambda kv: (-int(kv[1]["sum"]), -len(kv[1]["nodes"])),
            )
            for i, (cid, _slot) in enumerate(ordered, start=1):
                graph_rank_map[str(cid)] = i

        # Phase 2.4.5: Python-side 3-source weighted RRF (Cormack 2009 — fuse
        # ranks, NEVER raw scores). Per-source contribution is 0 when the
        # chunk does not appear in that source's ranked list. Per-class weights
        # come from _WEIGHTS_BY_CLASS (passed in by the caller).
        _RRF_K = 60.0
        if by_key:
            for key, cand in by_key.items():
                sem_r = sem_rank_map.get(key)
                fts_r = fts_rank_map.get(key)
                graph_r = graph_rank_map.get(key)
                rrf = 0.0
                if sem_r is not None:
                    rrf += sem_weight * (1.0 / (_RRF_K + float(sem_r)))
                if fts_r is not None:
                    rrf += fts_weight * (1.0 / (_RRF_K + float(fts_r)))
                if graph_r is not None:
                    rrf += graph_weight * (1.0 / (_RRF_K + float(graph_r)))
                # Replace the SQL-side single-weight rrf_score with the proper
                # 3-source weighted RRF. Fall back to the SQL fused score when
                # all per-source ranks are missing (defensive — should not
                # occur under normal flow).
                if rrf > 0.0:
                    cand.rrf_score = rrf
                cand.metadata["_base_rrf_score"] = cand.rrf_score

        # iter-09 RES-7 / Q10: inject anchor-seed candidates. When a seed's
        # node is already in the pool, bump its rrf_score floor; when missing,
        # synthesize at the floor so the cross-encoder can decide final rank.
        # iter-12 T31 R4: floor is bandit-sampled (or static fallback).
        _effective_floor = anchor_seed_floor if anchor_seed_floor is not None else _ANCHOR_SEED_FLOOR_RRF
        if anchor_seeds:
            for seed in anchor_seeds:
                # Phase 2.4.5: rag.fetch_anchor_seeds_v2 returns
                # ``canonical_chunk_id`` (the seed's chunk-id), not zettel-id.
                # Match by chunk-id under the new chunk-level dedup key.
                cid = seed.get("canonical_chunk_id") or seed.get("node_id")
                if not cid:
                    continue
                cid = str(cid)
                seed_score = float(seed.get("score") or 0.0)
                floored = max(seed_score, _effective_floor)
                if cid in by_key:
                    by_key[cid].rrf_score = max(by_key[cid].rrf_score, floored)
                else:
                    seed_row = {
                        "kind": "chunk",
                        "node_id": cid,
                        "chunk_id": seed.get("canonical_chunk_id"),
                        "chunk_idx": seed.get("chunk_idx"),
                        "name": seed.get("title") or "",
                        "title": seed.get("title") or "",
                        "source_type": seed.get("source_type") or "web",
                        "url": "",
                        "content": seed.get("content") or "",
                        "tags": [],
                        "metadata": {},
                        "rrf_score": floored,
                    }
                    candidate = _row_to_candidate(seed_row)
                    by_key[cid] = candidate
                    variant_hits[cid] = 0

        normalized_variants = [
            _normalize_for_match(v) for v in (query_variants or []) if v and v.strip()
        ]

        kinds_by_node: dict[str, set[str]] = {}
        for candidate in by_key.values():
            kinds_by_node.setdefault(candidate.node_id, set()).add(candidate.kind.value)

        # iter-04 consensus-suppress: a node hit by EVERY variant in a 3+
        # variant fan-out is a topic-magnet (q10 root cause: web-tools-for
        # hit all 3 paraphrases of the Steve Jobs question and won by the
        # consensus bump alone). Suppress the bump only in that magnet
        # case — small 2-variant fan-outs and legit 2-of-3 matches still
        # get the original consensus boost.
        total_variants = max(len(multi_variant), 1)
        for key, candidate in by_key.items():
            hits = variant_hits[key]
            is_magnet = hits == total_variants and total_variants >= 3
            if hits > 1 and not is_magnet:
                candidate.rrf_score += 0.05 * (hits - 1)
            # Title/name-match boost — queries that mention a zettel name
            # verbatim should reliably surface that zettel even when dense /
            # FTS signals are weak (e.g. stub bodies, rare embeddings).
            boost = _title_match_boost(candidate.name, normalized_variants)
            if boost > 0:
                candidate.rrf_score += boost
                # iter-10 P3: track cumulative title-overlap boost so the
                # secondary magnet damp can fire even when score-rank delta
                # alone is below threshold (small candidate pools).
                candidate.metadata["_title_overlap_boost"] = (
                    candidate.metadata.get("_title_overlap_boost", 0.0) + boost
                )
            # Sibling consensus — when both a summary and chunk(s) surface for
            # the same node, that cross-kind agreement is a stronger relevance
            # signal than a single stream. Small bump so it nudges, not skews.
            if len(kinds_by_node.get(candidate.node_id, set())) > 1:
                candidate.rrf_score += 0.03

        # Query-metadata-aware boosts (T10): recency, source-type, and
        # author-match. Skipped entirely when no QueryMetadata is supplied so
        # legacy callers see zero overhead and zero behavioral change.
        if query_metadata is not None and query_class is not None:
            total_boost = 0.0
            # Spec 2B.1: action-verb boost matches against the user's actual
            # question. The first deduped variant is the standalone form of
            # the user's query (rewriter passes it through verbatim when there
            # is no transformation), so use it as the source-of-truth string.
            primary_question = (query_variants or [""])[0] if query_variants else ""
            for candidate in by_key.values():
                rec = _recency_boost(candidate.metadata, query_class)
                src_st = getattr(candidate.source_type, "value", candidate.source_type)
                # _source_type_boost returns the *new* score (base + adjustments).
                # Subtract the unmodified base to derive the delta we apply.
                src_new = _source_type_boost(
                    base_score=0.0,
                    source_type=str(src_st or ""),
                    query_class=query_class,
                    question=primary_question,
                )
                src = src_new  # base was 0.0 -> the return is the delta
                aut = _author_match_boost(candidate, query_metadata)
                delta = rec + src + aut
                if delta:
                    candidate.rrf_score += delta
                    total_boost += delta
            if total_boost:
                _log.debug(
                    "dedup_and_fuse query-metadata boost total=%.4f over %d candidates",
                    total_boost,
                    len(by_key),
                )

        # iter-04 anti-magnet per-Kasten frequency prior. Multiplicatively
        # damps the score of nodes that have a high top-1 hit history within
        # this Kasten. Floor of 50 total hits prevents cold-start over-
        # penalisation; cap at 0.5 so a magnet can still rank where genuine
        # signal puts it.
        # iter-07 Fix B: detect compare-intent — disable anti-magnet for these.
        # iter-08 Phase 3.3: also fire on text-only signal (q10 fix where
        # query_metadata.authors only contains 1 of the 2 named people).
        compare_intent = False
        if _COMPARE_AWARE_ANTIMAGNET_ENABLED and query_metadata is not None:
            authors = list(getattr(query_metadata, "authors", None) or [])
            if len(authors) >= 2:
                for variant in (query_variants or []):
                    if variant and _COMPARE_PATTERN.search(variant):
                        compare_intent = True
                        break
                # Also fire on "X and Y" person-list with no explicit verb,
                # caught only by author count + a join word in the variants.
                if not compare_intent:
                    join_re = re.compile(r"\b(and|both)\b", re.IGNORECASE)
                    for variant in (query_variants or []):
                        if variant and join_re.search(variant):
                            compare_intent = True
                            break
        # iter-08 Phase 3.3: text-only fallback covers q10's "Naval not in
        # Kasten" hole — author-extractor only saw 1 of 2 named people.
        if (
            not compare_intent
            and _COMPARE_AWARE_ANTIMAGNET_ENABLED
            and query_variants
        ):
            for variant in query_variants:
                if variant and _detect_compare_intent_text_only(variant):
                    compare_intent = True
                    break

        # iter-08 Phase 4.2 / iter-09 RES-2: chunk-share normalization with
        # class+magnet gate. iter-08 always-on behaviour was over-damping
        # LOOKUP queries (q11/q12/q3 lost rerank score). iter-09 layers a
        # class gate (THEMATIC/MULTI_HOP only) and a per-query ratio-to-
        # median magnet detector so the damp only fires when there's an
        # actual outlier-magnet to penalise. compare_intent still short-
        # circuits the whole thing.
        chunk_share_enabled = os.environ.get(
            "RAG_CHUNK_SHARE_NORMALIZATION_ENABLED", "true"
        ).lower() not in ("false", "0", "no", "off")
        if query_class is not None:
            should_apply, gate_reason = should_apply_chunk_share(
                query_class, chunk_counts or {}
            )
        else:
            should_apply, gate_reason = True, "no_query_class"
        if (
            chunk_share_enabled
            and chunk_counts
            and not compare_intent
            and should_apply
        ):
            _apply_chunk_share_normalization(list(by_key.values()), chunk_counts)
        elif compare_intent:
            _log.debug(
                "chunk-share normalization disabled: compare-intent detected (authors=%d)",
                len(list(getattr(query_metadata, "authors", None) or [])),
            )
        else:
            _log.debug("chunk-share normalization skipped: gate=%s", gate_reason)

        if anchor_neighbours:
            _apply_anchor_boost(list(by_key.values()), anchor_neighbours)

        # Phase 8.5.B-4: raw-count boost from kasten_retrieval_edge_signals MV.
        # No-op when kasten_signals empty / cold-start gate / feature disabled.
        if kasten_signals and anchor_nodes:
            _apply_kasten_signal_boost(
                list(by_key.values()),
                kasten_signals,
                anchor_node_ids=anchor_nodes,
                total_event_count=kasten_event_count,
            )

        # iter-10 P3: score-rank-correlation magnet gate. THEMATIC/STEP_BACK
        # only. Demotes candidates whose post-boost rank is disproportionate
        # to their base rrf percentile. Runs AFTER chunk-share + anchor-boost
        # so it sees the actual final score, BEFORE the tie-breaker sort.
        _apply_score_rank_demote(
            list(by_key.values()),
            query_class=query_class,
            query_text=(query_variants or [""])[0] if query_variants else "",
            anchor_nodes=anchor_nodes,
        )

        # iter-10 Item 3: chunk_count_quartile tie-breaker. Sub-floor bias
        # (×0.0001) only matters when rrf_score is exactly equal; LOOKUP/VAGUE
        # prefer chunky relevant zettels, THEMATIC/MULTI_HOP/STEP_BACK prefer
        # lower quartile for broader coverage. sorted() remains stable for
        # absolute ties on both axes (insertion order preserved).
        _ccs = chunk_counts or {}
        ordered = sorted(
            by_key.values(),
            key=lambda candidate: _tiebreak_key(
                candidate.rrf_score,
                _ccs.get(candidate.node_id, 0),
                _ccs,
                query_class,
                # iter-11 Class B: positive title overlap flips the THEMATIC-
                # family inversion off for this candidate so a named zettel
                # wins ties.
                title_overlap_boost=float(
                    candidate.metadata.get("_title_overlap_boost", 0.0)
                ),
            ),
            reverse=True,
        )

        # iter-04 xQuAD slot-by-slot selection (Abdollahpouri 2017). Replaces
        # the flat sort with a greedy diversity-by-construction picker:
        # at each slot, pick the candidate that maximises
        # lambda*rel - (1-lambda)*overlap_with_already_picked, where overlap
        # counts node_ids already in the picked set. Diversity-aware ranking
        # is what prevents one node monopolising the top-K (q5 fix).
        # iter-12 Task 32: slot-1 anchor pin — highest-evidence anchored
        # candidate is placed first; remaining slots run normal xQuAD.
        lam = _xquad_lambda_for_class(query_class)
        pin = _pick_anchor_pin(ordered, anchor_neighbours or set(), evidence_floor=0.05)
        if pin is not None:
            ordered_rest = [c for c in ordered if c is not pin]
            ordered = [pin] + _xquad_select(ordered_rest, lam=lam)
        else:
            ordered = _xquad_select(ordered, lam=lam)

        # iter-04: q5 cross-corpus thematic still misses members because xQuAD's
        # 0.3 demotion can't overcome a 1.5x score gap. Promote one chunk per Kasten member
        # to the front for THEMATIC-class queries.
        if (
            query_class is QueryClass.THEMATIC
            and effective_nodes
            and len(effective_nodes) >= 2
        ):
            # iter-07 Fix C: scale the diversity floor by Kasten size.
            # Small Kastens (≤10 members) need a lower floor so all members
            # surface for cross-corpus synthesis (q5 fix). Large Kastens keep
            # the iter-05 floor of 0.05 to avoid q7-style noise promotion.
            _bump_enabled = os.environ.get(
                "RAG_THEMATIC_DIVERSITY_BUMP_ENABLED", "true"
            ).lower() not in ("false", "0", "no", "off")
            n_members = len(effective_nodes)
            if _bump_enabled and n_members <= 10:
                floor = 0.02
            elif _bump_enabled and n_members <= 20:
                floor = 0.035
            else:
                floor = _DIVERSITY_FLOOR_SCORE_MIN
            ordered = _ensure_member_coverage(
                ordered,
                member_ids=effective_nodes,
                min_per_member=1,
                score_floor=floor,
            )

        # Phase 2.4.5-int2: post-fusion zettel rollup. Caps chunks per
        # canonical_zettel_id at _MAX_CHUNKS_PER_ZETTEL (3 by operator
        # ruling #5) so a verbose zettel cannot monopolise top-K. Runs
        # AFTER xQuAD diversity selection so we cap on the post-diversity
        # ordering, not the raw fused list.
        return _apply_zettel_rollup(ordered)


def _apply_anchor_boost(
    candidates: list[RetrievalCandidate],
    neighbour_set: set[str],
    boost: float = _ANCHOR_BOOST_AMOUNT,
) -> None:
    """In-place additive rrf_score bump for candidates 1-hop from a KG anchor.

    iter-08 Phase 6 / G3: lightweight boost (default +0.05) applied AFTER
    chunk-share normalization so anchored neighbours keep the full additive
    boost regardless of chunk count.
    No-op when neighbour_set empty or feature flag disabled.
    """
    if not neighbour_set or not _ANCHOR_BOOST_ENABLED:
        return
    for c in candidates:
        if c.node_id in neighbour_set:
            c.rrf_score += boost


def _compute_kasten_signal_boost(positive: float, negative: float) -> float:
    """Raw-count boost formula from locked spec 8.5.B-4 (NOT bandit math).

    boost = clamp(log(1+pos) - 0.5*log(1+neg), 0, 1.5)

    Pos contributors: cite, accept events on this (anchor → result) edge.
    Neg contributors: reject events. Trivially debuggable; no popularity-collapse
    risk because the cross-encoder remains the primary ranker. Industry-standard
    sparse-feedback heuristic per Cohere/Voyage/Glean (none ship per-tenant
    bandits at <10k events). Mem-vault decision zrUWPShYIYieiXSXi1uzh-Ml.
    """
    if positive <= 0 and negative <= 0:
        return 0.0
    raw = math.log1p(max(0.0, positive)) - 0.5 * math.log1p(max(0.0, negative))
    return max(0.0, min(1.5, raw))


def _apply_kasten_signal_boost(
    candidates: list[RetrievalCandidate],
    signals_by_pair: dict[tuple[str, str], tuple[float, float]],
    *,
    anchor_node_ids: set[str] | None,
    total_event_count: int,
    scale: float = _KASTEN_SIGNAL_BOOST_SCALE,
    cold_start_threshold: int = _KASTEN_SIGNAL_COLD_START_THRESHOLD,
) -> None:
    """In-place additive rrf_score bump from kasten_retrieval_edge_signals MV.

    signals_by_pair: {(source_node_id, target_node_id) -> (positive, negative)}.
    anchor_node_ids: source-side filter (only edges from these anchors contribute).
    total_event_count: Kasten-wide event count from the MV; cold-start guard skips
    the boost when below threshold (avoids high-variance signals from sparse data).

    No-op when feature disabled, MV empty, or under cold-start threshold. Boost is
    additive on rrf_score (matches _apply_anchor_boost convention) and scaled by
    `scale` so the per-edge contribution is comparable to the +0.05 anchor boost.
    """
    if (
        not _KASTEN_SIGNAL_BOOST_ENABLED
        or not signals_by_pair
        or total_event_count < cold_start_threshold
    ):
        return
    if not anchor_node_ids:
        return
    for c in candidates:
        target_id = c.node_id
        # We don't have anchor-of-this-candidate without traversing kg_edges; the
        # signals MV already keys on (source, target), so we sum any (anchor, target)
        # row whose anchor is in the resolved anchor set.
        boost_sum = 0.0
        for anchor in anchor_node_ids:
            sig = signals_by_pair.get((anchor, target_id))
            if sig is None:
                continue
            boost_sum += _compute_kasten_signal_boost(sig[0], sig[1])
        if boost_sum > 0:
            c.rrf_score += boost_sum * scale


def _apply_chunk_share_normalization(
    candidates: list[RetrievalCandidate],
    chunk_counts: dict[str, int],
) -> None:
    """In-place: damp rrf_score by 1/sqrt(chunk_count_per_node).

    iter-08 Phase 4.2 anti-magnet: replaces the dead kasten_freq prior
    (RES-2: floor=50 was never crossed in 6 iters of production runs).
    """
    for c in candidates:
        n = chunk_counts.get(c.node_id, 0)
        if n > 1:
            c.rrf_score *= compute_chunk_share_penalty(n)


def _xquad_select(
    candidates: list[RetrievalCandidate],
    *,
    lam: float = 0.7,
) -> list[RetrievalCandidate]:
    """xQuAD slot-by-slot diversity selector.

    Greedy: at each slot, pick the candidate maximising
    ``lam * rel - (1 - lam) * already_picked_count_for_node``. ``rel`` is
    the candidate's current ``rrf_score`` (already includes all boosts and
    the frequency prior). The penalty is per-``node_id``, so a magnet with
    one chunk picked gets a -0.05 ish demotion when its second chunk would
    otherwise win the next slot.

    Returns a list with the same length as the input (no candidates are
    dropped — only reordered). Stable for ties via insertion order, since
    we iterate the input list in deterministic order.
    """
    if not candidates:
        return candidates
    if len(candidates) == 1:
        return list(candidates)
    remaining = list(candidates)
    picked: list[RetrievalCandidate] = []
    picked_node_counts: dict[str, int] = {}
    one_minus_lam = 1.0 - lam
    while remaining:
        best_idx = 0
        best_score = float("-inf")
        for i, candidate in enumerate(remaining):
            relevance = candidate.rrf_score
            overlap = picked_node_counts.get(candidate.node_id, 0)
            xq = lam * relevance - one_minus_lam * overlap
            if xq > best_score:
                best_score = xq
                best_idx = i
        chosen = remaining.pop(best_idx)
        picked.append(chosen)
        picked_node_counts[chosen.node_id] = picked_node_counts.get(chosen.node_id, 0) + 1
    return picked


_DIVERSITY_FLOOR_SCORE_MIN = 0.05


def _ensure_member_coverage(
    candidates: list[RetrievalCandidate],
    *,
    member_ids: list[str],
    min_per_member: int = 1,
    score_floor: float = _DIVERSITY_FLOOR_SCORE_MIN,
) -> list[RetrievalCandidate]:
    """iter-05: THEMATIC diversity floor with relevance gate. Promote one chunk
    per Kasten member ONLY if that member's best chunk clears ``score_floor``.

    Without the gate (iter-04 impl) this regressed q7 ("Anything about
    commencement?") which is THEMATIC-classified but only one member is
    actually relevant — promoting the other 6 members' near-zero-score chunks
    pushed the magnet zettel out of top-1 and the synthesiser cited the
    wrong member. The ``0.05`` default is empirically chosen to drop chunks
    that don't share any meaningful term with the query while still letting
    legitimate cross-corpus members through (q5-class synthesis queries)."""
    if not candidates or not member_ids or min_per_member < 1:
        return candidates
    member_set = set(member_ids)
    promoted_per_member: dict[str, list[RetrievalCandidate]] = {}
    leftover: list[RetrievalCandidate] = []
    for cand in candidates:
        nid = cand.node_id
        if (
            nid in member_set
            and len(promoted_per_member.get(nid, [])) < min_per_member
            and cand.rrf_score >= score_floor
        ):
            promoted_per_member.setdefault(nid, []).append(cand)
        else:
            leftover.append(cand)
    if not promoted_per_member:
        return candidates
    promoted: list[RetrievalCandidate] = []
    for chunks in promoted_per_member.values():
        promoted.extend(chunks)
    promoted.sort(key=lambda c: c.rrf_score, reverse=True)
    return promoted + leftover


def _apply_zettel_rollup(
    candidates: list[RetrievalCandidate],
    *,
    max_chunks_per_zettel: int = _MAX_CHUNKS_PER_ZETTEL,
) -> list[RetrievalCandidate]:
    """Phase 2.4.5-int2: cap chunks per canonical_zettel_id post-fusion.

    Under chunk-level dedup the candidate pool is unique per
    canonical_chunk_id, but a verbose zettel can still claim N>1 chunks in
    the top-K. This cap (operator ruling #5, default ``_MAX_CHUNKS_PER_ZETTEL``)
    ensures one zettel cannot crowd out the cross-encoder.

    Walks the input in input order (already xQuAD-sorted upstream), keeps
    up to ``max_chunks_per_zettel`` per canonical_zettel_id, and drops the
    excess. ``canonical_zettel_id`` is read from ``candidate.metadata`` (set
    by the v2 row adapter); when missing we fall back to the candidate's
    ``node_id`` (legacy callers, no rollup applied).
    """
    if not candidates or max_chunks_per_zettel < 1:
        return candidates
    seen: dict[str, int] = {}
    kept: list[RetrievalCandidate] = []
    for cand in candidates:
        zid = (
            cand.metadata.get("canonical_zettel_id")
            if cand.metadata
            else None
        )
        bucket_key = str(zid) if zid else cand.node_id
        count = seen.get(bucket_key, 0)
        if count >= max_chunks_per_zettel:
            continue
        seen[bucket_key] = count + 1
        kept.append(cand)
    return kept


def _cap_per_node(
    candidates: list[RetrievalCandidate],
    query_class: QueryClass | int | None = None,
    cap: int | None = None,
) -> list[RetrievalCandidate]:
    """Keep at most ``cap`` chunk candidates per ``node_id`` so a single verbose
    node cannot crowd out the top-K handed to the reranker. Summary-kind
    candidates are unaffected — one summary + N chunks per node still pass
    through.

    iter-08 Phase 3.1: ``query_class`` selects the cap from
    ``_MAX_CHUNKS_PER_NODE_BY_CLASS``. Legacy positional callers that passed
    an int as the second arg still work — ints are treated as ``cap``."""
    # Backward-compat: legacy callers passed a positional int.
    if cap is None:
        if isinstance(query_class, int) and not isinstance(query_class, bool):
            cap = query_class
        else:
            cap = _MAX_CHUNKS_PER_NODE_BY_CLASS.get(query_class, _DEFAULT_MAX_CHUNKS_PER_NODE)
    seen_chunk_count: dict[str, int] = {}
    kept: list[RetrievalCandidate] = []
    for candidate in candidates:
        if candidate.kind is ChunkKind.CHUNK:
            count = seen_chunk_count.get(candidate.node_id, 0)
            if count >= cap:
                continue
            seen_chunk_count[candidate.node_id] = count + 1
        kept.append(candidate)
    return kept


def _dedupe_variants(variants: list[str]) -> list[str]:
    """Drop empty / duplicate query variants (case- and whitespace-insensitive)
    while preserving the original order. The expander sometimes emits the raw
    query alongside a paraphrase that collapses to the same normalized form,
    which would otherwise double the RPC load and inflate consensus boosts."""
    seen: set[str] = set()
    kept: list[str] = []
    for variant in variants or []:
        if not variant or not str(variant).strip():
            continue
        normalized = _normalize_for_match(variant)
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(variant)
    return kept


def _normalize_for_match(text: str) -> str:
    """Lowercase and collapse whitespace so title matching is punctuation-
    insensitive without requiring exact casing from the user's query."""
    import re
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _title_match_boost(name: str, normalized_variants: list[str]) -> float:
    """Return a boost if any query variant appears as a substring of the
    candidate's name (or vice-versa for short names). Boost is graded so
    full equality beats partial containment."""
    if not name or not normalized_variants:
        return 0.0
    normalized_name = _normalize_for_match(name)
    if not normalized_name:
        return 0.0
    best = 0.0
    for variant in normalized_variants:
        if not variant:
            continue
        if variant == normalized_name:
            best = max(best, 0.40)
        elif variant in normalized_name or normalized_name in variant:
            # Partial containment — meaningful when user paraphrases a title.
            ratio = min(len(variant), len(normalized_name)) / max(
                len(variant), len(normalized_name)
            )
            best = max(best, 0.20 * ratio)
    return best


def _recency_boost(metadata: dict | None, query_class: QueryClass) -> float:
    """Return a small positive score boost for chunks whose source content is
    recent. Pure helper — no I/O, never raises, never returns negative.

    The chunk-date field is read from ``metadata['timestamp']`` first, falling
    back to ``metadata['time_span']['end']`` so that both per-chunk timestamps
    and aggregate node-level spans are honored. Anything missing or
    unparseable yields 0.0. Future-dated chunks also yield 0.0 (so a clock
    skew can never penalise — and never inflate — a candidate).

    Magnitude: linear decay over a 730-day (~2yr) window. Per-class scale is
    0.10 for LOOKUP / VAGUE (recency matters most for "what / when" lookups
    and ambiguous queries) and 0.05 for THEMATIC / MULTI_HOP / STEP_BACK
    (older canonical content shouldn't be penalised heavily for synthesis
    queries). Returned value is bounded by ``scale`` so it never overpowers
    the base RRF score.
    """
    if not metadata:
        return 0.0
    ts = metadata.get("timestamp") or (metadata.get("time_span") or {}).get("end")
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - dt).days
    if age_days < 0:
        return 0.0
    scale = 0.10 if query_class in (QueryClass.LOOKUP, QueryClass.VAGUE) else 0.05
    return scale * max(0.0, 1.0 - age_days / 730.0)


# Spec 2B.1 / iter-03 plan §3.7: action-verb regex. When a LOOKUP query
# contains an action verb (build, install, set up, deploy, ...), the user is
# almost always looking for executable / step-by-step content. GitHub repos
# and generic web (docs, tutorials) tend to carry that; newsletter/YouTube
# usually carry editorial / discussion content. Small magnitudes — see boost
# table below — so the action-verb signal nudges without overpowering RRF.
_ACTION_VERBS_RE = re.compile(
    r"\b(build|start|open|run|install|set\s+up|spin\s+up|deploy|configure|create|launch|bootstrap|try|use)\b",
    re.IGNORECASE,
)


def _source_type_boost(
    *,
    base_score: float,
    source_type: str,
    query_class,
    question: str,
) -> float:
    """Return ``base_score`` adjusted by source-type / query-class affinities.

    Pure helper — no I/O, never raises. Returns the *new* score (NOT a delta)
    so callers can adopt the result directly. Magnitudes are deliberately small
    (0.02-0.05) so they nudge ordering without overpowering base RRF / title /
    recency signals.

    Affinities applied (cumulative — they can stack on the same candidate):

    1. Class-specific source affinity (legacy T10):
       - THEMATIC / STEP_BACK + youtube  -> +0.03 (long-form discussion content)
       - LOOKUP + reddit                 -> +0.02 (concrete Q&A)

    2. Action-verb affinity (spec 2B.1 / iter-03 plan §3.7) — only when the
       query is LOOKUP-class and contains an action verb (build, install, set
       up, deploy, ...):
       - github / web        -> +0.05  (step-by-step / docs / tutorials)
       - newsletter / youtube -> -0.02  (editorial / discussion, less actionable)
    """
    score = float(base_score)

    st = str(source_type or "").lower()

    # 1. Class-specific source affinity (legacy T10 behavior, preserved).
    qc = query_class
    qc_value = getattr(qc, "value", qc)
    qc_str = str(qc_value or "").lower()
    if qc_str in ("thematic", "step_back") and st == "youtube":
        score += 0.03
    if qc_str == "lookup" and st == "reddit":
        score += 0.02

    # 2. Action-verb affinity (spec 2B.1).
    if qc_str == "lookup" and _ACTION_VERBS_RE.search(question or ""):
        if st in ("github", "web"):
            score += 0.05
        elif st in ("newsletter", "youtube"):
            score -= 0.02

    return score


def _author_match_boost(candidate: RetrievalCandidate, query_meta) -> float:
    """Return a small boost when a query mentions an author/channel that the
    candidate is attributed to.

    Pure helper — no I/O, never raises, never returns negative. The candidate's
    attribution is read from ``metadata['author']`` first, falling back to
    ``metadata['channel']`` for sources (YouTube, podcasts) where the channel
    name is the canonical attribution. Match is a case-insensitive substring
    check in either direction so "karpathy" in the query matches an
    "Andrej Karpathy" attribution. The boost is a single 0.05 — never summed
    across multiple author hits — so it stays bounded and idempotent.
    """
    if candidate is None or not query_meta:
        return 0.0
    authors = getattr(query_meta, "authors", None) or []
    if not authors:
        return 0.0
    md = candidate.metadata or {}
    cand_author = md.get("author") or md.get("channel")
    if not cand_author:
        return 0.0
    cand_lower = str(cand_author).lower()
    for qa in authors:
        if not qa:
            continue
        if str(qa).lower() in cand_lower:
            return 0.05
    return 0.0


def _row_to_candidate(row: dict) -> RetrievalCandidate:
    source_value = str(row.get("source_type") or "web").lower()
    try:
        source_type = SourceType(source_value)
    except ValueError:
        source_type = SourceType.WEB
    kind_value = str(row.get("kind") or "chunk").lower()
    kind = ChunkKind.SUMMARY if kind_value == "summary" else ChunkKind.CHUNK
    return RetrievalCandidate(
        kind=kind,
        node_id=row["node_id"],
        chunk_id=row.get("chunk_id"),
        chunk_idx=int(row.get("chunk_idx") or 0),
        name=str(row.get("name") or row.get("title") or row["node_id"]),
        source_type=source_type,
        url=str(row.get("url") or ""),
        content=str(row.get("content") or row.get("summary") or ""),
        tags=list(row.get("tags") or []),
        metadata=dict(row.get("metadata") or {}),
        rrf_score=float(row.get("rrf_score") or 0.0),
    )

