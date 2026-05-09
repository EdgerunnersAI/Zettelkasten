"""M3 — Graph Analytics powered by NetworkX.

Computes structural metrics (PageRank, communities, centrality) over
the knowledge graph so the frontend can visualise importance and clusters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import networkx as nx

from website.core.graph_models import KGGraph

logger = logging.getLogger(__name__)


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class GraphMetrics:
    """Computed graph-level and node-level metrics."""

    pagerank: dict[str, float] = field(default_factory=dict)
    communities: dict[str, int] = field(default_factory=dict)
    betweenness: dict[str, float] = field(default_factory=dict)
    closeness: dict[str, float] = field(default_factory=dict)
    num_communities: int = 0
    num_components: int = 0
    computed_at: str = ""


# ── Graph construction ──────────────────────────────────────────────────────

def _build_networkx_graph(graph: KGGraph) -> nx.Graph:
    """Build an undirected NetworkX graph from a ``KGGraph`` payload."""
    G = nx.Graph()
    for node in graph.nodes:
        G.add_node(node.id, name=node.name, group=node.group)
    for link in graph.links:
        G.add_edge(link.source, link.target, relation=link.relation)
    return G


def _compute_with_fallback(
    compute_fn,
    fallback_value,
    *,
    label: str,
):
    """Run a metric computation and fall back safely on failure."""
    try:
        return compute_fn()
    except Exception as exc:
        logger.warning("%s failed: %s", label, exc)
        return fallback_value() if callable(fallback_value) else fallback_value


# ── Metric computation ──────────────────────────────────────────────────────

def compute_graph_metrics(graph: KGGraph) -> GraphMetrics:
    """Compute structural metrics for the given knowledge graph.

    Handles edge cases:
    - Empty graph: returns zeroed-out metrics.
    - Single node: pagerank = {id: 1.0}, 1 community, 1 component.
    - Disconnected graph: per-component community detection.

    Returns a :class:`GraphMetrics` dataclass.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Empty graph — nothing to compute.
    if not graph.nodes:
        return GraphMetrics(computed_at=now)

    G = _build_networkx_graph(graph)

    # Single-node special case (PageRank would still work, but community
    # detection can behave oddly on trivially small graphs).
    if len(G) == 1:
        sole_id = list(G.nodes)[0]
        return GraphMetrics(
            pagerank={sole_id: 1.0},
            communities={sole_id: 0},
            betweenness={sole_id: 0.0},
            closeness={sole_id: 0.0},
            num_communities=1,
            num_components=1,
            computed_at=now,
        )

    # ── PageRank ────────────────────────────────────────────────────────
    pagerank = _compute_with_fallback(
        lambda: nx.pagerank(G, alpha=0.85),
        lambda: {n: 0.0 for n in G.nodes},
        label="PageRank computation",
    )

    # ── Community detection (Louvain) ───────────────────────────────────
    community_sets = _compute_with_fallback(
        lambda: nx.community.louvain_communities(
            G, resolution=1.0, seed=42,
        ),
        lambda: [set(G.nodes)],
        label="Community detection",
    )
    try:
        communities: dict[str, int] = {}
        for idx, members in enumerate(community_sets):
            for node_id in members:
                communities[node_id] = idx
        num_communities = len(community_sets)
    except Exception as exc:
        logger.warning("Community detection failed: %s", exc)
        communities = {n: 0 for n in G.nodes}
        num_communities = 1

    # ── Betweenness centrality ──────────────────────────────────────────
    betweenness = _compute_with_fallback(
        lambda: nx.betweenness_centrality(G, k=min(100, len(G))),
        lambda: {n: 0.0 for n in G.nodes},
        label="Betweenness centrality",
    )

    # ── Closeness centrality ────────────────────────────────────────────
    closeness = _compute_with_fallback(
        lambda: nx.closeness_centrality(G, wf_improved=True),
        lambda: {n: 0.0 for n in G.nodes},
        label="Closeness centrality",
    )

    # ── Connected components ────────────────────────────────────────────
    num_components = nx.number_connected_components(G)

    return GraphMetrics(
        pagerank=pagerank,
        communities=communities,
        betweenness=betweenness,
        closeness=closeness,
        num_communities=num_communities,
        num_components=num_components,
        computed_at=now,
    )
