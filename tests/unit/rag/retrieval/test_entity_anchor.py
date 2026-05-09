import pytest
from unittest.mock import MagicMock
from website.features.rag_pipeline.retrieval.entity_anchor import resolve_anchor_nodes

# Phase 2.4.1: this module mocks the retired v1 RPC ``rag_resolve_entity_anchors``
# whose schema (string node_ids, sandbox-scoped) is incompatible with the v2
# replacement ``kg.resolve_entity_anchors_v2`` (bigint kg_node_id, workspace-
# scoped). Coverage shifts to ``tests/integration/v2/test_phase_1d_rpcs.py``.
pytestmark = pytest.mark.skip(reason="v1 RPC retired in Phase 2.4.1; coverage moved to integration v2 tests")


@pytest.mark.asyncio
async def test_resolve_anchor_walker():
    """'Walker' resolves to the yt-matt-walker-sleep-depriv zettel via fuzzy title match."""
    fake_supabase = MagicMock()
    fake_supabase.rpc.return_value.execute.return_value.data = [
        {"node_id": "yt-matt-walker-sleep-depriv", "title": "Matt Walker on Sleep Deprivation"},
    ]
    result = await resolve_anchor_nodes(["Walker"], sandbox_id="kasten1", supabase=fake_supabase)
    assert "yt-matt-walker-sleep-depriv" in result
