"""Phase 8.0.4 - routes.py v1-retirement regression for tasks 4a/4b/4e.

Tasks 4c (/api/graph/query 410) and 4d (/api/graph/rebuild-links delete)
are covered by ``test_retired_graph_endpoints.py``; this file ratchets
that no v1-schema or v1 file-store fallback re-appears for the three
remaining handlers:

  4a - GET    /api/me              (no kg_users / get_user_by_render_id)
  4b - GET    /api/graph           (no v1 KGRepository.get_graph fallback)
  4e - DELETE /api/zettels/{id}    (no v1 KGRepository.delete_node and
                                    no file-store delete_graph_node fallback)

These are source-shape ratchets, not behavioural tests - the wire-shape
behaviour of the v2 paths is covered by the dedicated v2 endpoint test
files (test_api_me_v2.py, test_api_graph_v2.py, test_api_zettels_v2.py).
"""
from __future__ import annotations

import inspect


def _routes_source() -> str:
    from website.api import routes

    return inspect.getsource(routes)


# --- 4a: /api/me ----------------------------------------------------------

def test_api_me_no_kg_users_fallback():
    """`/api/me` must not read from dropped public.kg_users."""
    src = _routes_source()
    assert "kg_user.avatar_url" not in src, (
        "v1 kg_user.avatar_url access must be removed; v2 reads core.profiles"
    )
    assert "get_user_by_render_id" not in src, (
        "v1 KGRepository.get_user_by_render_id call must be removed"
    )


def test_api_me_no_get_supabase_helper():
    """The retired _get_supabase scope helper must not return."""
    src = _routes_source()
    assert "_get_supabase(" not in src, (
        "Retired _get_supabase helper must not reappear in routes.py"
    )


# --- 4b: /api/graph -------------------------------------------------------

def test_api_graph_no_v1_kgrepository_fallback():
    """`/api/graph` must not call the v1 KGRepository.get_graph(...) path."""
    src = _routes_source()
    assert "repo.get_graph(" not in src, (
        "v1 KGRepository.get_graph fallback must be removed; v2 path is the "
        "only DB path and the file-store is the anonymous public surface"
    )
    # Match the bare v1 class instantiation, not V2KGRepository().
    import re

    assert re.search(r"(?<![A-Za-z0-9_])KGRepository\(", src) is None, (
        "v1 KGRepository must not be instantiated in routes.py - v2 uses "
        "V2KGRepository / ContentRepository / CoreRepository"
    )


def test_api_graph_keeps_file_store_anonymous_path():
    """File-store get_graph() is the canonical anonymous surface - it stays."""
    src = _routes_source()
    # The import line + at least one call site for the global/anonymous path.
    assert "from website.core.graph_store import" in src
    assert "get_graph()" in src, (
        "Anonymous /api/graph callers must still serve file-store get_graph()"
    )


# --- 4e: DELETE /api/zettels/{id} -----------------------------------------

def test_delete_zettel_no_v1_repo_fallback():
    """`delete_zettel` must not call v1 KGRepository.delete_node."""
    src = _routes_source()
    assert "repo.delete_node(" not in src, (
        "v1 KGRepository.delete_node fallback must be removed - v2 uses "
        "ContentRepository.soft_delete_workspace_zettel"
    )


def test_delete_zettel_no_file_store_fallback():
    """`delete_zettel` (auth-required) must not fall back to file-store."""
    src = _routes_source()
    assert "delete_graph_node" not in src, (
        "File-store delete_graph_node import/call must be removed from "
        "routes.py - the file-store is the public/anonymous read surface, "
        "not a user-owned write target. v1-shaped node ids should 4xx."
    )
