"""iter-12 R4: per-Kasten Thompson-sampling bandit for anchor-seed floor.

Production-safety design (R4-followup validated 2026-05-07):
- Decay γ=0.98/day (Garivier-Moulines DS-UCB regime; NOT 0.9 weekly).
- Pool-size stratification (S<30, M<80, L≥80) removes Kasten-size confound.
- Informative prior from static-0.30 historical win-rate (NOT Beta(2,2)).
- Per-Kasten kill switch column (bandit_disabled_at; NOT global env only).
- Per-request θ sampling (NOT cached — caching breaks TS exploration).
- Atomic Postgres INSERT...ON CONFLICT...DO UPDATE for concurrent writes.
- Hard floor at 0.25 preserves CE-reranker primacy (lowest configured arm).
- N_min=20 cold-start gate falls back to static 0.30.
- DB-unreachable → fail-open (static fallback; zero query-stall risk).
"""
from __future__ import annotations

import logging
import math
import os
import random
from typing import Any

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

_log = logging.getLogger("rag.anchor_seed_bandit")

# ---------------------------------------------------------------------------
# Module-level constants (all overridable via env vars for ops convenience)
# ---------------------------------------------------------------------------
_BANDIT_ENABLED: bool = os.environ.get(
    "RAG_ANCHOR_BANDIT_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")

# Arms are the candidate floor values. Hard floor at 0.25 is the minimum.
_ARMS: list[float] = [
    float(a)
    for a in os.environ.get("RAG_ANCHOR_BANDIT_ARMS", "0.25,0.30,0.35,0.40").split(",")
]

# Cold-start gate: require ≥N_min total pulls across all arms before sampling.
_N_MIN_PULLS: int = int(os.environ.get("RAG_ANCHOR_BANDIT_N_MIN", "20"))

# Static fallback mirrors the existing env var so Day-1-3 behavior is identical
# to pre-bandit behavior.
_STATIC_FALLBACK: float = float(os.environ.get("RAG_ANCHOR_SEED_FLOOR_RRF", "0.30"))

# Rerank top-K used to determine seed survival for reward.
_FINAL_TOP_K: int = int(os.environ.get("RAG_FINAL_TOP_K", "8"))

# Hard timeout for the bandit DB read (ms); enforces 5ms decision latency cap.
_BANDIT_READ_TIMEOUT_S: float = float(os.environ.get("RAG_ANCHOR_BANDIT_TIMEOUT_MS", "5")) / 1000.0


def bucket_pool_size(n: int) -> str:
    """Map candidate pool size to stratification bucket: S, M, or L.

    S (<30): sparse Kastens — small enough that a high floor risks crowding out
             genuine signal; conservative arm range matters.
    M (<80): typical single-topic Kastens.
    L (≥80): dense multi-topic Kastens where the floor has more slack.
    """
    if n < 30:
        return "S"
    if n < 80:
        return "M"
    return "L"


