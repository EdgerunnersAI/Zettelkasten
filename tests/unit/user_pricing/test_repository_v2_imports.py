"""Phase 3.2 — user_pricing/repository.py SURGICAL v2 import alias test.

Per ``feedback_pricing_module_authority.md``, the pricing module is
operator-defined territory: ZERO behaviour changes, ZERO seeding, ZERO
``consume_entitlement`` edits, ZERO plan-name changes. The Phase 3.2
diff was a single-line import swap (legacy ``supabase_kg`` → v2
``supabase_v2`` aliased to the same name). These tests guard that
constraint.

**Retired in Phase 8.0.2 (2026-05-10).** The v1 fallback branches in
``user_pricing/repository.py`` were deleted as part of the v2-purge
closeout — see
``docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md``. The
``is_supabase_configured`` alias and the v1 RPC-string snapshots no
longer apply. The successor coverage lives in
``tests/unit/user_pricing/test_repository_v2_billing.py``. The golden
SQL function body in ``supabase/website/_v2/06_billing_schema.sql`` is
guarded separately by ``supabase/website/_v2/golden/pricing_consume_entitlement.md5``.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "v1 pricing surface retired in Phase 8.0.2 (2026-05-10); "
        "replaced by tests/unit/user_pricing/test_repository_v2_billing.py"
    )
)

import inspect
from pathlib import Path

# Import lazily inside test bodies — the v2-purge closeout removed the
# ``is_supabase_configured`` alias from the repository module, so a top-level
# import would fail at collection time even though all tests are skip-marked.
try:  # pragma: no cover — collection-only safety
    from website.features.user_pricing.repository import (
        PricingRepository,
        is_supabase_configured,
    )
except ImportError:  # pragma: no cover
    PricingRepository = None  # type: ignore[assignment]
    is_supabase_configured = None  # type: ignore[assignment]


REPO_PATH = Path("website/features/user_pricing/repository.py")


def test_repository_does_not_import_supabase_kg():
    """File-level grep: `supabase_kg` must not appear in repository.py."""
    src = REPO_PATH.read_text(encoding="utf-8")
    assert "supabase_kg" not in src, (
        "user_pricing/repository.py must not reference supabase_kg after Phase 3.2"
    )


def test_repository_imports_v2_client():
    src = REPO_PATH.read_text(encoding="utf-8")
    assert "from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured" in src, (
        "user_pricing/repository.py must alias v2 is_v2_configured -> is_supabase_configured"
    )


def test_is_supabase_configured_symbol_still_importable():
    """The aliased symbol must remain accessible from the module — every
    call site in repository.py uses the bare name ``is_supabase_configured``.
    """
    assert callable(is_supabase_configured)


def test_consume_entitlement_signature_unchanged():
    """Snapshot the call shape of ``PricingRepository.consume_entitlement``.

    Per pricing-module-authority feedback: the call sites in this method
    pass exactly three named args to the SQL RPC: ``p_render_user_id``,
    ``p_meter``, ``p_action_id``. Any change here is a behaviour change
    and is forbidden under Phase 3.2.
    """
    sig = inspect.signature(PricingRepository.consume_entitlement)
    params = sig.parameters
    assert list(params.keys()) == ["self", "user_sub", "meter", "action_id"]
    # All three after self are keyword-only (the method uses ``*``).
    for name in ("user_sub", "meter", "action_id"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY, (
            f"consume_entitlement.{name} must remain keyword-only"
        )

    # Snapshot the SQL RPC name + arg names that are baked into the body
    # string. If anyone "fixes" this, the test fails loudly.
    src = REPO_PATH.read_text(encoding="utf-8")
    assert "pricing_consume_entitlement" in src
    assert '"p_render_user_id": user_sub' in src
    assert '"p_meter": str(meter)' in src
    assert '"p_action_id": action_id' in src
