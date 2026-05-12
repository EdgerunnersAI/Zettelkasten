"""WAVE-C 1c-A.5 — Migrate analytics.py from networkx to igraph.

Locked decision **D-KG-5**: igraph migration NOW.

Invariants:
- Public surface of ``compute_graph_metrics`` is UNCHANGED (signature +
  return shape) — callers must not need to change.
- Betweenness is DROPPED from the default; exposed via
  ``compute_expensive_metrics`` as a separate function.
- Louvain seed=42 determinism (D-KG-1 reproducibility).
- Performance budget per D3 STRICT: 1k <500ms, 5k <3s, 10k <10s.
- Edge cases: empty / single-node / disconnected components.
"""
from __future__ import annotations

import time

import pytest

from website.core.graph_models import KGGraph, KGGraphLink, KGGraphNode
from website.features.kg_features.analytics import (
    GraphMetrics,
    compute_expensive_metrics,
    compute_graph_metrics,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _erdos_renyi_kggraph(n: int, p: float = 0.01, seed: int = 42) -> KGGraph:
    """Build a deterministic Erdős–Rényi KGGraph for perf benchmarks.

    Uses sparse sampling — for large n with low p, picks a Poisson-approx
    number of edges and samples directly, avoiding the O(n²) all-pairs
    rejection loop that dominates fixture build time at n=5k+.
    """
    import math
    import random

    rng = random.Random(seed)
    nodes = [
        KGGraphNode(id=f"n-{i}", name=f"node-{i}", group="web", url=f"http://e/{i}")
        for i in range(n)
    ]
    expected_edges = int(p * n * (n - 1) / 2)
    seen: set[tuple[int, int]] = set()
    links: list[KGGraphLink] = []
    while len(links) < expected_edges:
        a = rng.randrange(n)
        b = rng.randrange(n)
        if a == b:
            continue
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        links.append(
            KGGraphLink(source=f"n-{key[0]}", target=f"n-{key[1]}", relation="shared_tag")
        )
    return KGGraph(nodes=nodes, links=links)


def _filtered_subgraph(g: KGGraph, fraction: float, seed: int = 42) -> KGGraph:
    """Deterministically retain ``fraction`` of links to model the strong-edge
    subgraph produced by C3-d.4 ``min_strength`` filter at runtime.

    KGGraphLink does not carry ``connection_strength`` on the dataclass surface
    (that field is added downstream in the dict payload), so we model the
    post-filter graph by sampling the link list directly. fraction=0.3 mirrors
    a typical ≥0.7 threshold yield on a real PKM graph.
    """
    import random

    rng = random.Random(seed)
    keep = max(1, int(len(g.links) * fraction))
    chosen = rng.sample(g.links, keep) if g.links else []
    return KGGraph(nodes=g.nodes, links=chosen)


# ── Public surface invariants ──────────────────────────────────────────


def test_compute_graph_metrics_signature_unchanged() -> None:
    """The function still takes a single KGGraph and returns GraphMetrics."""
    import inspect

    sig = inspect.signature(compute_graph_metrics)
    params = list(sig.parameters)
    assert params == ["graph"]
    assert sig.parameters["graph"].annotation is KGGraph or "KGGraph" in str(
        sig.parameters["graph"].annotation
    )


def test_compute_graph_metrics_default_drops_betweenness_and_closeness() -> None:
    """Default ``compute_graph_metrics`` MUST NOT compute betweenness OR
    closeness — both are O(V·E) and the production droplet (2GB / 1vCPU)
    cannot afford them on every /api/graph call.

    C3-d: closeness replaced by harmonic_centrality on the default path
    (Boldi-Vigna; well-defined on disconnected graphs the strong-edge
    filter creates). Closeness still populated with zeros for back-compat.
    """
    g = _erdos_renyi_kggraph(n=100, p=0.05)
    m = compute_graph_metrics(g)
    # All-zero sentinels = "not computed".
    assert all(v == 0.0 for v in m.betweenness.values())
    assert all(v == 0.0 for v in m.closeness.values())
    # Harmonic centrality IS computed on the default path now.
    assert m.harmonic, "harmonic_centrality must be populated by default"
    assert len(m.harmonic) == 100
    # All values in [0, 1] (igraph normalised harmonic).
    assert all(0.0 <= v <= 1.0 for v in m.harmonic.values())


def test_harmonic_centrality_in_default_path() -> None:
    """C3-d: harmonic centrality replaces closeness on default path.

    On a 1k-node ER graph: every node has a harmonic value in [0, 1] and
    at least one node is nonzero (graph is sparsely connected, not empty)."""
    g = _erdos_renyi_kggraph(n=1000, p=0.005)
    m = compute_graph_metrics(g)
    assert len(m.harmonic) == 1000
    assert all(0.0 <= v <= 1.0 for v in m.harmonic.values())
    assert any(v > 0.0 for v in m.harmonic.values()), (
        "expected at least one nonzero harmonic value on a connected ER(1k, 0.005)"
    )


def test_compute_expensive_metrics_returns_betweenness() -> None:
    """The opt-in expensive function exposes betweenness for callers that
    explicitly ask for it (admin / offline analytics)."""
    g = _erdos_renyi_kggraph(n=100, p=0.05)
    m = compute_expensive_metrics(g)
    # On a connected ER(100, 0.05) graph there's >1 distinct value.
    distinct = len(set(round(v, 4) for v in m.betweenness.values()))
    assert distinct >= 2


# ── Edge cases ────────────────────────────────────────────────────────


def test_empty_graph_returns_zeroed_metrics() -> None:
    g = KGGraph(nodes=[], links=[])
    m = compute_graph_metrics(g)
    assert m.pagerank == {}
    assert m.communities == {}
    assert m.num_communities == 0
    assert m.num_components == 0
    assert m.computed_at != ""


def test_single_node_graph() -> None:
    g = KGGraph(nodes=[KGGraphNode(id="solo", name="solo", group="web", url="x")], links=[])
    m = compute_graph_metrics(g)
    assert m.pagerank == {"solo": 1.0}
    assert m.communities == {"solo": 0}
    assert m.num_communities == 1
    assert m.num_components == 1


def test_disconnected_components_counted() -> None:
    nodes = [
        KGGraphNode(id="a", name="a", group="web", url=""),
        KGGraphNode(id="b", name="b", group="web", url=""),
        KGGraphNode(id="c", name="c", group="web", url=""),
        KGGraphNode(id="d", name="d", group="web", url=""),
    ]
    links = [
        KGGraphLink(source="a", target="b", relation="shared_tag"),
        KGGraphLink(source="c", target="d", relation="shared_tag"),
    ]
    g = KGGraph(nodes=nodes, links=links)
    m = compute_graph_metrics(g)
    assert m.num_components == 2


# ── Determinism ──────────────────────────────────────────────────────


def test_louvain_seed_42_determinism() -> None:
    """Same input → same community partition across runs (D-KG-1 invariant).

    NOTE: igraph's community_multilevel takes a non-deterministic seed
    parameter via ``random.seed`` set on the python-igraph ``set_random_number_generator``
    OR via the global PRNG. We pin a global ``random.seed(42)`` before the
    call inside compute_graph_metrics.
    """
    g = _erdos_renyi_kggraph(n=200, p=0.05)
    m1 = compute_graph_metrics(g)
    m2 = compute_graph_metrics(g)
    assert m1.communities == m2.communities
    assert m1.num_communities == m2.num_communities


# ── Perf budgets (D3 STRICT) ─────────────────────────────────────────
# Skip on CI runners that aren't representative of the production droplet
# (2GB / 1vCPU); the local dev box is faster, so this is a ceiling check.


@pytest.mark.parametrize("n,budget_seconds", [(1000, 0.5), (5000, 3.0)])
def test_perf_budget_pagerank_louvain(n, budget_seconds) -> None:
    """1k <500ms, 5k <3s on a typical dev box. Production droplet has less
    headroom; this is a regression guard, not a hard SLO."""
    g = _erdos_renyi_kggraph(n=n, p=min(0.01, 50.0 / n))
    t0 = time.perf_counter()
    m = compute_graph_metrics(g)
    elapsed = time.perf_counter() - t0
    assert elapsed < budget_seconds, (
        f"compute_graph_metrics(n={n}) took {elapsed:.2f}s; "
        f"budget {budget_seconds}s"
    )
    assert isinstance(m, GraphMetrics)


@pytest.mark.slow
def test_perf_budget_10k_drops_betweenness() -> None:
    """10k nodes <10s — succeeds because betweenness is now dropped."""
    g = _erdos_renyi_kggraph(n=10_000, p=0.0005)
    t0 = time.perf_counter()
    m = compute_graph_metrics(g)
    elapsed = time.perf_counter() - t0
    assert elapsed < 10.0, f"10k-node compute took {elapsed:.2f}s; budget 10s"
    # Must NOT have computed betweenness in the default path.
    assert all(v == 0.0 for v in m.betweenness.values())