async def sample_floor(
    *,
    p_user_id: str,
    kasten_id: str,
    pool_size: int,
    supabase: Any,
) -> tuple[float, dict]:
    """Return (floor_value, telemetry_dict) for one request.

    Falls back to _STATIC_FALLBACK when:
    1. Global flag off (RAG_ANCHOR_BANDIT_ENABLED=false).
    2. Per-Kasten kill switch active (bandit_disabled_at IS NOT NULL).
    3. Cold-start: total pulls across arms < _N_MIN_PULLS.
    4. DB unreachable (fail-open — zero behavior change on error).
    5. No rows for this (user, kasten, bucket) yet.

    Per-request θ draw — never cached; caching breaks TS exploration guarantee.
    """
    bucket = bucket_pool_size(pool_size)
    telemetry: dict = {
        "p_user_id": p_user_id,
        "kasten_id": kasten_id,
        "pool_bucket": bucket,
        "pool_size": pool_size,
        "fallback_reason": None,
        "arm_sampled": None,
        "alpha_at_sample": None,
        "beta_at_sample": None,
        "theta_drawn": None,
        "posterior_entropy_nats": None,
    }

    if not _BANDIT_ENABLED:
        telemetry["fallback_reason"] = "global_flag_off"
        return _STATIC_FALLBACK, telemetry

    # DB read with hard timeout to enforce 5ms decision latency cap.
    try:
        import asyncio
        rows_resp = await asyncio.wait_for(
            rpc_call(supabase.rpc(
                "rag_bandit_read_arms",
                {
                    "p_user_id": p_user_id,
                    "p_kasten_id": kasten_id,
                    "p_bucket": bucket,
                },
            )),
            timeout=_BANDIT_READ_TIMEOUT_S,
        )
        rows: list[dict] = list(rows_resp.data or [])
    except Exception as exc:
        _log.warning(
            "bandit_read_failed kasten=%s err=%s", kasten_id, type(exc).__name__
        )
        telemetry["fallback_reason"] = "db_unreachable"
        return _STATIC_FALLBACK, telemetry

    # No rows yet → cold start for this Kasten/bucket combination.
    if not rows:
        telemetry["fallback_reason"] = "no_rows"
        return _STATIC_FALLBACK, telemetry

    # Per-Kasten kill switch: any row carrying bandit_disabled_at triggers it.
    if rows[0].get("bandit_disabled_at"):
        telemetry["fallback_reason"] = "kasten_kill_switch"
        return _STATIC_FALLBACK, telemetry

    # Cold-start gate: require enough total pulls for reliable posteriors.
    total_pulls = sum(int(r.get("seed_total_pulls") or 0) for r in rows)
    if total_pulls < _N_MIN_PULLS:
        telemetry["fallback_reason"] = "cold_start"
        return _STATIC_FALLBACK, telemetry

    # Build arm → (α, β) map from DB rows; missing arms get uniform prior.
    arm_to_params: dict[float, tuple[float, float]] = {
        float(r["seed_arm"]): (float(r["seed_alpha"]), float(r["seed_beta"]))
        for r in rows
        if r.get("seed_arm") is not None
    }

    # Per-request θ draw: sample each arm independently from Beta(α, β).
    samples: list[tuple[float, float, float, float]] = []
    for arm in _ARMS:
        alpha, beta = arm_to_params.get(arm, (1.0, 1.0))
        theta = random.betavariate(alpha, beta)
        samples.append((arm, theta, alpha, beta))

    arm_chosen, theta_max, alpha_at, beta_at = max(samples, key=lambda x: x[1])

    # Posterior entropy as a pathology metric (uniform = 1.39 nats).
    arm_means = [a / (a + b) for _, _, a, b in samples]
    total_mean = sum(arm_means) or 1.0
    probs = [m / total_mean for m in arm_means]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)

    telemetry.update({
        "arm_sampled": arm_chosen,
        "alpha_at_sample": alpha_at,
        "beta_at_sample": beta_at,
        "theta_drawn": theta_max,
        "posterior_entropy_nats": round(entropy, 4),
    })
    return arm_chosen, telemetry


async def record_outcome(
    *,
    p_user_id: str,
    kasten_id: str,
    arm: float,
    pool_bucket: str,
    seed_survived: bool,
    supabase: Any,
) -> None:
    """Atomic UPSERT: increment α (survived) or β (dropped) for the chosen arm.

    Uses INSERT...ON CONFLICT...DO UPDATE so concurrent writes never race on a
    SELECT-then-UPDATE pattern. Fail-open: errors are logged but never propagated
    so a DB hiccup cannot affect the response path.
    """
    try:
        await rpc_call(supabase.rpc(
            "rag_bandit_record_outcome",
            {
                "p_user_id": p_user_id,
                "p_kasten_id": kasten_id,
                "p_arm": arm,
                "p_bucket": pool_bucket,
                "p_reward": 1 if seed_survived else 0,
            },
        ))
    except Exception as exc:
        _log.warning(
            "bandit_record_failed kasten=%s arm=%.2f err=%s",
            kasten_id,
            arm,
            type(exc).__name__,
        )
        # Fail-open: posterior update lost for this one observation; no crash.
