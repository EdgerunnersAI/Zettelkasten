"""WAVE-C 1c-A.2 — Multi-signal connection-strength scorer.

Pure functions only. No DB / no network / no global mutation. The scorer
combines four signals into a single ``connection_strength`` value in [0, 1]
per locked decision **D-KG-1**::

    score = 0.55 * embedding + 0.25 * tag + 0.15 * structural + 0.05 * temporal

Locked thresholds:
- D-KG-2 edge-creation threshold: ``EDGE_CREATION_THRESHOLD = 0.55`` (>=)
- D-KG-3 edge-render threshold:   ``EDGE_RENDER_THRESHOLD = 0.7``    (>=)

Caller contract:
- ``embeddings``: ``{node_id: list[float]}``. Missing IDs / mismatched dims
  degrade the embedding signal to 0 silently — never raise.
- ``tags``:       ``{node_id: list[str]}``. Empty sets degrade the tag
  signal to 0 (Jaccard 0/0 → 0).
- ``structural``: ``{node_id: {neighbor_id: cooccurrence_count}}``. Missing
  IDs degrade to 0.
- ``temporal_days``: float, distance between node creation timestamps in
  days. Exponential decay; 0 days → 1.0; ~30 days → ~0.37.

The scorer's purity makes it cheap to call inline in the per-edge create
path (``persist_summarized_result``) AND in the offline backfill scripts
(``ops/scripts/backfill_links.py``).
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

# ── Locked decisions (D-KG-1 / D-KG-2 / D-KG-3) ──────────────────────────────

WEIGHTS: dict[str, float] = {
    "embedding": 0.55,
    "tag": 0.25,
    "structural": 0.15,
    "temporal": 0.05,
}

EDGE_CREATION_THRESHOLD: float = 0.55
EDGE_RENDER_THRESHOLD: float = 0.7

# Temporal half-life in days. exp(-days / 30) → ~0.37 at 30d, ~0.018 at 120d.
_TEMPORAL_HALFLIFE_DAYS: float = 30.0

__all__ = [
    "WEIGHTS",
    "EDGE_CREATION_THRESHOLD",
    "EDGE_RENDER_THRESHOLD",
    "compute_connection_strength",
    "percentile_rank",
]


# ── Per-signal kernels ──────────────────────────────────────────────────────


def _cosine_similarity(va: Sequence[float], vb: Sequence[float]) -> float:
    """Dim-mismatch / empty / zero-norm safe cosine sim mapped to [0, 1].

    Real cosine ranges [-1, 1]; we shift to [0, 1] so it composes linearly
    with the other signals. Any pathological input (length mismatch, zero
    vector, NaN) collapses to 0.0 silently — the score is a *signal*, not
    a numeric promise.
    """
    if not va or not vb or len(va) != len(vb):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for ai, bi in zip(va, vb):
        dot += ai * bi
        na += ai * ai
        nb += bi * bi
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    cos = dot / math.sqrt(na * nb)
    if math.isnan(cos):
        return 0.0
    # Clip + shift to [0, 1]
    cos = max(-1.0, min(1.0, cos))
    return (cos + 1.0) / 2.0


def _jaccard(set_a: Iterable[str], set_b: Iterable[str]) -> float:
    """Jaccard similarity on tag sets. Empty ∪ empty → 0.0 (no signal)."""
    sa = {t for t in set_a if t}
    sb = {t for t in set_b if t}
    if not sa and not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return inter / union


def _structural_signal(
    a: str,
    b: str,
    structural: Mapping[str, Mapping[str, int]],
) -> float:
    """Co-occurrence-based structural signal mapped to [0, 1].

    Reads the symmetric pair count ``structural[a][b]`` (falling back to
    ``structural[b][a]``) and squashes via ``count / (count + k)`` with
    ``k=2`` so a single co-occurrence registers, but the signal saturates
    smoothly above ~5 co-occurrences without ever exceeding 1.0.
    """
    count_ab = structural.get(a, {}).get(b, 0) if structural else 0
    count_ba = structural.get(b, {}).get(a, 0) if structural else 0
    count = max(int(count_ab), int(count_ba))
    if count <= 0:
        return 0.0
    return count / (count + 2.0)


def _temporal_signal(temporal_days: float) -> float:
    """Exponential decay with ~30d half-life: 1.0 same day → ~0.37 @ 30d."""
    if temporal_days is None:
        return 0.0
    days = max(0.0, float(temporal_days))
    return math.exp(-days / _TEMPORAL_HALFLIFE_DAYS)


# ── Public API ──────────────────────────────────────────────────────────────


def compute_connection_strength(
    node_a: str,
    node_b: str,
    *,
    embeddings: Mapping[str, Sequence[float]] | None = None,
    tags: Mapping[str, Sequence[str]] | None = None,
    structural: Mapping[str, Mapping[str, int]] | None = None,
    temporal_days: float = 0.0,
) -> float:
    """Combined multi-signal connection score in [0, 1] per D-KG-1.

    Pure / deterministic: same inputs always produce the same output. Never
    raises on missing keys, dim mismatches, empty containers, or zero
    vectors — pathological inputs silently degrade the offending signal to
    0 so the caller can score every candidate pair without try/except.
    """
    embeddings = embeddings or {}
    tags = tags or {}
    structural = structural or {}

    emb = _cosine_similarity(
        list(embeddings.get(node_a, ())),
        list(embeddings.get(node_b, ())),
    )
    tag = _jaccard(tags.get(node_a, ()), tags.get(node_b, ()))
    struct = _structural_signal(node_a, node_b, structural)
    temp = _temporal_signal(temporal_days)

    score = (
        WEIGHTS["embedding"] * emb
        + WEIGHTS["tag"] * tag
        + WEIGHTS["structural"] * struct
        + WEIGHTS["temporal"] * temp
    )
    # Defensive clamp — weights sum to 1.0 by construction so the result is
    # already in [0, 1], but guard against future weight edits.
    return max(0.0, min(1.0, score))


def percentile_rank(value: float, neighborhood: Sequence[float]) -> float:
    """Per-node-neighborhood percentile rank in [0, 1].

    Uses the "fraction of strict-less-than peers" definition (a.k.a.
    "competition rank, normalized"). Stable for tied values and well-defined
    on small neighborhoods.

    Edge cases:
    - Empty / singleton neighborhoods → 0.0 (no relative information).
    - Used by the scorer's caller to *normalize* candidate scores against
      the source node's own neighborhood before applying the
      EDGE_CREATION_THRESHOLD — keeps a sparsely-connected node's edges
      from being unfairly culled by the global threshold.
    """
    if not neighborhood or len(neighborhood) <= 1:
        return 0.0
    strict_less = sum(1 for x in neighborhood if x < value)
    return strict_less / (len(neighborhood) - 1)
