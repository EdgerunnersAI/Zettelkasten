"""Phase 8.0 H5 - pipelines.extraction_blocklist v2 port.

Verifies (a) the blocklist module no longer references the dropped v1 table,
and (b) the EntityBlocklist async roundtrip (record_miss x N -> is_blocked ->
record_hit -> evicted) works against pipelines.extraction_blocklist using the
v2 service-role client.
"""
from __future__ import annotations

import inspect

import pytest

from website.core.supabase_v2.client import get_v2_client


pytestmark = pytest.mark.live


def test_blocklist_module_imports_no_v1():
    """Source-level guard: dropped v1 table name must not reappear."""
    from website.features.rag_pipeline.query import blocklist
    src = inspect.getsource(blocklist)
    assert "kg_extraction_blocklist" not in src, (
        "blocklist.py still references the dropped public.kg_extraction_blocklist"
    )
    # Must use v2 schema-qualified PostgREST path.
    assert 'schema("pipelines").table("extraction_blocklist")' in src


async def test_blocklist_miss_block_hit_roundtrip(mint_user):
    """record_miss x threshold -> is_blocked True -> record_hit -> not blocked.

    Uses the real workspace_id (FK to core.workspaces) so cascade-on-delete
    cleans up the row when the test user is torn down.
    """
    from website.features.rag_pipeline.query.blocklist import (
        EntityBlocklist,
        _MISS_THRESHOLD,
        _COLD_START_NODE_FLOOR,
    )

    user = mint_user(workspace_count=1)
    workspace_id = str(user.workspace_ids[0])
    entity = "phase-8-h5-fixture-entity"
    # Bypass cold-start guard.
    node_count = _COLD_START_NODE_FLOOR

    blocklist = EntityBlocklist(get_v2_client())

    # Initially not blocked.
    assert await blocklist.is_blocked(workspace_id, entity, node_count=node_count) is False

    # Accumulate misses to threshold so blocked_until is set.
    for _ in range(_MISS_THRESHOLD):
        await blocklist.record_miss(workspace_id, entity, node_count=node_count)
    assert await blocklist.is_blocked(workspace_id, entity, node_count=node_count) is True

    # A successful resolution evicts the block.
    await blocklist.record_hit(workspace_id, entity)
    assert await blocklist.is_blocked(workspace_id, entity, node_count=node_count) is False

    # Cold-start guard still short-circuits regardless of state.
    assert await blocklist.is_blocked(workspace_id, entity, node_count=0) is False
