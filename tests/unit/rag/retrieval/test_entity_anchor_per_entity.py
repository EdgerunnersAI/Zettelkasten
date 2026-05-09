"""iter-11 Class C: anchor resolution must union per-entity, not require all.

Replaces the iter-08 batched RPC with a per-entity loop. Each entity gets its
own RPC; non-empty results are unioned. An empty entity poisons only itself,
not the whole resolution. The loop also gives us per-entity observability
(which entity resolved? which didn't?) that the batched RPC could not.
"""
from unittest.mock import MagicMock

import pytest

from website.features.rag_pipeline.retrieval.entity_anchor import resolve_anchor_nodes

# Phase 2.4.1: this module mocks the retired v1 RPC ``rag_resolve_entity_anchors``
# (p_entities/p_sandbox_id parameter shape and string node_id return). The v2
# replacement ``kg.resolve_entity_anchors_v2`` returns bigint kg_node_id and
# is workspace-scoped — schema-incompatible with these mocks.
pytestmark = pytest.mark.skip(reason="v1 RPC retired in Phase 2.4.1; coverage moved to integration v2 tests")


def _rpc_factory(per_entity_data: dict[str, list[dict]], calls: list[list[str]]):
    def _rpc(name, params):
        ents = list(params.get("p_entities") or [])
        calls.append(ents)
        node = MagicMock()
        key = ",".join(ents) if ents else ""
        node.execute = MagicMock(return_value=MagicMock(data=per_entity_data.get(key, [])))
        return node
    return _rpc


@pytest.mark.asyncio
async def test_partial_resolution_returns_resolved_subset():
    """Two entities, one resolves, one does not -> union of resolved (= just
    the one that hit). The iter-09 batched call returned [] for ALL when ONE
    entity poisoned the match (q10 failure mode root cause)."""
    sb = MagicMock()
    calls: list[list[str]] = []
    sb.rpc = _rpc_factory(
        {"Steve Jobs": [{"node_id": "yt-steve-jobs-2005-stanford"}]},
        calls,
    )
    out = await resolve_anchor_nodes(
        ["Steve Jobs", "Naval Ravikant"],
        sandbox_id="00000000-0000-0000-0000-000000000001",
        supabase=sb,
    )
    assert "yt-steve-jobs-2005-stanford" in out
    # Per-entity loop hits the RPC twice (once per entity), in input order.
    assert calls == [["Steve Jobs"], ["Naval Ravikant"]]


@pytest.mark.asyncio
async def test_all_resolve_returns_full_union():
    sb = MagicMock()
    calls: list[list[str]] = []
    sb.rpc = _rpc_factory(
        {"A": [{"node_id": "n1"}], "B": [{"node_id": "n2"}]},
        calls,
    )
    out = await resolve_anchor_nodes(
        ["A", "B"],
        sandbox_id="00000000-0000-0000-0000-000000000001",
        supabase=sb,
    )
    assert out == {"n1", "n2"}


@pytest.mark.asyncio
async def test_zero_resolve_returns_empty():
    sb = MagicMock()
    sb.rpc = lambda *a, **k: MagicMock(execute=MagicMock(return_value=MagicMock(data=[])))
    out = await resolve_anchor_nodes(
        ["X"],
        sandbox_id="00000000-0000-0000-0000-000000000001",
        supabase=sb,
    )
    assert out == set()


@pytest.mark.asyncio
async def test_empty_string_entity_skipped():
    """Per-entity loop strips/skips empty / whitespace-only entries before RPC."""
    sb = MagicMock()
    calls: list[list[str]] = []
    sb.rpc = _rpc_factory({"X": [{"node_id": "nx"}]}, calls)
    out = await resolve_anchor_nodes(
        ["", " ", "X"],
        sandbox_id="00000000-0000-0000-0000-000000000001",
        supabase=sb,
    )
    assert out == {"nx"}
    # Only "X" should have hit the RPC; empty / whitespace strings filtered.
    assert calls == [["X"]]


@pytest.mark.asyncio
async def test_rpc_exception_does_not_poison_other_entities():
    """An RPC error on ONE entity must log+skip and continue with the rest."""
    sb = MagicMock()
    calls: list[list[str]] = []

    def _rpc(name, params):
        ents = list(params.get("p_entities") or [])
        calls.append(ents)
        if ents == ["A"]:
            raise RuntimeError("simulated rpc failure on A")
        node = MagicMock()
        node.execute = MagicMock(return_value=MagicMock(data=[{"node_id": "nb"}]))
        return node

    sb.rpc = _rpc
    out = await resolve_anchor_nodes(
        ["A", "B"],
        sandbox_id="00000000-0000-0000-0000-000000000001",
        supabase=sb,
    )
    assert out == {"nb"}
    assert calls == [["A"], ["B"]]
