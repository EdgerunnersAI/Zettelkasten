"""iter-12 Task 31 R4: bandit unit tests.

Coverage:
  1. bucket_pool_size boundaries
  2. sample_floor → global flag off → static fallback
  3. sample_floor → DB unreachable → static fallback
  4. sample_floor → kill switch active → static fallback
  5. sample_floor → cold start → static fallback
  6. sample_floor → ≥N_min pulls → returns a configured arm
  7. record_outcome → fail-open on DB error (no exception raised)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. bucket_pool_size
# ---------------------------------------------------------------------------
def test_bucket_pool_size():
    from website.features.rag_pipeline.observability.anchor_seed_bandit import bucket_pool_size
    assert bucket_pool_size(0) == "S"
    assert bucket_pool_size(10) == "S"
    assert bucket_pool_size(29) == "S"
    assert bucket_pool_size(30) == "M"
    assert bucket_pool_size(50) == "M"
    assert bucket_pool_size(79) == "M"
    assert bucket_pool_size(80) == "L"
    assert bucket_pool_size(500) == "L"


# ---------------------------------------------------------------------------
# 2. Global flag off → static fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_floor_fallback_when_bandit_disabled(monkeypatch):
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    monkeypatch.setattr(mod, "_BANDIT_ENABLED", False)
    arm, tel = await mod.sample_floor(
        p_user_id="u", kasten_id="k", pool_size=20, supabase=MagicMock()
    )
    assert arm == mod._STATIC_FALLBACK
    assert tel["fallback_reason"] == "global_flag_off"


# ---------------------------------------------------------------------------
# 3. DB unreachable → static fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_floor_fallback_when_db_unreachable(monkeypatch):
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    monkeypatch.setattr(mod, "_BANDIT_ENABLED", True)

    sb = MagicMock()
    # rpc_call is awaited; make the awaitable raise
    with patch(
        "website.features.rag_pipeline.observability.anchor_seed_bandit.rpc_call",
        side_effect=RuntimeError("boom"),
    ):
        arm, tel = await mod.sample_floor(
            p_user_id="u", kasten_id="k", pool_size=20, supabase=sb
        )
    assert arm == mod._STATIC_FALLBACK
    assert "db" in (tel["fallback_reason"] or "").lower()


# ---------------------------------------------------------------------------
# 4. Kill switch active → static fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_floor_fallback_when_kill_switch_active(monkeypatch):
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    monkeypatch.setattr(mod, "_BANDIT_ENABLED", True)

    fake_resp = MagicMock()
    fake_resp.data = [{"seed_arm": 0.30, "seed_alpha": 2.0, "seed_beta": 1.0,
                       "seed_total_pulls": 25, "bandit_disabled_at": "2026-05-07T10:00:00Z"}]
    with patch(
        "website.features.rag_pipeline.observability.anchor_seed_bandit.rpc_call",
        new=AsyncMock(return_value=fake_resp),
    ):
        arm, tel = await mod.sample_floor(
            p_user_id="u", kasten_id="k", pool_size=20, supabase=MagicMock()
        )
    assert arm == mod._STATIC_FALLBACK
    assert "kill" in tel["fallback_reason"]


# ---------------------------------------------------------------------------
# 5. Cold start → static fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_floor_fallback_cold_start(monkeypatch):
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    monkeypatch.setattr(mod, "_BANDIT_ENABLED", True)
    monkeypatch.setattr(mod, "_N_MIN_PULLS", 20)

    # Total pulls across rows = 10 + 5 = 15 < 20
    fake_resp = MagicMock()
    fake_resp.data = [
        {"seed_arm": 0.30, "seed_alpha": 2.0, "seed_beta": 1.0,
         "seed_total_pulls": 10, "bandit_disabled_at": None},
        {"seed_arm": 0.35, "seed_alpha": 1.5, "seed_beta": 1.5,
         "seed_total_pulls": 5, "bandit_disabled_at": None},
    ]
    with patch(
        "website.features.rag_pipeline.observability.anchor_seed_bandit.rpc_call",
        new=AsyncMock(return_value=fake_resp),
    ):
        arm, tel = await mod.sample_floor(
            p_user_id="u", kasten_id="k", pool_size=20, supabase=MagicMock()
        )
    assert arm == mod._STATIC_FALLBACK
    assert tel["fallback_reason"] == "cold_start"


# ---------------------------------------------------------------------------
# 6. ≥N_min total pulls → bandit picks a configured arm
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sample_floor_picks_arm_above_n_min(monkeypatch):
    """With ≥20 total pulls, the bandit picks one of the configured arms."""
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    monkeypatch.setattr(mod, "_BANDIT_ENABLED", True)
    monkeypatch.setattr(mod, "_N_MIN_PULLS", 20)

    fake_resp = MagicMock()
    fake_resp.data = [
        {"seed_arm": 0.25, "seed_alpha": 5.0, "seed_beta": 5.0,
         "seed_total_pulls": 10, "bandit_disabled_at": None},
        {"seed_arm": 0.30, "seed_alpha": 9.0, "seed_beta": 2.0,
         "seed_total_pulls": 11, "bandit_disabled_at": None},  # high mean → likely winner
        {"seed_arm": 0.35, "seed_alpha": 3.0, "seed_beta": 5.0,
         "seed_total_pulls": 5, "bandit_disabled_at": None},
        {"seed_arm": 0.40, "seed_alpha": 3.0, "seed_beta": 4.0,
         "seed_total_pulls": 4, "bandit_disabled_at": None},
    ]
    with patch(
        "website.features.rag_pipeline.observability.anchor_seed_bandit.rpc_call",
        new=AsyncMock(return_value=fake_resp),
    ):
        arm, tel = await mod.sample_floor(
            p_user_id="u", kasten_id="k", pool_size=20, supabase=MagicMock()
        )
    assert arm in mod._ARMS
    assert tel["fallback_reason"] is None
    assert tel["theta_drawn"] is not None
    assert tel["arm_sampled"] == arm


# ---------------------------------------------------------------------------
# 7. record_outcome → fail-open on DB error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_outcome_fail_open():
    from website.features.rag_pipeline.observability import anchor_seed_bandit as mod
    with patch(
        "website.features.rag_pipeline.observability.anchor_seed_bandit.rpc_call",
        side_effect=RuntimeError("db error"),
    ):
        # Must not raise
        await mod.record_outcome(
            p_user_id="u", kasten_id="k", arm=0.30, pool_bucket="M",
            seed_survived=True, supabase=MagicMock()
        )
