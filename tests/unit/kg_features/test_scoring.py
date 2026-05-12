"""WAVE-C 1c-A.2 — Unit tests for multi-signal connection strength scorer.

Locked decisions covered:
- D-KG-1 weights: embedding=0.55 + tag=0.25 + structural=0.15 + temporal=0.05
- D-KG-2 edge-creation threshold: ≥ 0.55
- Per-node neighborhood percentile rank (NOT global percentile)
- Pure function: no DB / no network / no global state
"""
from __future__ import annotations

import math

import pytest

from website.features.kg_features.scoring import (
    WEIGHTS,
    EDGE_CREATION_THRESHOLD,
    EDGE_RENDER_THRESHOLD,
    compute_connection_strength,
    percentile_rank,
)


# ── Invariants ──────────────────────────────────────────────────────────


def test_weights_sum_to_one() -> None:
    """D-KG-1 invariant: all four signal weights must sum to exactly 1.0."""
    assert math.isclose(sum(WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_weights_match_locked_decision() -> None:
    """Pin the exact D-KG-1 weights so future edits trip CI."""
    assert WEIGHTS == {
        "embedding": 0.55,
        "tag": 0.25,
        "structural": 0.15,
        "temporal": 0.05,
    }


def test_thresholds_match_locked_decisions() -> None:
    """D-KG-2 (edge create ≥ 0.55) and D-KG-3 (edge render ≥ 0.7)."""
    assert EDGE_CREATION_THRESHOLD == 0.55
    assert EDGE_RENDER_THRESHOLD == 0.7


# ── Determinism ─────────────────────────────────────────────────────────


def test_score_is_deterministic() -> None:
    """Same inputs → same output, every time. Pure function invariant."""
    a = "node-a"
    b = "node-b"
    s1 = compute_connection_strength(
        a,
        b,
        embeddings={"node-a": [0.1, 0.2, 0.3], "node-b": [0.1, 0.2, 0.3]},
        tags={"node-a": ["python"], "node-b": ["python", "fastapi"]},
        structural={"node-a": {"node-b": 1}, "node-b": {"node-a": 1}},
        temporal_days=3.0,
    )
    s2 = compute_connection_strength(
        a,
        b,
        embeddings={"node-a": [0.1, 0.2, 0.3], "node-b": [0.1, 0.2, 0.3]},
        tags={"node-a": ["python"], "node-b": ["python", "fastapi"]},
        structural={"node-a": {"node-b": 1}, "node-b": {"node-a": 1}},
        temporal_days=3.0,
    )
    assert s1 == s2


def test_score_in_unit_interval() -> None:
    """Output must always be in [0, 1] regardless of input magnitudes."""
    score = compute_connection_strength(
        "a",
        "b",
        embeddings={"a": [1.0, 0.0], "b": [1.0, 0.0]},
        tags={"a": ["x"], "b": ["x"]},
        structural={"a": {"b": 100}, "b": {"a": 100}},
        temporal_days=0.0,
    )
    assert 0.0 <= score <= 1.0


# ── Graceful degradation ────────────────────────────────────────────────


def test_empty_tags_does_not_raise() -> None:
    """No tags on either side → tag signal contributes 0 (Jaccard = 0/0 → 0)."""
    score = compute_connection_strength(
        "a",
        "b",
        embeddings={"a": [1.0, 0.0], "b": [0.0, 1.0]},
        tags={"a": [], "b": []},
        structural={"a": {}, "b": {}},
        temporal_days=365.0,
    )
    # Orthogonal embeddings + no tags + no co-occurrence + far apart → tiny
    assert score >= 0.0
    assert score < 0.3


def test_missing_node_in_embeddings_returns_zero_signal() -> None:
    """Missing embedding entry → embedding signal degrades to 0, not crash."""
    score = compute_connection_strength(
        "a",
        "b",
        embeddings={},
        tags={"a": ["python"], "b": ["python"]},
        structural={},
        temporal_days=0.0,
    )
    # Tag perfect (Jaccard=1.0) + temporal=1.0 contribute 0.25 + 0.05 = 0.3
    assert 0.25 <= score <= 0.35


def test_missing_embedding_dim_mismatch_returns_zero_signal() -> None:
    """Embedding length mismatch → safe-zero, not numpy broadcasting error."""
    score = compute_connection_strength(
        "a",
        "b",
        embeddings={"a": [1.0, 0.0], "b": [1.0, 0.0, 0.0]},  # mismatched length
        tags={"a": ["x"], "b": ["x"]},
        structural={"a": {}, "b": {}},
        temporal_days=0.0,
    )
    assert 0.0 <= score <= 1.0


# ── Component contributions ────────────────────────────────────────────


def test_identical_embeddings_max_embedding_signal() -> None:
    """Cosine(v, v) = 1.0 → embedding signal contributes 0.55."""
    score = compute_connection_strength(
        "a",
        "b",
        embeddings={"a": [0.6, 0.8], "b": [0.6, 0.8]},
        tags={"a": [], "b": []},
        structural={"a": {}, "b": {}},
        temporal_days=365.0,
    )
    # Embedding contributes 0.55; tag=0; structural=0; temporal ~ tiny
    assert 0.5 <= score <= 0.6


def test_identical_tag_set_max_tag_signal() -> None:
    """Jaccard({a,b,c}, {a,b,c}) = 1.0 → tag signal contributes 0.25."""
    score = compute_connection_strength(
        "n1",
        "n2",
        embeddings={},
        tags={"n1": ["a", "b", "c"], "n2": ["a", "b", "c"]},
        structural={},
        temporal_days=365.0,
    )
    # Only tag fires (~0.25) + small temporal residual
    assert 0.2 <= score <= 0.3


def test_temporal_decay_recent_higher() -> None:
    """Same-day temporal signal ≥ year-old temporal signal."""
    same_day = compute_connection_strength(
        "a",
        "b",
        embeddings={},
        tags={},
        structural={},
        temporal_days=0.0,
    )
    year_old = compute_connection_strength(
        "a",
        "b",
        embeddings={},
        tags={},
        structural={},
        temporal_days=365.0,
    )
    assert same_day >= year_old


# ── Percentile rank (per-node neighborhood) ───────────────────────────


def test_percentile_rank_monotonic() -> None:
    """Larger value → larger percentile within the same neighborhood."""
    neighborhood = [0.1, 0.2, 0.3, 0.4, 0.5]
    ranks = [percentile_rank(v, neighborhood) for v in neighborhood]
    assert ranks == sorted(ranks)


def test_percentile_rank_bounds() -> None:
    """Rank ∈ [0, 1]; lowest → 0.0; highest → 1.0."""
    neighborhood = [0.1, 0.5, 0.9]
    assert percentile_rank(0.1, neighborhood) == pytest.approx(0.0)
    assert percentile_rank(0.9, neighborhood) == pytest.approx(1.0)


def test_percentile_rank_empty_neighborhood_returns_zero() -> None:
    """No peers → rank 0.0 (no signal); never NaN / divide-by-zero."""
    assert percentile_rank(0.5, []) == 0.0


def test_percentile_rank_singleton_returns_zero() -> None:
    """One-peer neighborhood (the value itself) → rank 0.0 (no relative info)."""
    assert percentile_rank(0.42, [0.42]) == 0.0


# ── Edge-creation gate ────────────────────────────────────────────────


def test_below_creation_threshold_predicate_excludes() -> None:
    """Score < 0.55 must NOT qualify for edge creation."""
    assert 0.54 < EDGE_CREATION_THRESHOLD
    assert not (0.54 >= EDGE_CREATION_THRESHOLD)


def test_at_creation_threshold_predicate_includes() -> None:
    """Score == 0.55 IS the creation cutoff (≥, not >)."""
    assert 0.55 >= EDGE_CREATION_THRESHOLD


def test_render_threshold_strict_subset_of_creation() -> None:
    """Render threshold (0.7) > creation threshold (0.55) by construction."""
    assert EDGE_RENDER_THRESHOLD > EDGE_CREATION_THRESHOLD
