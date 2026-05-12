"""M3 — Graph Analytics powered by igraph (D-KG-5 migration).

Computes structural metrics (PageRank, communities, harmonic centrality)
over the knowledge graph so the frontend can visualise importance and
clusters.

Why igraph (per D-KG-5): networkx's pure-python pagerank/louvain are
O(V·E) with high constants; igraph's C core runs the same algorithms
~10-100x faster, which matters at our 10k+ scale target. The public
surface (``compute_graph_metrics(graph: KGGraph) -> GraphMetrics``) is
intentionally UNCHANGED — callers in ``website.api.routes`` and the
backfill scripts must not need to be touched.

Locked decisions:
- ``compute_graph_metrics`` returns the canonical PageRank + Louvain +
  harmonic centrality + components + communities. Betweenness is *NOT*
  in the default path — it's O(V·E) and the production droplet
  (2GB/1vCPU) cannot afford it on every /api/graph call. The
  ``betweenness`` field on the returned dataclass is preserved for
  backward compatibility but populated with zeros; callers that need
  it must invoke :func:`compute_expensive_metrics` explicitly.
- C3-d: closeness was DROPPED from the default path and replaced by
  harmonic_centrality (Boldi-Vigna). Rationale: (a) harmonic is
  well-defined on disconnected components which the strong-edge filter
  routinely creates — closeness collapses on disconnected graphs;
  (b) it matches Neo4j's primary distance-centrality metric;
  (c) PKM peers (Logseq, Obsidian Graph) don't render closeness anyway,
  so we lose nothing on the wire. ``closeness`` field on the dataclass
  remains for back-compat (zero-populated); use
  :func:`compute_expensive_metrics` for real closeness.
- C3-d.1: harmonic uses ``cutoff=3``. PKM graphs have diameter ≈4-6, so
  cutoff=2 truncates too aggressively; cutoff=3 captures most distance
  mass while keeping per-source BFS bounded inside the 5k <3s budget.
- Louvain seed=42 (D-KG-1 reproducibility). We pin both Python's
  ``random.seed(42)`` and igraph's internal RNG via
  ``igraph.set_random_number_generator`` so re-runs are byte-identical.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

import igraph as ig

from website.core.graph_models import KGGraph

logger = logging.getLogger(__name__)


# ── Data model ──────────────────────────────────────────────────────────────

@dataclass
class GraphMetrics:
    """Computed graph-level and node-level metrics.

    ``betweenness`` AND ``closeness`` are preserved for backward-compat but
    populated with zeros by :func:`compute_graph_metrics`. Callers that need
    real values must use :func:`compute_expensive_metrics`.

    C3-d: ``harmonic`` (Boldi-Vigna harmonic centrality) is the new
    default-path distance metric, replacing closeness on the wire.
    """

    pagerank: dict[str, float] = field(default_factory=dict)
    communities: dict[str, int] = field(default_factory=dict)
    betweenness: dict[str, float] = field(default_factory=dict)
    closeness: dict[str, float] = field(default_factory=dict)
    # C3-d: harmonic_centrality (Boldi-Vigna) — well-defined on disconnected
    # graphs the strong-edge filter creates; replaces closeness in default path.
    harmonic: dict[str, float] = field(default_factory=dict)
    num_communities: int = 0
    num_components: int = 0
    computed_at: str = ""


# ── Graph construction ──────────────────────────────────────────────────────

def _build_igraph(graph: KGGraph) -> tuple[ig.Graph, list[str]]:
    """Build an undirected igraph graph from a ``KGGraph`` payload.

    Returns ``(igraph.Graph, [node_id_in_order])`` so callers can map vertex
    indices back to the string IDs used by the rest of the system.
    """
    node_ids: list[str] = [n.id for n in graph.nodes]
    if not node_ids:
        return ig.Graph(directed=False), []
    name_to_idx = {nid: i for i, nid in enumerate(node_ids)}
    edges: list[tuple[int, int]] = []
    for link in graph.links:
        si = name_to_idx.get(link.source)
        ti = name_to_idx.get(link.target)
        if si is None or ti is None:
            continue
        if si == ti:  # igraph allows self-loops but they hurt closeness/Louvain
            continue
        edges.append((si, ti))
    g = ig.Graph(n=len(node_ids), edges=edges, directed=False)
    g.vs["name"] = node_ids
    return g, node_ids


def _seed_rng_for_louvain() -> None:
    """Pin the global PRNG igraph reads from for community_multilevel.

    python-igraph 0.11+ delegates to Python's ``random`` for its internal
    RNG when ``set_random_number_generator(random)`` is set, which lets us
    seed deterministically.
    """
    try:
        ig.set_random_number_generator(random)
    except Exception:  # noqa: BLE001 — older igraph may not expose this
        pass
    random.seed(42)


def _safe(label: str, fn, fallback):
    """Run ``fn``; on any exception log+fallback."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — analytics must never abort the request
        logger.warning("%s failed: %s", label, exc)
        return fallback() if callable(fallback) else fallback


def _compute_with_fallback(compute_fn, fallback_fn, *, label: str):
    """Back-compat shim for the pre-igraph public API (tests/kg_intelligence/).

    Wraps :func:`_safe` so the legacy call-shape
    ``_compute_with_fallback(compute, fallback, label=...)`` keeps working
    after the igraph migration. New callers should use ``_safe`` directly.
    """
    return _safe(label, compute_fn, fallback_fn)


# ── Metric computation ──────────────────────────────────────────────────────

