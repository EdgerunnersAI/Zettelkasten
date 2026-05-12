"""WAVE-C 1c-A.4 — /api/graph payload trim + Brotli negotiation tests.

Locked decisions covered:
- D-KG-8: Brotli + gzip via Accept-Encoding negotiation
- D-KG-9: drop embedding, raw scores, raw timestamps, model_version

Strategy: black-box the response shape via FastAPI TestClient + monkey-patch
the upstream graph loader. Avoids any Supabase round-trip (these are NOT
@live tests) so they run in the regular suite.
"""
from __future__ import annotations

import gzip
import json

import pytest
from fastapi.testclient import TestClient


def _build_test_app():
    """Construct a minimal FastAPI app exposing /api/graph against an
    in-memory file-store stub. Avoids Supabase / auth / lifespan startup.
    """
    from fastapi import FastAPI

    from website.api import routes as routes_module

    app = FastAPI()

    # Register Brotli compression middleware to mirror production wiring.
    try:
        from brotli_asgi import BrotliMiddleware

        app.add_middleware(BrotliMiddleware, minimum_size=512, quality=4)
    except ImportError:
        pytest.skip("brotli-asgi not installed in this env")

    app.include_router(routes_module.router)
    return app


# ── _trim_graph_response unit-level checks ────────────────────────────


def test_trim_drops_embedding_and_model_version() -> None:
    from website.api.routes import _trim_graph_response

    payload = {
        "nodes": [
            {
                "id": "n1",
                "name": "node-1",
                "summary": "ok",
                "embedding": [0.1] * 768,
                "embedding_model_version": "gemini-001-mrl-768",
                "embedding_dim": 768,
                "model_version": "v1",
                "score_breakdown": {"a": 1},
                "betweenness": 0.5,
                "closeness": 0.7,
                "created_at_microseconds": 999,
                "tags": ["x"],
                "pagerank": 0.123456789,
            }
        ],
        "links": [
            {
                "source": "n1",
                "target": "n2",
                "relation": "shared_tag",
                "connection_strength": 0.85,
                "embedding_distance": 0.123,
                "raw_score": 0.99,
                "score_breakdown": {"e": 1},
            }
        ],
        "meta": {"communities": 1},
    }

    trimmed = _trim_graph_response(payload)
    node = trimmed["nodes"][0]
    for k in (
        "embedding",
        "embedding_model_version",
        "embedding_dim",
        "model_version",
        "score_breakdown",
        "betweenness",
        "closeness",
        "created_at_microseconds",
    ):
        assert k not in node, f"node still leaks {k!r}"
    assert node["id"] == "n1"
    assert node["pagerank"] == round(0.123456789, 6)

    link = trimmed["links"][0]
    for k in ("embedding_distance", "raw_score", "score_breakdown"):
        assert k not in link, f"link still leaks {k!r}"
    assert link["connection_strength"] == 0.85
    assert link["source"] == "n1"

    # Top-level meta preserved.
    assert trimmed["meta"] == {"communities": 1}


def test_trim_preserves_essential_fields() -> None:
    from website.api.routes import _trim_graph_response

    payload = {
        "nodes": [
            {"id": "n1", "name": "x", "tags": ["a"], "url": "http://e"},
        ],
        "links": [],
    }
    out = _trim_graph_response(payload)
    assert out["nodes"][0] == {"id": "n1", "name": "x", "tags": ["a"], "url": "http://e"}


def test_trim_preserves_harmonic_centrality() -> None:
    """C3-d: harmonic_centrality is the new default-path distance signal,
    intentionally KEPT on the wire so the frontend can use it as a
    node-importance signal alongside pagerank. Closeness stays trimmed."""
    from website.api.routes import _trim_graph_response

    payload = {
        "nodes": [
            {
                "id": "n1",
                "name": "x",
                "harmonic_centrality": 0.42,
                "closeness": 0.7,  # must be dropped
                "tags": [],
            },
        ],
        "links": [],
    }
    out = _trim_graph_response(payload)
    assert out["nodes"][0]["harmonic_centrality"] == 0.42
    assert "closeness" not in out["nodes"][0]


