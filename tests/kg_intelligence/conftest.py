"""Shared fixtures for KG Intelligence Layer (M1–M6) tests.

Provides mock Supabase clients, sample KGGraph instances, and a
stubbed settings object so that modules which call ``get_settings()``
at import/use time don't trigger ``SystemExit(1)``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from website.core.graph_models import (
    KGGraph,
    KGGraphLink,
    KGGraphNode,
)


# ── Settings stub ───────────────────────────────────────────────────────────

@pytest.fixture
def stub_settings():
    """A minimal Settings-like object with a fake Gemini API key."""
    return SimpleNamespace(gemini_api_key="fake-key-for-tests")


# ── Supabase client mock ────────────────────────────────────────────────────

@pytest.fixture
def mock_supabase_client():
    """Return a MagicMock supporting the ``.rpc(name, args).execute()`` chain.

    Default behaviour: ``execute()`` returns an object with ``.data = []``.
    Tests can override by setting
    ``client.rpc.return_value.execute.return_value.data = [...]``.
    """
    client = MagicMock()
    rpc_call = MagicMock()
    execute_result = MagicMock()
    execute_result.data = []
    rpc_call.execute.return_value = execute_result
    client.rpc.return_value = rpc_call
    return client


# ── Sample graphs ───────────────────────────────────────────────────────────

def _mk_node(node_id: str, name: str | None = None) -> KGGraphNode:
    return KGGraphNode(
        id=node_id,
        name=name or node_id,
        group="web",
        summary="",
        tags=[],
        url=f"https://example.com/{node_id}",
        date="",
    )


def _mk_link(source: str, target: str, relation: str = "shared_tag") -> KGGraphLink:
    return KGGraphLink(source=source, target=target, relation=relation)


@pytest.fixture
def empty_graph() -> KGGraph:
    return KGGraph(nodes=[], links=[])


@pytest.fixture
def triangle_graph() -> KGGraph:
    """3-node fully-connected triangle graph (1 component, 1 community)."""
    nodes = [_mk_node("a"), _mk_node("b"), _mk_node("c")]
    links = [
        _mk_link("a", "b"),
        _mk_link("b", "c"),
        _mk_link("c", "a"),
    ]
    return KGGraph(nodes=nodes, links=links)


@pytest.fixture
def disconnected_graph() -> KGGraph:
    """4 nodes in 2 disconnected pairs (2 components)."""
    nodes = [_mk_node("a"), _mk_node("b"), _mk_node("c"), _mk_node("d")]
    links = [
        _mk_link("a", "b"),
        _mk_link("c", "d"),
    ]
    return KGGraph(nodes=nodes, links=links)
