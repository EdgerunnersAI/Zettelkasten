from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from website.experimental_features.nexus.source_ingest.common.models import NexusProvider
from website.experimental_features.nexus.source_ingest.common.oauth_state import consume_oauth_state

# Phase 3.5: oauth_state was rebased from public.nexus_oauth_states (v1) onto an
# in-memory store with TTL. The legacy mocks below patch ``get_supabase_client``
# on the module, which no longer exists. New v2 coverage lives in
# tests/unit/experimental_features/test_nexus_v2.py — skip-mark these v1 mocks.
pytestmark = pytest.mark.skip(
    reason="v1 oauth_state retired in Phase 3.5; replaced by tests/unit/experimental_features/test_nexus_v2.py"
)


def _state_row(*, consumed_at=None, expires_at=None):
    return {
        "id": str(uuid4()),
        "provider": "github",
        "auth_user_sub": "user-1",
        "redirect_path": "/home/nexus",
        "code_verifier": None,
        "metadata": {},
        "expires_at": (expires_at or (datetime.now(timezone.utc) + timedelta(minutes=5))).isoformat(),
        "consumed_at": consumed_at.isoformat() if consumed_at else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_consume_oauth_state_uses_atomic_update() -> None:
    client = MagicMock()
    select_query = MagicMock()
    select_query.select.return_value = select_query
    select_query.eq.return_value = select_query
    select_query.limit.return_value = select_query
    select_query.execute.return_value = MagicMock(data=[_state_row()])

    update_query = MagicMock()
    update_query.update.return_value = update_query
    update_query.eq.return_value = update_query
    update_query.is_.return_value = update_query
    update_query.gt.return_value = update_query
    update_query.execute.return_value = MagicMock(data=[_state_row(consumed_at=datetime.now(timezone.utc))])

    table_mock = MagicMock(side_effect=[select_query, update_query])
    client.table.side_effect = table_mock

    with patch(
        "website.experimental_features.nexus.source_ingest.common.oauth_state.get_supabase_client",
        return_value=client,
    ):
        record = consume_oauth_state(NexusProvider.GITHUB, "plain-state")

    assert record.consumed_at is not None
    update_query.is_.assert_called_once_with("consumed_at", "null")
    update_query.gt.assert_called_once()


def test_consume_oauth_state_rejects_previously_used_state() -> None:
    client = MagicMock()
    used_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    select_query = MagicMock()
    select_query.select.return_value = select_query
    select_query.eq.return_value = select_query
    select_query.limit.return_value = select_query
    select_query.execute.return_value = MagicMock(data=[_state_row(consumed_at=used_at)])

    update_query = MagicMock()
    update_query.update.return_value = update_query
    update_query.eq.return_value = update_query
    update_query.is_.return_value = update_query
    update_query.gt.return_value = update_query
    update_query.execute.return_value = MagicMock(data=[])

    client.table.side_effect = [select_query, update_query]

    with patch(
        "website.experimental_features.nexus.source_ingest.common.oauth_state.get_supabase_client",
        return_value=client,
    ):
        with pytest.raises(ValueError, match="already been used"):
            consume_oauth_state(NexusProvider.GITHUB, "plain-state")