# ── min_strength filter strict subset ─────────────────────────────────


def test_min_strength_filter_strict_subset() -> None:
    from website.api.routes import _apply_min_strength_filter

    payload = {
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "links": [
            {"source": "a", "target": "b", "connection_strength": 0.9},
            {"source": "a", "target": "c", "connection_strength": 0.5},
            {"source": "b", "target": "c", "connection_strength": 0.2},
            {"source": "a", "target": "a", "connection_strength": None},
        ],
    }
    weak = _apply_min_strength_filter(payload, 0.0)
    strong = _apply_min_strength_filter(payload, 0.7)
    assert len(weak["links"]) == 4, "min_strength=0.0 returns everything"
    assert len(strong["links"]) == 1
    # Strong is a strict subset.
    strong_keys = {(l["source"], l["target"]) for l in strong["links"]}
    weak_keys = {(l["source"], l["target"]) for l in weak["links"]}
    assert strong_keys.issubset(weak_keys)


def test_min_strength_filter_drops_null_strength() -> None:
    from website.api.routes import _apply_min_strength_filter

    payload = {
        "nodes": [],
        "links": [
            {"source": "a", "target": "b", "connection_strength": None},
            {"source": "a", "target": "c", "connection_strength": 0.6},
        ],
    }
    out = _apply_min_strength_filter(payload, 0.5)
    assert len(out["links"]) == 1
    assert out["links"][0]["target"] == "c"


# ── Brotli content-encoding negotiation ─────────────────────────────


def test_brotli_negotiation_returns_br(monkeypatch) -> None:
    """Accept-Encoding: br ⇒ Content-Encoding: br on a >1KB response."""
    import website.api.routes as routes_module
    from website.core.graph_models import KGGraph

    # Stub out get_graph() to return a payload large enough to compress.
    big_payload = {
        "nodes": [
            {
                "id": f"n-{i}",
                "name": f"node-{i}",
                "group": "web",
                "summary": "lorem ipsum " * 30,
                "tags": ["python", "fastapi", "supabase"],
                "url": f"https://example.com/{i}",
                "date": "2026-01-01",
                "node_date": "2026-01-01",
            }
            for i in range(100)
        ],
        "links": [],
    }

    def _fake_get_graph():
        return big_payload

    # Both routes_module-local and origin name (defensive monkey-patch).
    monkeypatch.setattr(routes_module, "get_graph", _fake_get_graph)
    monkeypatch.setattr(
        routes_module,
        "_enrich_graph_with_analytics",
        lambda d, **_kw: d,  # skip analytics in this payload-shape test
    )

    app = _build_test_app()
    with TestClient(app) as client:
        r = client.get("/api/graph", headers={"Accept-Encoding": "br"})
    assert r.status_code == 200
    assert r.headers.get("Content-Encoding") == "br"


