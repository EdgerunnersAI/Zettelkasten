"""Graph centrality scoring over retrieval candidates."""

from __future__ import annotations

import math
import os
from typing import Any
from uuid import UUID

import networkx as nx

from website.features.rag_pipeline.types import QueryClass, RetrievalCandidate
from website.core.supabase_kg.client import get_supabase_client
from website.features.rag_pipeline.retrieval._async_helpers import rpc_call


_USAGE_EDGES_ENABLED = os.environ.get("RAG_USAGE_EDGES_ENABLED", "true").lower() == "true"


def _usage_weight_bonus(
    supabase: Any,
    *,
    user_id: UUID,
    target_node_id: str,
    query_class: QueryClass | str,
) -> float:
    """Read decayed usage-edge weights for (user, target, query_class) and map to a bounded bonus.

    Returns a sigmoid-bounded value in [-0.05, +0.05] (≈0 when weight==0,
    approaching +0.05 as weight grows). Returns 0.0 on any failure so a missing
    materialized view (cold staging) or transient DB error never breaks the
    request path.
    """
    if not _USAGE_EDGES_ENABLED:
        return 0.0
    try:
        qc_value = query_class.value if hasattr(query_class, "value") else str(query_class)
        res = (
            supabase.table("kg_usage_edges_agg")
            .select("weight")
            .eq("user_id", str(user_id))
            .eq("target_node_id", target_node_id)
            .eq("query_class", qc_value)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        weight = sum(float(r.get("weight") or 0.0) for r in rows)
        # Sigmoid-bounded bonus in (-0.05, +0.05); 0 when weight==0.
        return 0.10 / (1.0 + math.exp(-weight / 5.0)) - 0.05
    except Exception:
        return 0.0


class LocalizedPageRankScorer:
    """Compute a small induced-subgraph PageRank score for candidates."""

    def __init__(self, damping: float = 0.85, supabase: Any | None = None):
        self._supabase = supabase or get_supabase_client()
        self._damping = damping

    async def score(
        self,
        *,
        user_id: UUID,
        candidates: list[RetrievalCandidate],
        query_class: QueryClass | str | None = None,
    ) -> None:
        node_ids = list({candidate.node_id for candidate in candidates})
        if len(node_ids) < 2:
            for candidate in candidates:
                candidate.graph_score = 0.0
            return

        response = await rpc_call(self._supabase.rpc(
            "rag_subgraph_for_pagerank",
            {"p_user_id": str(user_id), "p_node_ids": node_ids},
        ))
        edges = response.data or []

        graph = nx.Graph()
        graph.add_nodes_from(node_ids)
        for edge in edges:
            graph.add_edge(
                edge["source_node_id"],
                edge["target_node_id"],
                weight=edge.get("weight") or 1.0,
            )

        if graph.number_of_edges() == 0:
            for candidate in candidates:
                candidate.graph_score = 0.0
        else:
            pagerank = nx.pagerank(graph, alpha=self._damping, weight="weight")
            max_score = max(pagerank.values()) or 1.0
            for candidate in candidates:
                candidate.graph_score = pagerank.get(candidate.node_id, 0.0) / max_score

        # Usage-edge bonus: only applied when query_class is supplied (caller opts in).
        # Cached per target node_id within this call to avoid duplicate DB lookups
        # when multiple chunk-candidates share a node.
        if query_class is None:
            return

        bonus_cache: dict[str, float] = {}
        for candidate in candidates:
            if candidate.node_id not in bonus_cache:
                bonus_cache[candidate.node_id] = _usage_weight_bonus(
                    self._supabase,
                    user_id=user_id,
                    target_node_id=candidate.node_id,
                    query_class=query_class,
                )
            candidate.graph_score = (candidate.graph_score or 0.0) + bonus_cache[candidate.node_id]
