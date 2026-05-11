"""P2.5 — Kasten signal boost end-to-end assertion.

The existing `test_retrieval_signals_e2e.py` proves the MV gets populated.
This test closes the silent-no-op gap: that the populated MV actually
mutates `rrf_score` when `_apply_kasten_signal_boost` runs.

Approach (unit-shape integration test):
  1. Seed >= 50 cite events on (anchor, target_boosted) via direct asyncpg
     INSERTs into rag.retrieval_feedback_events (bypasses /api/rag/feedback +
     Gemini; the endpoint round-trip is covered by test_retrieval_signals_e2e).
  2. Force MV refresh via SQL (`kg.refresh_signal_mv(...)`).
  3. Sanity-probe the MV row to confirm event_count >= cold-start threshold —
     without this, a silent cold-start skip would falsely pass the boost test.
  4. Read signals_by_pair from MV (mirrors what `_fetch_kasten_signals` builds).
  5. Construct two synthetic RetrievalCandidate objects with identical
     rrf_score: one targets the boosted edge, one a control with no signals.
  6. Call `_apply_kasten_signal_boost` in place; assert strict score delta
     for boosted candidate and zero delta for control.
  7. Cold-start regression: re-run with total_event_count=49 → boost no-ops.

This covers the boost path WITHOUT firing the full HybridRetriever pipeline
(Gemini, embeddings, cross-encoder). The full e2e flow is covered by
`test_retrieval_signals_e2e.py` + RAG smoke probe.
"""
from __future__ import annotations

import asyncio
import math
import uuid

import asyncpg
import pytest

from website.features.rag_pipeline.retrieval.hybrid import (
    _KASTEN_SIGNAL_BOOST_SCALE,
    _KASTEN_SIGNAL_COLD_START_THRESHOLD,
    _apply_kasten_signal_boost,
    _compute_kasten_signal_boost,
)
from website.features.rag_pipeline.types import ChunkKind, RetrievalCandidate, SourceType

pytestmark = pytest.mark.live


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _seed_kasten(pool: asyncpg.Pool, workspace_id: uuid.UUID) -> uuid.UUID:
    kid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.kastens (id, workspace_id, name) VALUES ($1, $2, $3)",
            kid, workspace_id, f"K-boost-{uuid.uuid4().hex[:8]}",
        )
    return kid


async def _seed_feedback_events(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    kasten_id: uuid.UUID,
    profile_id: uuid.UUID,
    source_node: uuid.UUID,
    target_node: uuid.UUID,
    event_type: str,
    n_events: int,
    weight_delta: float = 1.0,
) -> None:
    """Bulk-insert N feedback events on a single (source, target) edge.

    Schema (per supabase/website/_v2/34_retrieval_feedback_events.sql):
      - event_id is BIGINT IDENTITY (auto-gen, omit)
      - user_id is the profile UUID (denormalized nullable per GDPR-pseudonymisation)
    """
    rows = [
        (
            workspace_id, kasten_id, profile_id,
            source_node, target_node,
            event_type,
            weight_delta,
        )
        for _ in range(n_events)
    ]
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO rag.retrieval_feedback_events "
            "(workspace_id, kasten_id, user_id, "
            " source_node_id, target_node_id, event_type, weight_delta) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            rows,
        )


async def _refresh_retrieval_mv(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')"
        )


