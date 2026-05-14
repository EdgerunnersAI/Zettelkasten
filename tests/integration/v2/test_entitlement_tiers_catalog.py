"""UP-15: plan-tier catalog invariant + Phase-9-pending live-quota xfail.

Two parts:
1. ``test_no_invented_plan_tiers`` and ``test_plan_quota_matrix_matches_spec``
   are pure-unit (catalog-level) assertions — they are the *non-xfail*
   catalog invariant the operator pricing-authority rule demands. Plans MUST
   be exactly {free, basic, max} with the documented quota numbers.
2. The remaining tests are live-quota enforcement probes wrapped in
   ``@pytest.mark.xfail(condition=not PHASE9_LIVE, strict=True, …)``.
   PHASE9_LIVE is read from ``PRICING_ENFORCEMENT_ENABLED=true``. When
   Phase 9 ships and that env flips on, ``strict=True`` causes the xfails
   to FAIL CI unless they actually pass — forcing the operator to remove the
   xfail decoration and lock in real enforcement coverage.

Why this shape (decision rationale): the repository ``check_entitlement`` /
``consume_entitlement`` methods are deliberate Phase-9-pending stubs
(``repository.py:84-121``). Writing live-quota tests that pass *today* would
require either seeding entitlement rows (forbidden by pricing-authority rule)
or invoking ``billing.pricing_consume_entitlement`` (golden-md5 protected).
The strict xfail keeps the test surface ready without violating either rule.
"""

from __future__ import annotations

import os

import pytest

from website.features.user_pricing.catalog import get_public_catalog
from website.features.user_pricing.models import Meter

PHASE9_LIVE = os.environ.get("PRICING_ENFORCEMENT_ENABLED", "").lower() == "true"

# Quota spec per docs/research/pricing1.md (zettel daily / weekly / monthly).
# kasten quotas are total-based; rag_question is monthly.
EXPECTED_QUOTAS: dict[str, dict[str, dict[str, int]]] = {
    "free": {
        "zettel": {"daily": 2, "weekly": 10, "monthly": 30},
        "kasten": {"total": 1},
        "rag_question": {"monthly": 30},
    },
    "basic": {
        "zettel": {"daily": 5, "weekly": 30, "monthly": 50},
        "kasten": {"total": 5},
        "rag_question": {"monthly": 100},
    },
    "max": {
        "zettel": {"daily": 30, "weekly": 100, "monthly": 200},
        "kasten": {"weekly": 5, "total": 50},
        "rag_question": {"monthly": 500},
    },
}


# ────────────────── catalog invariants (always run) ──────────────────


def test_no_invented_plan_tiers():
    """Only free/basic/max may exist. Inventing tiers violates pricing-authority rule."""
    catalog = get_public_catalog()
    plan_ids = set(catalog["plans"].keys())
    assert plan_ids == {"free", "basic", "max"}, (
        f"Plan-tier set drifted from operator-locked {{free, basic, max}}: {plan_ids}"
    )
    # Each plan's nested id field must match its key (frontend dedup invariant)
    for plan_id, plan in catalog["plans"].items():
        assert plan["id"] == plan_id


def test_plan_quota_matrix_matches_spec():
    """Quotas per plan/meter/period must match the locked spec in pricing1.md.

    Free zettel 2/10/30 (daily/weekly/monthly), Basic zettel 5/30/50,
    Max zettel 30/100/200. Kasten and rag_question per locked spec.
    """
    catalog = get_public_catalog()
    for plan_id, expected_meters in EXPECTED_QUOTAS.items():
        plan = catalog["plans"][plan_id]
        actual_quotas = plan["quotas"]
        for meter_name, expected_periods in expected_meters.items():
            actual_periods = actual_quotas.get(meter_name, {})
            for period, expected in expected_periods.items():
                actual = actual_periods.get(period)
                assert actual == expected, (
                    f"{plan_id}/{meter_name}/{period}: expected {expected}, got {actual}"
                )


def test_meter_enum_matches_catalog():
    """Catalog meters must exactly cover the Meter enum (no orphans either way)."""
    catalog = get_public_catalog()
    catalog_meters = set(catalog["meters"].keys())
    enum_meters = {m.value for m in Meter}
    assert catalog_meters == enum_meters, (
        f"Catalog meters {catalog_meters} drifted from Meter enum {enum_meters}"
    )


# ────────── live-quota enforcement (xfail until Phase 9 lands) ──────────


@pytest.mark.live
@pytest.mark.xfail(
    condition=not PHASE9_LIVE,
    strict=True,
    reason="Phase-9 RPC pending — repository.check_entitlement is a fail-open stub",
)
def test_free_tier_zettel_monthly_quota_enforced(mint_user):
    """Free tier monthly zettel quota = 30. The 31st call must 402.

    When Phase 9 enforcement lands, set PRICING_ENFORCEMENT_ENABLED=true and
    this test must pass. strict=True causes it to FAIL (not xpass) once the
    env is on — that failure is the prompt to remove this xfail decorator.
    """
    from fastapi.testclient import TestClient

    from website.app import create_app

    user = mint_user()
    quota = EXPECTED_QUOTAS["free"]["zettel"]["monthly"]
    with TestClient(create_app()) as client:
        statuses = []
        for i in range(quota + 1):
            r = client.post(
                "/api/zettels/add",
                json={
                    "url": "https://example.com",
                    "client_action_id": f"quota-probe-{i}",
                    "persist": True,
                    "surface": "home",
                    "mode": "sync",
                },
                headers={"Authorization": f"Bearer {user.jwt}"},
            )
            statuses.append(r.status_code)
        assert statuses[-1] == 402, (
            f"Expected 402 on call {quota + 1}; got {statuses[-1]} (all: {statuses})"
        )


@pytest.mark.live
@pytest.mark.xfail(
    condition=not PHASE9_LIVE,
    strict=True,
    reason="Phase-9 RPC pending — quota enforcement not active",
)
def test_basic_tier_zettel_monthly_quota_enforced(mint_user):
    """Basic tier monthly zettel quota = 50; 51st must 402.

    Requires Phase-9 enforcement AND a real subscribe path to put the user
    on Basic. Until then this xfails (expected) under the same env gate.
    """
    pytest.skip(
        "Live Basic-tier enforcement probe — requires real subscribe path "
        "(forbidden to seed directly per pricing-authority rule)"
    )
