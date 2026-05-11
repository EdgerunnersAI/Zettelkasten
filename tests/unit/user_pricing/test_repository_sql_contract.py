from __future__ import annotations

from pathlib import Path

import pytest


# Phase 8.5 v2-purge (2026-05-11): the v1 user_pricing SQL contract test
# anchors at the archived legacy migration. v2 pricing surface is covered by
# tests/unit/user_pricing/test_repository_v2_billing.py.
pytestmark = pytest.mark.skip(
    reason=(
        "v1 user_pricing migration archived in Phase 8.5 v2-purge (2026-05-11); "
        "replaced by tests/unit/user_pricing/test_repository_v2_billing.py"
    )
)


def test_user_pricing_migration_defines_required_tables_and_rpcs() -> None:
    sql = Path(
        "supabase/website/kg_public/migrations_archived_2026-05-11/"
        "2026-05-01_user_pricing.sql"
    ).read_text(encoding="utf-8")

    for name in [
        "pricing_billing_profiles",
        "pricing_orders",
        "pricing_subscriptions",
        "pricing_webhook_events",
        "pricing_credit_ledger",
        "pricing_usage_counters",
        "pricing_check_entitlement",
        "pricing_consume_entitlement",
    ]:
        assert name in sql

    assert "unique" in sql.lower()
    assert "for update" in sql.lower()