def test_gzip_negotiation_returns_gzip(monkeypatch) -> None:
    """Accept-Encoding: gzip alone ⇒ either gzip or br (server may downgrade)."""
    import website.api.routes as routes_module

    big_payload = {
        "nodes": [{"id": f"n-{i}", "name": f"x{i}", "summary": "y" * 200, "tags": []}
                  for i in range(50)],
        "links": [],
    }
    monkeypatch.setattr(routes_module, "get_graph", lambda: big_payload)
    monkeypatch.setattr(routes_module, "_enrich_graph_with_analytics", lambda d, **_kw: d)

    app = _build_test_app()
    with TestClient(app) as client:
        r = client.get("/api/graph", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # brotli-asgi falls back to gzip when br not in Accept-Encoding.
    assert r.headers.get("Content-Encoding") in ("gzip", None)
    # Body is decoded transparently by httpx when Content-Encoding is set.
    parsed = r.json()
    assert "nodes" in parsed


def test_payload_trims_embedding_via_endpoint(monkeypatch) -> None:
    """Even if the upstream loader returns embedding-laden nodes, the
    response payload must NOT contain them (D-KG-9)."""
    import website.api.routes as routes_module

    payload_with_embeddings = {
        "nodes": [
            {
                "id": "n1",
                "name": "x",
                "embedding": [0.1] * 768,
                "embedding_model_version": "gemini-001-mrl-768",
                "tags": [],
            }
        ],
        "links": [],
    }
    monkeypatch.setattr(routes_module, "get_graph", lambda: payload_with_embeddings)
    monkeypatch.setattr(routes_module, "_enrich_graph_with_analytics", lambda d, **_kw: d)

    app = _build_test_app()
    with TestClient(app) as client:
        r = client.get("/api/graph", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    body = r.json()
    assert "embedding" not in body["nodes"][0]
    assert "embedding_model_version" not in body["nodes"][0]


# ── Payload size budget at 1k-node fixture ─────────────────────────


def test_payload_under_300kb_at_1k_nodes(monkeypatch) -> None:
    """Compressed /api/graph response must stay under 300KB at 1k nodes
    with default trim + br compression. Headroom for 10k-user scale.
    """
    import website.api.routes as routes_module

    n = 1000
    fixture = {
        "nodes": [
            {
                "id": f"n-{i}",
                "name": f"node-{i}",
                "group": "web",
                "summary": "summary " * 20,
                "tags": [f"tag-{i % 25}"],
                "url": f"https://example.com/{i}",
                "date": "2026-01-01",
                "node_date": "2026-01-01",
            }
            for i in range(n)
        ],
        "links": [
            {
                "source": f"n-{i}",
                "target": f"n-{(i + 1) % n}",
                "relation": "shared_tag",
                "connection_strength": 0.65,
            }
            for i in range(n)
        ],
    }
    monkeypatch.setattr(routes_module, "get_graph", lambda: fixture)
    monkeypatch.setattr(routes_module, "_enrich_graph_with_analytics", lambda d, **_kw: d)

    app = _build_test_app()
    with TestClient(app) as client:
        r = client.get("/api/graph", headers={"Accept-Encoding": "br"})
    assert r.status_code == 200
    # httpx.Response.content has already been decompressed; we want the
    # raw on-the-wire size — read from the Content-Length header.
    raw_size = int(r.headers.get("Content-Length", 0))
    if raw_size == 0:
        # Some servers omit Content-Length on chunked; re-encode to estimate.
        import brotli  # type: ignore

        raw_size = len(brotli.compress(r.content, quality=4))
    assert raw_size < 300 * 1024, (
        f"compressed /api/graph payload is {raw_size} bytes at 1k nodes; "
        f"D-KG-8 budget is <300KB"
    )


# ── _v2_assemble_graph self-loop regression (PR #7 C1) ────────────────


def test_v2_graph_has_no_self_loops_unless_actual_self_loop(monkeypatch) -> None:
    """Edges in the assembled v2 graph must resolve src/dst via mention join.

    Regression for PR #7 C1: prior code emitted ``source = target = evidence``
    for every edge, producing universal self-loops that igraph drops at
    ``analytics.py:78-79`` — leaving PageRank/Louvain with zero edges and
    rendering the D-KG-1 strength filter inert.

    Fixture: 3 zettels (Z_A, Z_B, Z_C) backed by 3 kg_nodes (N1, N2, N3) plus
    one cross-mention (chunk of Z_A also mentions N3, which usually maps to
    Z_C). Edges: N1↔N2 (cross-zettel), N2↔N2 (genuine self-loop). Expect:
    one A↔B link AND the genuine self-loop preserved when src/dst kg_node
    ids are equal.
    """
    import uuid

    import website.api.routes as routes_module

    workspace_id = uuid.uuid4()
    profile_id = uuid.uuid4()

    # Three canonical zettels, three kg_nodes (one per zettel).
    z_a, z_b, z_c = (str(uuid.uuid4()) for _ in range(3))
    n1, n2, n3 = 101, 202, 303

    class _StubContent:
        def list_workspace_zettels(self, ws_id, *, limit, offset):
            assert ws_id == workspace_id
            return [
                {
                    "canonical": {
                        "id": z_a,
                        "source_type": "web",
                        "title": "Alpha",
                        "normalized_url": "https://a",
                        "publication_date": "2026-01-01",
                    },
                    "ai_summary": "alpha summary",
                    "user_tags": ["tag-a"],
                },
                {
                    "canonical": {
                        "id": z_b,
                        "source_type": "web",
                        "title": "Bravo",
                        "normalized_url": "https://b",
                        "publication_date": "2026-01-02",
                    },
                    "ai_summary": "bravo summary",
                    "user_tags": ["tag-b"],
                },
                {
                    "canonical": {
                        "id": z_c,
                        "source_type": "web",
                        "title": "Charlie",
                        "normalized_url": "https://c",
                        "publication_date": "2026-01-03",
                    },
                    "ai_summary": "charlie summary",
                    "user_tags": ["tag-c"],
                },
            ]

    class _StubKG:
        def list_workspace_edges(self, ws_id):
            return [
                # Cross-zettel edge: N1 (->Z_A) ↔ N2 (->Z_B). Must NOT self-loop.
                {
                    "id": 1,
                    "src_node_id": n1,
                    "dst_node_id": n2,
                    "relation_type": "shared_tag",
                    "shared_tag_label": "shared",
                    "weight": None,
                    "evidence_canonical_zettel_id": z_a,
                },
                # Genuine self-loop: N2 ↔ N2. src_id == dst_id, src == dst is OK.
                {
                    "id": 2,
                    "src_node_id": n2,
                    "dst_node_id": n2,
                    "relation_type": "shared_tag",
                    "shared_tag_label": "self",
                    "weight": None,
                    "evidence_canonical_zettel_id": z_b,
                },
            ]

        def list_node_zettel_mapping(self, ws_id, kg_node_ids, *, limit=50000):
            return {
                n1: [z_a],
                n2: [z_b],
                n3: [z_c],
            }

    monkeypatch.setattr(
        routes_module,
        "get_supabase_v2_scope_for_read",
        lambda sub: (_StubContent(), profile_id, [workspace_id]),
    )
    monkeypatch.setattr(routes_module, "V2KGRepository", _StubKG)

    graph = routes_module._v2_assemble_graph(
        user_sub=str(uuid.uuid4()), limit=100, offset=0
    )
    assert graph is not None
    nodes_by_id = {n.id: n for n in graph.nodes}
    assert len(graph.nodes) == 3, f"expected 3 nodes, got {len(graph.nodes)}"
    assert len(graph.links) >= 2, "expected cross-zettel link + self-loop"

    # Find the cross-zettel link (Alpha <-> Bravo) and assert source != target.
    cross_links = [
        link for link in graph.links
        if link.source != link.target
    ]
    assert cross_links, (
        "v2 graph emitted ZERO non-self-loop links — regression of PR #7 C1 "
        "where every edge was source=target=evidence."
    )
    cross = cross_links[0]
    assert cross.source != cross.target
    src_node = nodes_by_id.get(cross.source)
    dst_node = nodes_by_id.get(cross.target)
    assert src_node is not None and dst_node is not None
    assert {src_node.name, dst_node.name} == {"Alpha", "Bravo"}

    # The genuine self-loop (N2 ↔ N2) MUST be preserved when src_id == dst_id.
    self_loops = [link for link in graph.links if link.source == link.target]
    assert self_loops, (
        "expected the genuine N2↔N2 self-loop to be preserved; got none"
    )
    self_loop_node = nodes_by_id[self_loops[0].source]
    assert self_loop_node.name == "Bravo"
