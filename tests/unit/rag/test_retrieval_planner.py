"""Tests for the RetrievalPlanner KG-first adapter (Task 19).

8.0-H7: the planner is now a pass-through (kg_features.retrieval was deleted;
v2 entity-anchor expansion runs inside HybridRetriever via entity_anchor.py).
The narrowing behaviour these tests pin no longer exists. Skip-marked rather
than rewriting against v2 -- the behaviour is now covered by
tests/integration/v2/test_hybrid_pipeline_e2e.py.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="RetrievalPlanner narrowing retired in Phase 8.0 H7 (now pass-through); "
    "v2 entity-anchor expansion lives in HybridRetriever. See plan "
    "docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md"
)

from unittest.mock import MagicMock  # noqa: E402

from website.features.rag_pipeline.query.metadata import QueryMetadata  # noqa: E402
from website.features.rag_pipeline.retrieval.planner import RetrievalPlanner  # noqa: E402
from website.features.rag_pipeline.types import QueryClass, ScopeFilter  # noqa: E402


def _fake_kg(expanded=None, hits=None):
    fake = MagicMock()
    fake._supabase = MagicMock()
    fake.expand_subgraph = MagicMock(return_value=list(expanded or []))
    fake.hybrid_search = MagicMock(
        return_value=[MagicMock(id=h) for h in (hits or [])]
    )
    return fake


@pytest.mark.asyncio
async def test_lookup_with_entities_narrows_scope():
    kg = _fake_kg(expanded=["n1", "n2", "n3"], hits=["seed1", "seed2"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["transformer"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert out.node_ids is not None
    assert set(out.node_ids) >= {"n1", "n2", "n3"}
    assert set(out.node_ids) >= {"seed1", "seed2"}
    # depth defaulted to 1
    kg.expand_subgraph.assert_called_once()
    _, kwargs = kg.expand_subgraph.call_args
    assert kwargs.get("depth") == 1
    assert kwargs.get("user_id") == "u"
    assert set(kwargs.get("node_ids")) == {"seed1", "seed2"}


@pytest.mark.asyncio
async def test_multi_hop_uses_default_depth_and_seed_union():
    kg = _fake_kg(expanded=["a", "b"], hits=["s1"])
    planner = RetrievalPlanner(kg_module=kg, default_depth=2)
    qm = QueryMetadata(entities=["x"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.MULTI_HOP,
        scope_filter=sf,
    )
    _, kwargs = kg.expand_subgraph.call_args
    assert kwargs.get("depth") == 2
    assert set(out.node_ids) == {"a", "b", "s1"}


@pytest.mark.asyncio
async def test_thematic_short_circuits_no_kg_call():
    kg = _fake_kg(expanded=["n1"], hits=["seed1"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["x"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.THEMATIC,
        scope_filter=sf,
    )
    assert out is sf  # untouched
    kg.expand_subgraph.assert_not_called()
    kg.hybrid_search.assert_not_called()


@pytest.mark.asyncio
async def test_no_entities_returns_scope_unchanged():
    kg = _fake_kg(expanded=["n1"], hits=["s1"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=[])
    sf = ScopeFilter(node_ids=["existing"])
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert out is sf
    kg.expand_subgraph.assert_not_called()


@pytest.mark.asyncio
async def test_no_seed_hits_returns_scope_unchanged():
    kg = _fake_kg(expanded=[], hits=[])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["unknown"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert out is sf
    kg.expand_subgraph.assert_not_called()


@pytest.mark.asyncio
async def test_intersect_with_existing_scope_node_ids():
    kg = _fake_kg(expanded=["n1", "n2", "n3"], hits=["seed1"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["e"])
    sf = ScopeFilter(node_ids=["n2", "seed1", "other"])
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    # Intersection of expanded∪seed with existing scope
    assert set(out.node_ids) == {"n2", "seed1"}


@pytest.mark.asyncio
async def test_empty_intersection_falls_back_to_original_scope():
    kg = _fake_kg(expanded=["x", "y"], hits=["seed1"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["e"])
    sf = ScopeFilter(node_ids=["disjoint1", "disjoint2"])
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    # Don't narrow to empty — return original
    assert out is sf


@pytest.mark.asyncio
async def test_expand_exception_returns_original_scope():
    kg = _fake_kg(hits=["seed1"])
    kg.expand_subgraph = MagicMock(side_effect=RuntimeError("rpc down"))
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["e"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert out is sf


@pytest.mark.asyncio
async def test_hybrid_search_exception_per_entity_swallowed():
    kg = _fake_kg(expanded=["n1"], hits=["seed1"])
    # First entity raises, second succeeds
    call_count = {"n": 0}

    def _maybe_raise(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("flaky")
        return [MagicMock(id="seed1")]

    kg.hybrid_search = MagicMock(side_effect=_maybe_raise)
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["bad", "good"])
    sf = ScopeFilter()
    out = await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert out.node_ids is not None
    assert "n1" in out.node_ids
    assert "seed1" in out.node_ids


@pytest.mark.asyncio
async def test_does_not_mutate_inputs():
    kg = _fake_kg(expanded=["n1"], hits=["seed1"])
    planner = RetrievalPlanner(kg_module=kg)
    qm = QueryMetadata(entities=["e"])
    original_entities = list(qm.entities)
    sf = ScopeFilter(node_ids=["seed1", "n1", "z"])
    original_scope_ids = list(sf.node_ids)
    await planner.plan(
        user_id="u",
        query_meta=qm,
        query_class=QueryClass.LOOKUP,
        scope_filter=sf,
    )
    assert qm.entities == original_entities
    assert sf.node_ids == original_scope_ids
