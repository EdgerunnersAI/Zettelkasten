"""iter-12 β TERTIARY: workspace_id non-None at every v2 write boundary.

Service-role bypasses RLS; application assertion is the only guard against
cross-tenant leaks via missing/NULL workspace_id at the write boundary.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4, UUID


# ---------------------------------------------------------------------------
# KGRepository.upsert_node
# ---------------------------------------------------------------------------

def test_kg_repo_upsert_node_rejects_none_workspace_id():
    """upsert_node must raise on workspace_id=None."""
    from website.core.supabase_v2.repositories.kg_repository import KGRepository

    repo = KGRepository(client=MagicMock())
    with pytest.raises((ValueError, TypeError, AssertionError)):
        repo.upsert_node(
            workspace_id=None,
            node_type="entity",
            canonical_name="Test",
            slug="test",
        )


def test_kg_repo_upsert_node_accepts_valid_workspace_id():
    """upsert_node must not raise when a real UUID is provided."""
    from website.core.supabase_v2.repositories.kg_repository import KGRepository

    mock_client = MagicMock()
    mock_client.schema.return_value.table.return_value.upsert.return_value.execute.return_value.data = [
        {"id": 1}
    ]
    repo = KGRepository(client=mock_client)
    # Should not raise
    result = repo.upsert_node(
        workspace_id=uuid4(),
        node_type="entity",
        canonical_name="Test",
        slug="test",
    )
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# KGRepository.add_edge
# ---------------------------------------------------------------------------

def test_kg_repo_add_edge_rejects_none_workspace_id():
    """add_edge must raise on workspace_id=None."""
    from website.core.supabase_v2.repositories.kg_repository import KGRepository

    repo = KGRepository(client=MagicMock())
    with pytest.raises((ValueError, TypeError, AssertionError)):
        repo.add_edge(
            workspace_id=None,
            src_node_id=1,
            dst_node_id=2,
            relation_type="related",
        )


def test_kg_repo_add_edge_accepts_valid_workspace_id():
    """add_edge must not raise when a real UUID is provided."""
    from website.core.supabase_v2.repositories.kg_repository import KGRepository

    mock_client = MagicMock()
    mock_client.schema.return_value.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 99}
    ]
    repo = KGRepository(client=mock_client)
    # Should not raise
    result = repo.add_edge(
        workspace_id=uuid4(),
        src_node_id=1,
        dst_node_id=2,
        relation_type="related",
    )
    assert isinstance(result, int)