async def _read_signals(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    kasten_id: uuid.UUID,
    source_node: uuid.UUID,
    target_node: uuid.UUID,
) -> tuple[float, float, int] | None:
    """Returns (positive_signal, negative_signal, event_count) or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT positive_signal, negative_signal, event_count "
            "FROM rag.kasten_retrieval_edge_signals "
            "WHERE workspace_id=$1 AND kasten_id=$2 "
            "AND source_node_id=$3 AND target_node_id=$4",
            workspace_id, kasten_id, source_node, target_node,
        )
        if row is None:
            return None
        return (
            float(row["positive_signal"]),
            float(row["negative_signal"] or 0.0),
            int(row["event_count"]),
        )


async def _read_kasten_event_count(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    kasten_id: uuid.UUID,
) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COALESCE(SUM(event_count), 0) "
            "FROM rag.kasten_retrieval_edge_signals "
            "WHERE workspace_id=$1 AND kasten_id=$2",
            workspace_id, kasten_id,
        )


def _make_candidate(node_id: str, rrf_score: float = 0.10) -> RetrievalCandidate:
    """Minimal synthetic candidate for boost-path tests.

    Only the fields the boost function reads (`node_id`, `rrf_score`) drive
    behaviour; the rest are required-by-schema and get safe sentinels.
    """
    return RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_idx=0,
        name=f"synthetic-{node_id[:8]}",
        source_type=SourceType.WEB,
        url=f"https://synthetic.test/{node_id}",
        content="",
        rrf_score=rrf_score,
    )


def test_kasten_signal_boost_increments_rrf_score(mint_user, asyncpg_pool):
    """Boosted candidate's rrf_score strictly increases; control unchanged.

    Locks the formula tying _compute_kasten_signal_boost(pos, neg) * SCALE to
    the observable rrf_score delta — catches silent regressions where the boost
    becomes a no-op but the surrounding pipeline still appears healthy.
    """
    user = mint_user(workspace_count=1)
    ws = user.workspace_ids[0]
    kasten = _run(_seed_kasten(asyncpg_pool, ws))

    anchor = uuid.uuid4()
    target_boosted = uuid.uuid4()
    target_control = uuid.uuid4()

    # Seed enough cite events to clear cold-start threshold (default 50).
    n_events = _KASTEN_SIGNAL_COLD_START_THRESHOLD
    _run(_seed_feedback_events(
        asyncpg_pool,
        workspace_id=ws,
        kasten_id=kasten,
        profile_id=user.profile_id,
        source_node=anchor,
        target_node=target_boosted,
        event_type="cite",
        n_events=n_events,
        weight_delta=1.0,
    ))

    # Refresh MV synchronously — do NOT rely on pg_cron.
    _run(_refresh_retrieval_mv(asyncpg_pool))

    # Sanity-probe: MV row exists for (anchor, target_boosted) with event_count
    # >= threshold. Without this, a silently-empty MV would mask the boost.
    sig = _run(_read_signals(
        asyncpg_pool,
        workspace_id=ws, kasten_id=kasten,
        source_node=anchor, target_node=target_boosted,
    ))
    assert sig is not None, (
        f"MV row missing after seeding {n_events} cite events — "
        f"check kg.refresh_signal_mv or trigger wiring"
    )
    pos, neg, event_count = sig
    assert event_count >= _KASTEN_SIGNAL_COLD_START_THRESHOLD, (
        f"MV event_count={event_count} below cold-start threshold "
        f"{_KASTEN_SIGNAL_COLD_START_THRESHOLD} — would silent-skip boost"
    )
    assert pos > 0, f"positive_signal={pos} should be > 0 after {n_events} cite events"

    # Kasten-wide event count (the cold-start gate input).
    total_event_count = _run(_read_kasten_event_count(
        asyncpg_pool, workspace_id=ws, kasten_id=kasten,
    ))
    assert total_event_count >= _KASTEN_SIGNAL_COLD_START_THRESHOLD

    # Build signals_by_pair the same shape _fetch_kasten_signals produces:
    # {(source_str, target_str) -> (pos, neg)}
    signals = {(str(anchor), str(target_boosted)): (pos, neg)}

    # Two synthetic candidates with identical starting score.
    cand_boost = _make_candidate(str(target_boosted), rrf_score=0.10)
    cand_control = _make_candidate(str(target_control), rrf_score=0.10)
    before_boost = cand_boost.rrf_score
    before_control = cand_control.rrf_score

    _apply_kasten_signal_boost(
        [cand_boost, cand_control],
        signals,
        anchor_node_ids={str(anchor)},
        total_event_count=total_event_count,
    )

    # Boosted candidate: strict positive delta matching the formula.
    expected_delta = (
        _compute_kasten_signal_boost(pos, neg) * _KASTEN_SIGNAL_BOOST_SCALE
    )
    assert expected_delta > 0, (
        f"_compute_kasten_signal_boost({pos}, {neg}) returned 0 — "
        f"formula regression"
    )
    assert math.isclose(
        cand_boost.rrf_score - before_boost, expected_delta, rel_tol=1e-9
    ), (
        f"boosted delta={cand_boost.rrf_score - before_boost} != "
        f"expected={expected_delta} — formula or scale drifted"
    )

    # Control candidate: untouched (no signal entry for its target).
    assert cand_control.rrf_score == before_control, (
        f"control rrf_score moved ({before_control} -> {cand_control.rrf_score}) "
        f"despite no signal — boost is bleeding into non-target candidates"
    )


def test_kasten_signal_boost_cold_start_gate_blocks_below_threshold(mint_user):
    """When total_event_count < threshold, boost is a no-op even with strong signals.

    This is a pure-function test — no DB needed — but it lives in the live
    suite alongside the positive case so both invariants are exercised together.
    Catches the regression where someone removes the cold-start gate and
    high-variance early signals start mutating scores.
    """
    anchor = str(uuid.uuid4())
    target = str(uuid.uuid4())
    # Strong signal but kasten under cold-start (49 < 50).
    signals = {(anchor, target): (10.0, 0.0)}
    cand = _make_candidate(target, rrf_score=0.10)
    before = cand.rrf_score

    _apply_kasten_signal_boost(
        [cand],
        signals,
        anchor_node_ids={anchor},
        total_event_count=_KASTEN_SIGNAL_COLD_START_THRESHOLD - 1,
    )
    assert cand.rrf_score == before, (
        f"cold-start gate failed: under-threshold total_event_count "
        f"{_KASTEN_SIGNAL_COLD_START_THRESHOLD - 1} still applied boost"
    )
