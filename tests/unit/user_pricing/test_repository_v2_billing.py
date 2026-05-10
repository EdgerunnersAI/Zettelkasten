"""Unit tests for user_pricing/repository.py v2 routing (Task 8.0.2).

Mocks the v2 client; asserts every repository method targets the
``billing.*`` schema and never touches public.pricing_* or
``KGRepository._client``. Closes hazards H2 + H3 from
``docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md``.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock, patch

import pytest


@patch("website.core.persist.get_billing_scope")
def test_get_billing_profile_uses_billing_schema(mock_get_scope):
    """get_billing_profile invokes billing.pricing_billing_profiles by profile_id."""
    from website.features.user_pricing.repository import PricingRepository

    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.data = [{"profile_id": str(uuid.uuid4()), "email": "u@x.io"}]
    (
        fake_client.schema.return_value
        .table.return_value
        .select.return_value
        .eq.return_value
        .limit.return_value
        .execute.return_value
    ) = fake_resp

    profile_id = uuid.uuid4()
    mock_get_scope.return_value = (fake_client, profile_id)

    repo = PricingRepository()
    result = repo.get_billing_profile(user_sub=str(profile_id))

    assert result is not None
    fake_client.schema.assert_any_call("billing")
    fake_client.schema.return_value.table.assert_any_call("pricing_billing_profiles")


def test_check_entitlement_no_v1_rpc_in_repository_source():
    """check_entitlement must not call the legacy public.pricing_check_entitlement RPC."""
    from website.features.user_pricing import repository

    src = inspect.getsource(repository)
    # The string 'pricing_check_entitlement' may appear in routes/tests, but
    # this repository module itself must contain ZERO RPC string references
    # to v1 entitlement enforcement (the body is now a fail-open stub).
    assert 'rpc("pricing_check_entitlement"' not in src, (
        "v1 pricing_check_entitlement RPC reference must be deleted"
    )
    assert 'rpc("pricing_consume_entitlement"' not in src, (
        "v1 pricing_consume_entitlement RPC reference must be deleted"
    )


def test_repository_does_not_use_render_user_id_in_v2_payloads():
    """v2 billing.* uses profile_id (uuid). v1 RPC parameter / SQL column key must be gone.

    The in-memory mirror dicts retain the legacy ``render_user_id`` key for
    backward-compatibility with the route layer (``website/features/user_pricing/routes.py``
    reads ``record.get("render_user_id")`` to bind a payment to its caller).
    That dict-key shape is route contract, NOT a v1 SQL surface; we only
    forbid the v1 RPC parameter name ``p_render_user_id`` here.
    """
    from website.features.user_pricing import repository

    src = inspect.getsource(repository)
    assert "p_render_user_id" not in src, (
        "v1 RPC parameter p_render_user_id must be removed (v2 uses p_profile_id)"
    )
    # No payload sent to billing.* may key by render_user_id — v2 schema
    # strictly uses profile_id. Catch a common foot-gun: copy/pasting the
    # in-memory dict into a v2 row builder.
    assert '"render_user_id": str(profile_id)' not in src
    assert '"render_user_id": user_sub, "p_' not in src


def test_repository_does_not_import_supabase_kg():
    """repository.py must not import from website.core.supabase_kg (v1 KG path)."""
    from website.features.user_pricing import repository

    src = inspect.getsource(repository)
    assert "from website.core.supabase_kg" not in src
    assert "import website.core.supabase_kg" not in src


def test_repository_does_not_call_get_supabase_scope():
    """v1 helper get_supabase_scope is dead-path; only get_billing_scope remains."""
    from website.features.user_pricing import repository

    src = inspect.getsource(repository)
    assert "get_supabase_scope" not in src, (
        "v1 get_supabase_scope helper must not be referenced; use get_billing_scope"
    )


@patch("website.core.persist.get_billing_scope")
def test_check_entitlement_returns_true_fail_open(mock_get_scope):
    """check_entitlement is a fail-open no-op until Phase 9 ships v3 enforcement."""
    from website.features.user_pricing.models import Meter
    from website.features.user_pricing.repository import PricingRepository

    repo = PricingRepository()
    # Should not call get_billing_scope at all (pure stub).
    assert (
        repo.check_entitlement(
            user_sub=str(uuid.uuid4()), meter=Meter.ZETTEL, action_id="x"
        )
        is True
    )
    mock_get_scope.assert_not_called()


@patch("website.core.persist.get_billing_scope")
def test_consume_entitlement_returns_none_fail_open(mock_get_scope):
    """consume_entitlement is a fail-open no-op until Phase 9 ships v3 enforcement."""
    from website.features.user_pricing.models import Meter
    from website.features.user_pricing.repository import PricingRepository

    repo = PricingRepository()
    assert (
        repo.consume_entitlement(
            user_sub=str(uuid.uuid4()), meter=Meter.ZETTEL, action_id="x"
        )
        is None
    )
    mock_get_scope.assert_not_called()


def test_get_billing_scope_hard_fails_on_non_uuid():
    """Operator-approved: non-UUID user_sub is a hard error in v2 billing path."""
    from website.core.persist import get_billing_scope

    with pytest.raises(RuntimeError, match="Supabase auth UUID"):
        get_billing_scope("not-a-uuid")


def test_get_billing_scope_returns_uuid_for_valid_input():
    """Valid UUID strings resolve to UUID objects; raw UUIDs pass through."""
    from website.core.persist import get_billing_scope

    pid = uuid.uuid4()
    with patch("website.core.persist._get_v2_client") as mock_client:
        mock_client.return_value = MagicMock()
        # String form
        client, returned_pid = get_billing_scope(str(pid))
        assert returned_pid == pid
        # UUID form
        client, returned_pid2 = get_billing_scope(pid)
        assert returned_pid2 == pid
