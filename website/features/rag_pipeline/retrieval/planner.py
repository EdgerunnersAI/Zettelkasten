"""KG-first retrieval planner — pass-through after Phase 8.0 H7.

Originally (Task 19 / T20) this consulted ``website.features.kg_features.retrieval``
to narrow the :class:`ScopeFilter` via ``hybrid_search`` + ``expand_subgraph``
against v1 ``public.kg_nodes`` / ``kg_links``. Those tables were dropped in
Phase 6 (commit e168b38) and the v1 module was retired in Phase 8.0 H7
alongside this rewrite.

v2 entity-anchor expansion now lives **inside** :class:`HybridRetriever` (see
``website.features.rag_pipeline.retrieval.entity_anchor``), which calls the
``kg.expand_subgraph(p_workspace_id, p_node_ids bigint[], p_depth int)`` v2
RPC with the workspace_id resolved from the kasten. That replaces what this
planner used to do — and does it correctly under the v2 RLS / workspace
boundary, which the planner had no good way to honour.

This module is preserved as a pass-through so the orchestrator wiring,
constructor signature, and the ``planner.plan(...)`` call site in
``orchestrator.py`` remain stable. Removing the planner outright would
require a larger orchestrator refactor outside this task's scope.
"""
from __future__ import annotations

import logging

from website.features.rag_pipeline.query.metadata import QueryMetadata
from website.features.rag_pipeline.types import QueryClass, ScopeFilter

logger = logging.getLogger(__name__)


class RetrievalPlanner:
    """Pass-through planner. See module docstring for migration history."""

    def __init__(
        self,
        *,
        kg_module=None,
        default_depth: int = 1,
        seeds_per_entity: int = 3,
    ) -> None:
        # kg_module / default_depth / seeds_per_entity kept for back-compat
        # with existing constructor call sites + tests; no longer wired to
        # any v1 module.
        self._kg = kg_module
        self._default_depth = default_depth
        self._seeds_per_entity = seeds_per_entity

    async def plan(
        self,
        *,
        user_id: str,
        query_meta: QueryMetadata,
        query_class: QueryClass,
        scope_filter: ScopeFilter,
    ) -> ScopeFilter:
        """Return ``scope_filter`` unchanged.

        v2 entity-anchor narrowing happens in :class:`HybridRetriever` via
        ``entity_anchor.py`` using the workspace_id resolved from the kasten;
        narrowing the scope here would either duplicate that work or do it
        with the wrong tenancy boundary.
        """
        return scope_filter
