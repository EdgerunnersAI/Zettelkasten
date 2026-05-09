"""Locks the pricing module against unauthorised drift.

This test suite enforces the operator-defined pricing module per:
- docs/research/pricing1.md (the canonical pricing spec — Free 2/10/30, Basic 5/30/50, Max 30/100/200)
- ~/.claude/.../memory/feedback_pricing_module_authority.md (NEVER seed entitlements,
  NEVER alter pricing_consume_entitlement, NEVER auto-subscribe; 402 quota_exhausted is correct)
- docs/superpowers/plans/2026-05-09-website-features-v2-purge.md Round-1 Amendment 0.8
  + Round-2 R2.7 (golden hash, not pg_get_functiondef byte-eq, for pg-version robustness)

If any test in this file fails, an unauthorised pricing edit landed. Revert and ask the operator.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _baseline_count(table: str) -> int:
    """Read the Phase-0 baseline row count from docs/db-v2/baseline-counts-pre-pass2.txt.

    The baseline file is generated once at Phase 0.7 and committed; tests assert against it.
    """
    repo_root = Path(__file__).resolve().parents[3]
    baseline_path = repo_root / "docs" / "db-v2" / "baseline-counts-pre-pass2.txt"
    if not baseline_path.exists():
        pytest.fail(f"baseline file missing at {baseline_path}; rerun Phase 0.7")
    text = baseline_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(table)}\s+(\d+)\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        pytest.fail(f"table {table!r} not found in baseline file")
    return int(m.group(1))


@pytest.mark.asyncio
async def test_pricing_entitlements_unchanged_count(asyncpg_pool):
    """Row count of billing.pricing_plan_entitlements must match the Phase-0 baseline.

    Defends against unauthorised seeding (memory: feedback_pricing_module_authority.md
    — operator caught a previous executor seeding 25/200/2000 limits and reverted via
    commit f51a629). Any executor that adds entitlement rows fails CI here.
    """
    expected = _baseline_count("billing.pricing_plan_entitlements")
    async with asyncpg_pool.acquire() as conn:
        actual = await conn.fetchval("SELECT count(*) FROM billing.pricing_plan_entitlements")
    assert actual == expected, (
        f"billing.pricing_plan_entitlements row count drifted from {expected} to {actual}. "
        "Executor MAY NOT seed entitlements without per-row operator approval. "
        "See feedback_pricing_module_authority.md."
    )


@pytest.mark.asyncio
async def test_pricing_subscriptions_unchanged_count(asyncpg_pool):
    """Row count of billing.pricing_subscriptions must match the Phase-0 baseline.

    Defends against unauthorised auto-subscribe (no auto-subscribe trigger by design;
    Razorpay webhook is the canonical paid-subscription entry point).
    """
    expected = _baseline_count("billing.pricing_subscriptions")
    async with asyncpg_pool.acquire() as conn:
        actual = await conn.fetchval("SELECT count(*) FROM billing.pricing_subscriptions")
    assert actual == expected, (
        f"billing.pricing_subscriptions row count drifted from {expected} to {actual}. "
        "Executor MAY NOT auto-create subscriptions; that is a Razorpay-webhook flow."
    )


@pytest.mark.asyncio
async def test_pricing_consume_entitlement_body_unchanged(asyncpg_pool):
    """Logical body of billing.pricing_consume_entitlement must match the golden hash.

    Per Round-2 amendment R2.7: hash (prosrc, proargtypes, provolatile, prosecdef, prorettype)
    from pg_proc — robust to pg-minor-version whitespace and pg_get_functiondef formatting drift.
    """
    repo_root = Path(__file__).resolve().parents[3]
    golden_path = repo_root / "supabase" / "website" / "_v2" / "golden" / "pricing_consume_entitlement.md5"
    if not golden_path.exists():
        pytest.fail(f"golden file missing at {golden_path}; rerun Phase 0.8")
    golden_hash = golden_path.read_text(encoding="utf-8").strip()

    async with asyncpg_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT prosrc, proargtypes::text AS argtypes, provolatile::text AS volatile,
                   prosecdef::text AS secdef, prorettype::text AS rettype
            FROM pg_proc
            WHERE oid = 'billing.pricing_consume_entitlement(uuid,text,text)'::regprocedure
        """)
    assert row is not None, "billing.pricing_consume_entitlement(uuid,text,text) not found in pg_proc"

    payload = (
        row["prosrc"] + ":" + row["argtypes"] + ":" + row["volatile"]
        + ":" + row["secdef"] + ":" + row["rettype"]
    ).encode("utf-8")
    actual_hash = hashlib.md5(payload).hexdigest()

    assert actual_hash == golden_hash, (
        f"pricing_consume_entitlement logical body drifted from golden hash. "
        f"actual={actual_hash}, golden={golden_hash}. "
        "If this is intentional, regenerate the golden file ONLY with operator approval. "
        "See feedback_pricing_module_authority.md hard rule #2 (never alter consume_entitlement)."
    )