def compute_graph_metrics(graph: KGGraph) -> GraphMetrics:
    """Compute structural metrics for the given knowledge graph.

    Handles edge cases:
    - Empty graph: returns zeroed-out metrics.
    - Single node: pagerank = {id: 1.0}, 1 community, 1 component.
    - Disconnected graph: per-component community detection.

    NOTE: ``betweenness`` AND ``closeness`` are intentionally NOT computed
    here (D-KG-5 perf budget — both are O(V·E) and break the 5k <3s budget).
    The fields are populated with zeros for backward compatibility; use
    :func:`compute_expensive_metrics` if you need real values.

    C3-d: ``harmonic`` (Boldi-Vigna harmonic centrality) IS computed in the
    default path. It replaces closeness on the wire because (a) it stays
    well-defined on disconnected components, (b) matches Neo4j's primary
    distance metric, (c) PKM peers don't render closeness anyway. Cost:
    O(V·(V+E)) like closeness, but igraph's C core keeps it inside the
    5k <3s budget for our sparse PKM graphs.
    """
    now = datetime.now(timezone.utc).isoformat()

    if not graph.nodes:
        return GraphMetrics(computed_at=now)

    g, node_ids = _build_igraph(graph)

    if g.vcount() == 1:
        sole = node_ids[0]
        return GraphMetrics(
            pagerank={sole: 1.0},
            communities={sole: 0},
            betweenness={sole: 0.0},
            closeness={sole: 0.0},
            harmonic={sole: 0.0},
            num_communities=1,
            num_components=1,
            computed_at=now,
        )

    # ── PageRank ──────────────────────────────────────────────────────
    def _pagerank() -> dict[str, float]:
        pr = g.pagerank(damping=0.85)
        return {nid: float(pr[i]) for i, nid in enumerate(node_ids)}

    pagerank = _safe(
        "PageRank", _pagerank,
        lambda: {nid: 0.0 for nid in node_ids},
    )

    # ── Communities (Louvain, seed=42) ────────────────────────────────
    def _louvain() -> tuple[dict[str, int], int]:
        _seed_rng_for_louvain()
        partition = g.community_multilevel(weights=None, return_levels=False)
        membership = partition.membership
        return (
            {nid: int(membership[i]) for i, nid in enumerate(node_ids)},
            len(set(membership)),
        )

    communities, num_communities = _safe(
        "Louvain community detection",
        _louvain,
        lambda: ({nid: 0 for nid in node_ids}, 1),
    )

    # ── Components ───────────────────────────────────────────────────
    num_components = _safe(
        "Connected components count",
        lambda: int(len(g.connected_components(mode="weak"))),
        1,
    )

    # ── Harmonic centrality (C3-d.1) ─────────────────────────────────
    # cutoff=3: PKM graph diameter is typically 4-6, so cutoff=2 truncates
    # too aggressively while cutoff=3 captures most of the distance mass
    # (1/1 + 1/2 + 1/3 ≈ 1.83 of the unbounded ≤2.0 ceiling for typical
    # neighbourhoods) and keeps the C-core BFS bounded for the 5k <3s budget.
    def _harmonic() -> dict[str, float]:
        if hasattr(g, "harmonic_centrality"):
            hc = g.harmonic_centrality(mode="all", cutoff=3, normalized=True)
            return {nid: float(hc[i]) for i, nid in enumerate(node_ids)}
        # Fallback: sum(1/d for d in [1..3]) / (n-1) — matches cutoff=3.
        n = g.vcount()
        if n <= 1:
            return {nid: 0.0 for nid in node_ids}
        sp = g.shortest_paths()
        denom = float(n - 1)
        out: dict[str, float] = {}
        for i, nid in enumerate(node_ids):
            row = sp[i]
            s = 0.0
            for j, d in enumerate(row):
                if i == j:
                    continue
                if d == float("inf") or d == 0 or d > 3:
                    continue
                s += 1.0 / d
            out[nid] = s / denom
        return out

    harmonic = _safe(
        "Harmonic centrality", _harmonic,
        lambda: {nid: 0.0 for nid in node_ids},
    )

    return GraphMetrics(
        pagerank=pagerank,
        communities=communities,
        # Backward-compat sentinels: zeros so existing callers don't KeyError.
        # See compute_expensive_metrics() for real betweenness + closeness.
        betweenness={nid: 0.0 for nid in node_ids},
        closeness={nid: 0.0 for nid in node_ids},
        harmonic=harmonic,
        num_communities=num_communities,
        num_components=num_components,
        computed_at=now,
    )


def compute_expensive_metrics(graph: KGGraph) -> GraphMetrics:
    """Compute the same metrics PLUS betweenness AND closeness centrality.

    Opt-in expensive companion to :func:`compute_graph_metrics`. Use only
    on offline / admin paths — DO NOT invoke from the per-request
    ``/api/graph`` handler. Both metrics are O(V·E) on connected graphs
    and break the 5k <3s budget that protects the 2GB / 1vCPU droplet.
    """
    base = compute_graph_metrics(graph)
    if not graph.nodes:
        return base

    g, node_ids = _build_igraph(graph)
    if g.vcount() == 1:
        return base

    def _betweenness() -> dict[str, float]:
        bt = g.betweenness()
        # Normalise to [0, 1] as networkx did, for callers expecting that scale.
        n = len(node_ids)
        norm = max(1, (n - 1) * (n - 2) / 2) if n > 2 else 1
        return {nid: float(bt[i]) / norm for i, nid in enumerate(node_ids)}

    def _closeness() -> dict[str, float]:
        cl = g.closeness(normalized=True)
        return {nid: float(cl[i] if cl[i] is not None else 0.0)
                for i, nid in enumerate(node_ids)}

    base.betweenness = _safe(
        "Betweenness centrality", _betweenness,
        lambda: {nid: 0.0 for nid in node_ids},
    )
    base.closeness = _safe(
        "Closeness centrality", _closeness,
        lambda: {nid: 0.0 for nid in node_ids},
    )
    return base
