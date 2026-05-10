"""Phase 8.0 H9 — sandbox member serializer reads v2 RPC shape.

The v1 PostgREST embed returned ``row["kg_nodes"]`` from a nested
join against the dropped ``public.kg_nodes`` table. The v2 RPC
``rag.list_kasten_zettels`` returns a flat shape with columns
(workspace_zettel_id, canonical_zettel_id, title, source_type,
user_tags, ai_summary, added_at) — no nested embed key. Serializers
must not rely on the legacy embed key.
"""
from __future__ import annotations

import importlib
import inspect


def _load_modules():
    api_init = importlib.import_module("website.api")
    sandbox_routes = importlib.import_module("website.api.sandbox_routes")
    return api_init, sandbox_routes


def test_serialize_member_does_not_read_v1_kg_nodes_embed():
    """Both serializers must drop the v1 ``row.get("kg_nodes")`` lookup."""
    api_init, sandbox_routes = _load_modules()
    for mod in (api_init, sandbox_routes):
        src = inspect.getsource(mod)
        assert 'row.get("kg_nodes")' not in src, (
            f"{mod.__name__}: v1 PostgREST embed key 'kg_nodes' remains"
        )
        assert '.table("kg_nodes")' not in src, (
            f"{mod.__name__}: dropped public.kg_nodes table reference remains"
        )


def test_serialize_member_consumes_v2_flat_rpc_columns():
    """Smoke-test: feed a representative ``rag.list_kasten_zettels`` row
    into each ``_serialize_member`` and assert the response surfaces the
    v2 fields (title, source_type, user_tags) rather than crashing on a
    missing nested key."""
    api_init, sandbox_routes = _load_modules()

    sample_v2_row = {
        "workspace_zettel_id": "11111111-1111-1111-1111-111111111111",
        "canonical_zettel_id": "22222222-2222-2222-2222-222222222222",
        "title": "Example Zettel",
        "source_type": "youtube",
        "user_tags": ["alpha", "beta"],
        "ai_summary": "summary text",
        "added_at": "2026-05-10T00:00:00+00:00",
    }

    for mod in (api_init, sandbox_routes):
        out = mod._serialize_member(sample_v2_row)
        assert isinstance(out, dict)
        node = out.get("node") or {}
        assert node.get("name") == "Example Zettel", (
            f"{mod.__name__}: title not surfaced as node.name"
        )
        assert node.get("source_type") == "youtube", (
            f"{mod.__name__}: source_type not surfaced"
        )
        assert sorted(node.get("tags") or []) == ["alpha", "beta"], (
            f"{mod.__name__}: user_tags not surfaced as node.tags"
        )
