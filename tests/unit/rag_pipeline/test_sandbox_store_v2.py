"""Unit tests for the v2 SandboxStore (Phase 2.6).

Verifies:
* CRUD lands on `rag.kastens` via RAGRepository v2 helpers.
* `list_members` uses `rag.list_kasten_zettels` RPC (NOT the legacy
  nested PostgREST embed `select("..., kg_nodes(...)")`).
* `add_members` uses `rag.bulk_add_to_kasten` RPC.
* No `supabase_kg.client.get_supabase_client` import remains.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from website.features.rag_pipeline.memory.sandbox_store import SandboxStore


def _make_repo_mock() -> MagicMock:
    repo = MagicMock()
    return repo


@pytest.mark.asyncio
async def test_create_sandbox_uses_create_kasten():
    repo = _make_repo_mock()
    expected = {"id": str(uuid4()), "name": "test-kasten"}
    repo.create_kasten.return_value = expected
    store = SandboxStore(repo=repo)

    workspace_id = uuid4()
    out = await store.create_sandbox(
        workspace_id=workspace_id,
        name="test-kasten",
        description="d",
        icon="i",
        color="c",
        default_quality="high",
    )
    assert out == expected
    repo.create_kasten.assert_called_once_with(
        workspace_id=workspace_id,
        name="test-kasten",
        description="d",
        icon="i",
        color="c",
        default_quality="high",
    )


@pytest.mark.asyncio
async def test_list_members_uses_list_kasten_zettels_rpc():
    """Replaces legacy nested PostgREST embed with the JOIN RPC."""
    repo = _make_repo_mock()
    rows = [
        {
            "workspace_zettel_id": str(uuid4()),
            "canonical_zettel_id": str(uuid4()),
            "title": "Z",
            "source_type": "web",
            "user_tags": ["a"],
            "ai_summary": "s",
            "added_at": "2026-05-09T00:00:00Z",
        }
    ]
    repo.list_kasten_zettels.return_value = rows
    store = SandboxStore(repo=repo)

    sandbox_id = uuid4()
    out = await store.list_members(sandbox_id, uuid4(), limit=500)
    assert out == rows
    repo.list_kasten_zettels.assert_called_once_with(sandbox_id)


@pytest.mark.asyncio
async def test_add_members_uses_bulk_add_rpc():
    repo = _make_repo_mock()
    repo.add_zettels_to_kasten.return_value = 3
    store = SandboxStore(repo=repo)

    sandbox_id = uuid4()
    workspace_id = uuid4()
    wz_ids = [uuid4(), uuid4(), uuid4()]

    n = await store.add_members(
        sandbox_id=sandbox_id,
        workspace_id=workspace_id,
        workspace_zettel_ids=wz_ids,
    )
    assert n == 3
    repo.add_zettels_to_kasten.assert_called_once_with(
        kasten_id=sandbox_id,
        workspace_zettel_ids=wz_ids,
    )


@pytest.mark.asyncio
async def test_add_members_empty_list_is_zero():
    repo = _make_repo_mock()
    store = SandboxStore(repo=repo)
    n = await store.add_members(
        sandbox_id=uuid4(), workspace_id=uuid4(), workspace_zettel_ids=[],
    )
    assert n == 0
    repo.add_zettels_to_kasten.assert_not_called()


@pytest.mark.asyncio
async def test_delete_sandbox_uses_delete_kasten():
    repo = _make_repo_mock()
    repo.delete_kasten.return_value = True
    store = SandboxStore(repo=repo)
    sid, wid = uuid4(), uuid4()
    assert await store.delete_sandbox(sid, wid) is True
    repo.delete_kasten.assert_called_once_with(sid, wid)


@pytest.mark.asyncio
async def test_remove_member_uses_remove_zettel_from_kasten():
    repo = _make_repo_mock()
    repo.remove_zettel_from_kasten.return_value = True
    store = SandboxStore(repo=repo)
    sid, wid, zid = uuid4(), uuid4(), uuid4()
    assert await store.remove_member(sid, wid, zid) is True
    repo.remove_zettel_from_kasten.assert_called_once_with(
        kasten_id=sid, workspace_zettel_id=zid, workspace_id=wid,
    )


@pytest.mark.asyncio
async def test_no_supabase_kg_import():
    """Forensic guard — phase 2 grep gate."""
    import website.features.rag_pipeline.memory.sandbox_store as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "from website.core.supabase_kg" not in src
    # Also: legacy nested embed must be gone (allow docstring mention).
    # Filter out triple-quoted strings before scanning.
    import re
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    assert ".select(" not in code_only or "kg_nodes" not in code_only


@pytest.mark.asyncio
async def test_back_compat_supabase_kwarg_ignored():
    """Constructor must still accept ``supabase=`` kwarg (legacy callers)."""
    repo = _make_repo_mock()
    fake_client = MagicMock()
    store = SandboxStore(supabase=fake_client, repo=repo)
    # Internal repo is the one we passed, not derived from the supabase shim.
    assert store._repo is repo
