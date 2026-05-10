"""Iter-03 fix: hybrid_search must coerce UUID-typed user_id and seed_node_id
to strings before sending the Supabase RPC payload.

Bug observed in prod (2026-04-28 16:5x UTC, container zettelkasten-green):
ERROR:website.features.kg_features.retrieval:hybrid_kg_search RPC failed:
Object of type UUID is not JSON serializable

Caused every hybrid retrieval to fail and fall back to pure semantic search,
inflating Gemini retry pressure and contributing to worker OOM under q1 load.
"""
from __future__ import annotations

import pytest

# 8.0-H7: kg_features.retrieval was hard-deleted (v1 hybrid_kg_search RPC
# referenced dropped public.kg_nodes/kg_links). The UUID-coercion fix this
# test pinned no longer applies — v2 retrieval coerces in
# website/features/rag_pipeline/retrieval/hybrid.py.
pytest.skip(
    "v1 kg_features.retrieval retired in Phase 8.0 H7; UUID coercion now "
    "handled in v2 HybridRetriever (see "
    "docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md)",
    allow_module_level=True,
)

from unittest.mock import MagicMock  # noqa: E402
from uuid import UUID  # noqa: E402

from website.features.kg_features import retrieval  # noqa: E402


def _stub_client():
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = []
    return client


def test_uuid_user_id_is_coerced_to_str():
    client = _stub_client()
    user_uuid = UUID("8842e563-ee10-4b8b-bbf2-8af4ba65888e")
    retrieval.hybrid_search(client, user_id=user_uuid, query="hello")
    call_kwargs = client.rpc.call_args.args[1]
    assert isinstance(call_kwargs["p_user_id"], str), (
        "hybrid_search must coerce user_id to str before RPC; UUID objects "
        "raise 'Object of type UUID is not JSON serializable' in supabase-py."
    )
    assert call_kwargs["p_user_id"] == "8842e563-ee10-4b8b-bbf2-8af4ba65888e"


def test_str_user_id_unchanged():
    client = _stub_client()
    retrieval.hybrid_search(client, user_id="abc-123", query="hi")
    assert client.rpc.call_args.args[1]["p_user_id"] == "abc-123"


def test_uuid_seed_node_id_is_coerced_to_str():
    client = _stub_client()
    seed = UUID("227e0fb2-ff81-4d08-8702-76d9235564f4")
    retrieval.hybrid_search(client, user_id="u", query="hi", seed_node_id=seed)
    call_kwargs = client.rpc.call_args.args[1]
    assert isinstance(call_kwargs["p_seed_node_id"], str)
    assert call_kwargs["p_seed_node_id"] == "227e0fb2-ff81-4d08-8702-76d9235564f4"


def test_none_user_id_stays_none():
    client = _stub_client()
    retrieval.hybrid_search(client, user_id=None, query="hi")
    assert client.rpc.call_args.args[1]["p_user_id"] is None
