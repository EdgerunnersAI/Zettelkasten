"""WAVE-C kg_features gap-fill — KF-DRIFT-A drift sentinel.

Re-scoring stored vectors with a new embedding model produces edges that
overlap >=80% with the prior model's top-K (drift sentinel). When the
overlap drops below 80% we have evidence the new model is materially
re-shaping the graph and a backfill of stored vectors is required before
serving the new model alongside the old one.

Pure unit test — no DB, no network. Two stub embedding generators ("model
A" / "model B") produce slightly-perturbed vectors for the same node id,
then we score every (source, candidate) pair with
``compute_connection_strength`` and rank top-K candidates per source. The
overlap is taken across all sources and asserted >= 0.80 per the drift
sentinel threshold.

The threshold (80%) and rationale match standard semantic-search drift
monitoring practice: minor model upgrades should preserve top-K neighbour
identity; major upgrades drop overlap and require a backfill.
"""
from __future__ import annotations

import hashlib
import math
import random

import pytest

from website.features.kg_features.scoring import compute_connection_strength


# ── Stub embedding generators ───────────────────────────────────────────


def _stub_embed(*, model: str, node_id: str, dim: int = 64) -> list[float]:
    """Deterministic per-(model, node_id) embedding.

    Both models share the *node* component (so semantically-similar nodes
    stay similar across models) but XOR in a model-specific salt so the
    vectors are not identical. This mirrors the real-world drift pattern:
    same content, slightly different geometry.
    """
    seed_bytes = hashlib.sha256(f"{model}|{node_id}".encode()).digest()
    rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
    # Shared semantic core (perturbed slightly per model). 0.05 sigma keeps
    # the "minor model upgrade" simulation realistic: same neighbour set with
    # mild rank-order shuffles, mirroring an embeddings model point-release.
    core_bytes = hashlib.sha256(f"node|{node_id}".encode()).digest()
    core_rng = random.Random(int.from_bytes(core_bytes[:8], "big"))
    core = [core_rng.gauss(0.0, 1.0) for _ in range(dim)]
    perturbation = [rng.gauss(0.0, 0.05) for _ in range(dim)]
    raw = [c + p for c, p in zip(core, perturbation)]
    norm = math.sqrt(sum(v * v for v in raw))
    if norm <= 0.0:
        return raw
    return [v / norm for v in raw]


def _top_k_per_source(
    embeddings: dict[str, list[float]],
    *,
    k: int,
) -> dict[str, list[str]]:
    """For each node, rank every other node by ``compute_connection_strength``
    and return the top-K candidate ids.

    Tags / structural / temporal signals are deliberately empty so the
    embedding signal dominates — that's the surface KF-DRIFT-A is testing.
    """
    nodes = sorted(embeddings.keys())
    result: dict[str, list[str]] = {}
    for src in nodes:
        scored: list[tuple[float, str]] = []
        for cand in nodes:
            if cand == src:
                continue
            score = compute_connection_strength(
                src,
                cand,
                embeddings=embeddings,
                tags={},
                structural={},
                temporal_days=0.0,
            )
            scored.append((score, cand))
        # Stable sort: higher score first, lexicographic tiebreak on id.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        result[src] = [cand for _, cand in scored[:k]]
    return result


# ── KF-DRIFT-A ──────────────────────────────────────────────────────────


def test_top_k_overlap_at_least_80pct_across_models() -> None:
    """KF-DRIFT-A: with N=20 nodes, top-K=5, the union of (source, neighbour)
    pairs from model A's top-K and model B's top-K must overlap >= 80%.

    Counts pairs (not raw nodes) so the assertion is symmetric and ignores
    rank-order shuffles within the K cap — what matters is whether the
    *neighbour set* is preserved, which is the metric a drift monitor would
    actually alert on.
    """
    n = 20
    k = 5
    node_ids = [f"n{i:02d}" for i in range(n)]

    emb_a = {nid: _stub_embed(model="A", node_id=nid) for nid in node_ids}
    emb_b = {nid: _stub_embed(model="B", node_id=nid) for nid in node_ids}

    top_a = _top_k_per_source(emb_a, k=k)
    top_b = _top_k_per_source(emb_b, k=k)

    pairs_a = {(src, nbr) for src, nbrs in top_a.items() for nbr in nbrs}
    pairs_b = {(src, nbr) for src, nbrs in top_b.items() for nbr in nbrs}

    intersection = pairs_a & pairs_b
    union = pairs_a | pairs_b
    overlap = len(intersection) / len(union) if union else 0.0

    assert overlap >= 0.80, (
        f"top-K drift overlap {overlap:.3f} < 0.80 — backfill of stored "
        f"vectors REQUIRED before serving model B alongside model A. "
        f"|A|={len(pairs_a)} |B|={len(pairs_b)} |A∩B|={len(intersection)}"
    )


def test_top_k_overlap_unchanged_for_same_model_is_one() -> None:
    """Sanity / control: scoring twice with the same model is deterministic
    (the scorer is pure), so the overlap MUST be exactly 1.0. Catches a
    regression where the helper accidentally introduces non-determinism.
    """
    n = 12
    k = 4
    node_ids = [f"n{i:02d}" for i in range(n)]

    emb = {nid: _stub_embed(model="A", node_id=nid) for nid in node_ids}

    top1 = _top_k_per_source(emb, k=k)
    top2 = _top_k_per_source(emb, k=k)

    assert top1 == top2, (
        "Top-K rankings must be deterministic for identical inputs"
    )

    pairs1 = {(s, n) for s, ns in top1.items() for n in ns}
    pairs2 = {(s, n) for s, ns in top2.items() for n in ns}
    assert pairs1 == pairs2 and len(pairs1) > 0


def test_top_k_overlap_drops_below_threshold_with_random_model() -> None:
    """Negative control: a "model" whose embeddings are pure random noise
    (no shared semantic core) should drop the overlap below the 0.80 drift
    threshold — proving the sentinel can actually detect drift.

    Without this control the positive test could pass trivially if the
    scoring formula were degenerate.
    """
    n = 20
    k = 5
    node_ids = [f"n{i:02d}" for i in range(n)]

    emb_a = {nid: _stub_embed(model="A", node_id=nid) for nid in node_ids}
    # "Random" model: per-call RNG, no shared semantic core with model A.
    rng = random.Random(99)
    def _random_emb() -> list[float]:
        raw = [rng.gauss(0.0, 1.0) for _ in range(64)]
        norm = math.sqrt(sum(v * v for v in raw))
        return [v / norm for v in raw] if norm > 0 else raw

    emb_random = {nid: _random_emb() for nid in node_ids}

    top_a = _top_k_per_source(emb_a, k=k)
    top_r = _top_k_per_source(emb_random, k=k)

    pairs_a = {(s, n) for s, ns in top_a.items() for n in ns}
    pairs_r = {(s, n) for s, ns in top_r.items() for n in ns}
    overlap = (
        len(pairs_a & pairs_r) / len(pairs_a | pairs_r)
        if (pairs_a | pairs_r) else 0.0
    )

    assert overlap < 0.80, (
        f"Drift sentinel false-negative: random-model overlap {overlap:.3f} "
        "should be < 0.80 to prove the metric can detect drift."
    )
