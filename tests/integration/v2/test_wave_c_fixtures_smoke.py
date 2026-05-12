"""WAVE-C Phase 1a smoke tests.

Exercises each of the 5 new fixtures once before Phase 1b/1c sub-agents
dispatch. These tests are **not** ``@pytest.mark.live`` because none of them
hit the network or Supabase — they only validate the fixture plumbing in
isolation. Marked ``@pytest.mark.live`` would gate them off the default
local run, defeating the smoke purpose.

Anti-pattern guard reminder:
  * No entitlement seeding.
  * No SQL function-body changes.
  * No auth bypass.
  * No protected-knob mutation.

Each fixture has exactly one focused assertion path.
"""
from __future__ import annotations

from datetime import timedelta

import pytest


# ---------------------------------------------------------------------------
# 1. mock_gemini_pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_gemini_pool_records_calls(mock_gemini_pool):
    """Stub returns deterministic embeddings + records (key, model, hash)."""
    stub = mock_gemini_pool(embedding_dim=64)

    resp_a = stub.embed_content("hello world")
    resp_b = stub.embed_content("hello world")  # same input → same key_index?
    resp_c = stub.embed_content("different content")

    # Vector dim respected.
    assert len(resp_a.embeddings[0].values) == 64
    # Same input ⇒ same vector (deterministic SHA-based stub).
    assert resp_a.embeddings[0].values == resp_b.embeddings[0].values
    # Different input ⇒ different vector (collision is astronomically unlikely
    # but the assertion is still a real check on the stub's hash plumbing).
    assert resp_a.embeddings[0].values != resp_c.embeddings[0].values

    # Three calls recorded.
    assert len(stub.calls) == 3
    assert all(c.method == "embed_content" for c in stub.calls)
    assert {c.key_index for c in stub.calls} <= {0, 1}


@pytest.mark.asyncio
async def test_mock_gemini_pool_force_429_and_cooldown(mock_gemini_pool):
    """force_429_after raises once; inject_cooldown skips the cooled slot."""
    stub = mock_gemini_pool(force_429_after=1)

    # First generate succeeds.
    resp1, model1, ki1 = await stub.generate_content("first", label="t1")
    assert "stub" in resp1.text

    # Second generate raises a stub 429.
    with pytest.raises(Exception) as exc_info:
        await stub.generate_content("second", label="t2")
    assert getattr(exc_info.value, "is_429", False) is True

    # Cooldown injection: pin key 0 / flash for 60s, next generate must use
    # key 1 (or fall back through the chain).
    fresh = mock_gemini_pool()
    fresh.inject_cooldown(
        key_index=0, model="gemini-2.5-flash", cooldown_seconds=60.0
    )
    resp, _, ki = await fresh.generate_content("after-cooldown")
    assert ki != 0 or _ != "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# 2. recorded_source_fixtures
# ---------------------------------------------------------------------------


def test_recorded_source_fixtures_path_resolution(recorded_source_fixtures, tmp_path):
    """Phase 1a: directories exist with .gitkeep but no cassette files yet,
    so loading must raise FileNotFoundError with the exact expected path."""
    from tests.v2.fixtures.wave_c import SOURCE_INGEST_NAMES, SourceFixturePathResolver

    # All 10 source dirs exist (registry-completeness gate).
    for source in SOURCE_INGEST_NAMES:
        assert SourceFixturePathResolver.path_for(
            source=source
        ).parent.is_dir(), f"missing dir for source {source}"

    # Loading a missing cassette surfaces a clear FileNotFoundError so the
    # next-phase sub-agent knows which file to provide.
    with pytest.raises(FileNotFoundError, match="No recorded fixture at"):
        recorded_source_fixtures(source="github", scenario="happy")

    # Unknown source name is rejected at path resolution.
    with pytest.raises(ValueError, match="Unknown source"):
        recorded_source_fixtures(source="not-a-source")


# ---------------------------------------------------------------------------
# 3. nx_graph_factory
# ---------------------------------------------------------------------------


def test_nx_graph_factory_deterministic(nx_graph_factory):
    """Same seed ⇒ same graph (node count, edge set, weights)."""
    import networkx as nx

    g1 = nx_graph_factory(n=50, p=0.1, seed=42)
    g2 = nx_graph_factory(n=50, p=0.1, seed=42)
    g3 = nx_graph_factory(n=50, p=0.1, seed=99)

    assert isinstance(g1, nx.DiGraph)
    assert g1.number_of_nodes() == 50
    assert set(g1.edges()) == set(g2.edges()), "same seed must reproduce edges"
    # Different seed ⇒ different edge set (with overwhelming probability at
    # n=50 / p=0.1 — birthday-style collision is negligible).
    assert set(g1.edges()) != set(g3.edges())
    # Weights present + deterministic.
    for u, v in list(g1.edges())[:5]:
        assert g1[u][v]["weight"] == g2[u][v]["weight"]


# ---------------------------------------------------------------------------
# 4. graph_json_loader
# ---------------------------------------------------------------------------


def test_graph_json_loader_validates(graph_json_loader):
    """Default path resolves and the existing graph.json passes the schema."""
    payload = graph_json_loader()
    assert isinstance(payload, dict)
    assert "nodes" in payload and "links" in payload
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["links"], list)


def test_graph_json_loader_rejects_bad_payload(graph_json_loader, tmp_path):
    """Schema mismatch raises a clear ValueError."""
    bad = tmp_path / "broken.json"
    bad.write_text('{"nodes": [{"id": "a"}], "links": [{"source": "a"}]}')
    with pytest.raises(ValueError, match="link"):
        graph_json_loader(path=bad)


# ---------------------------------------------------------------------------
# 5. frozen_clock
# ---------------------------------------------------------------------------


def test_frozen_clock_advances_without_sleep(frozen_clock):
    """Time moves only when frozen_clock.tick() is called — no real sleep."""
    from datetime import datetime, timezone

    t0 = datetime.now(timezone.utc)
    assert t0.year == 2026 and t0.month == 5 and t0.day == 12

    frozen_clock.tick(timedelta(seconds=30))
    t1 = datetime.now(timezone.utc)
    assert (t1 - t0) == timedelta(seconds=30)

    frozen_clock.tick(timedelta(minutes=5))
    t2 = datetime.now(timezone.utc)
    assert (t2 - t0) == timedelta(seconds=330)
