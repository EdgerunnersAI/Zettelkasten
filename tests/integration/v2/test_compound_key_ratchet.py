"""Compound-key ratchet — proves app-layer tenant filter blocks cross-tenant
mutation even when RLS is bypassed.

The repository methods on rag_repository.RAGRepository and
content_repository.ContentRepository chain ``.eq("workspace_id", ws_id)`` after
``.eq("id", row_id)`` so a caller passing the wrong workspace_id matches zero
rows. This holds independently of Postgres RLS — important because the
service-role client (used by background jobs, ops scripts, and any future
elevated path) bypasses RLS entirely.

The 2024-2026 Supabase security consensus (Makerkit RLS best practices,
Precursor Security "Row-Level Recklessness" 2024, Supabase 2025 retro,
OWASP API1:2023 BOLA + Multi-Tenant Cheat Sheet) treats RLS as the security
floor, not the only defense. This test locks the floor in code so a future
repository method that omits the compound key gets caught.

Construction: each test mints two users A and B, seeds A's row via direct
asyncpg INSERT (RLS-bypassed fixture path), then invokes the repository
method with A's row id BUT B's workspace_id. Expected outcome: the method
reports no-op (False / None / empty data) AND the row is unchanged when
read back via asyncpg.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from website.core.supabase_v2.client import get_v2_client
from website.core.supabase_v2.repositories.content_repository import ContentRepository
from website.core.supabase_v2.repositories.rag_repository import RAGRepository

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Seed helpers (service-role asyncpg bypasses RLS for fixture setup)
# ---------------------------------------------------------------------------
async def _seed_kasten(pool: asyncpg.Pool, *, workspace_id: uuid.UUID, name: str) -> uuid.UUID:
    kid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.kastens (id, workspace_id, name) VALUES ($1, $2, $3)",
            kid, workspace_id, name,
        )
    return kid


async def _seed_chat_session(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID, profile_id: uuid.UUID, title: str
) -> uuid.UUID:
    sid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.chat_sessions (id, workspace_id, profile_id, title) "
            "VALUES ($1, $2, $3, $4)",
            sid, workspace_id, profile_id, title,
        )
    return sid


async def _seed_workspace_zettel(pool: asyncpg.Pool, *, workspace_id: uuid.UUID) -> uuid.UUID:
    cz = uuid.uuid4()
    wz = uuid.uuid4()
    norm_url = f"https://ratchet-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO content.canonical_zettels "
            "(id, normalized_url, content_hash, source_type, title, body_md, publication_date) "
            "VALUES ($1, $2, $3, 'web', $4, 'body', '2026-04-01'::date)",
            cz, norm_url, chash, "compound-key ratchet fixture",
        )
        await conn.execute(
            "INSERT INTO content.workspace_zettels "
            "(id, workspace_id, canonical_zettel_id, ai_summary, user_tags, user_note, pinned, added_via) "
            "VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')",
            wz, workspace_id, cz,
            '{"brief_summary": "x", "detailed_summary": "x"}',
            ["ratchet"],
        )
    return wz


def _run(coro):
    """Synchronous bridge for the function-scoped asyncpg_pool fixture."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# rag_repository compound-key ratchet
# ---------------------------------------------------------------------------
def test_update_kasten_cross_tenant_returns_none(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = _run(_seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0], name="A's kasten"))

    # Service-role client bypasses RLS — the only defense here is the
    # repository's compound-key .eq("workspace_id", ...) filter.
    repo = RAGRepository(client=get_v2_client())
    result = repo.update_kasten(kid, b.workspace_ids[0], name="hijacked by B")
    assert result is None, "update_kasten with wrong workspace_id MUST be a no-op"

    async def _read():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval("SELECT name FROM rag.kastens WHERE id = $1", kid)
    assert _run(_read()) == "A's kasten"


def test_delete_kasten_cross_tenant_returns_false(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = _run(_seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0], name="A's kasten"))

    repo = RAGRepository(client=get_v2_client())
    assert repo.delete_kasten(kid, b.workspace_ids[0]) is False

    async def _exists():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval("SELECT 1 FROM rag.kastens WHERE id = $1", kid)
    assert _run(_exists()) == 1, "kasten must still exist after cross-tenant delete attempt"


def test_touch_kasten_cross_tenant_returns_none(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    kid = _run(_seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0], name="A's kasten"))

    async def _read_last_used():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval("SELECT last_used_at FROM rag.kastens WHERE id = $1", kid)
    before = _run(_read_last_used())

    repo = RAGRepository(client=get_v2_client())
    assert repo.touch_kasten(kid, b.workspace_ids[0]) is None

    after = _run(_read_last_used())
    assert before == after, "touch_kasten must NOT update last_used_at across tenants"


def test_update_chat_session_cross_tenant_returns_none(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = _run(_seed_chat_session(
        asyncpg_pool,
        workspace_id=a.workspace_ids[0],
        profile_id=a.profile_id,
        title="A's session",
    ))

    repo = RAGRepository(client=get_v2_client())
    result = repo.update_chat_session(sid, b.workspace_ids[0], title="hijacked by B")
    assert result is None

    async def _read():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval("SELECT title FROM rag.chat_sessions WHERE id = $1", sid)
    assert _run(_read()) == "A's session"


def test_delete_chat_session_cross_tenant_returns_false(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = _run(_seed_chat_session(
        asyncpg_pool,
        workspace_id=a.workspace_ids[0],
        profile_id=a.profile_id,
        title="A's session",
    ))

    repo = RAGRepository(client=get_v2_client())
    assert repo.delete_chat_session(sid, b.workspace_ids[0]) is False

    async def _exists():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval("SELECT 1 FROM rag.chat_sessions WHERE id = $1", sid)
    assert _run(_exists()) == 1


# ---------------------------------------------------------------------------
# content_repository compound-key ratchet (canonical fix in commit c6b0af9)
# ---------------------------------------------------------------------------
def test_update_workspace_zettel_cross_tenant_returns_false(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    wz = _run(_seed_workspace_zettel(asyncpg_pool, workspace_id=a.workspace_ids[0]))

    repo = ContentRepository(client=get_v2_client())
    ok = repo.update_workspace_zettel(
        wz,
        workspace_id=b.workspace_ids[0],
        user_note="hijacked by B",
    )
    assert ok is False

    async def _read_note():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_note FROM content.workspace_zettels WHERE id = $1", wz
            )
    assert _run(_read_note()) is None


def test_soft_delete_workspace_zettel_cross_tenant_returns_false(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    wz = _run(_seed_workspace_zettel(asyncpg_pool, workspace_id=a.workspace_ids[0]))

    repo = ContentRepository(client=get_v2_client())
    assert repo.soft_delete_workspace_zettel(wz, workspace_id=b.workspace_ids[0]) is False

    async def _read_deleted_at():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT deleted_at FROM content.workspace_zettels WHERE id = $1", wz
            )
    assert _run(_read_deleted_at()) is None


# ---------------------------------------------------------------------------
# Counter-test: own-tenant mutation MUST succeed.
# Without this, all the above tests could pass trivially if the methods were
# accidentally hard-broken (e.g. wrong table name) — and we'd never notice.
# ---------------------------------------------------------------------------
def test_update_kasten_same_tenant_succeeds(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    kid = _run(_seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0], name="orig"))

    repo = RAGRepository(client=get_v2_client())
    result = repo.update_kasten(kid, a.workspace_ids[0], name="renamed by owner")
    assert result is not None
    assert result["name"] == "renamed by owner"


def test_update_workspace_zettel_same_tenant_succeeds(mint_user, asyncpg_pool):
    a = mint_user(workspace_count=1)
    wz = _run(_seed_workspace_zettel(asyncpg_pool, workspace_id=a.workspace_ids[0]))

    repo = ContentRepository(client=get_v2_client())
    ok = repo.update_workspace_zettel(
        wz,
        workspace_id=a.workspace_ids[0],
        user_note="owner-authored note",
    )
    assert ok is True

    async def _read_note():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_note FROM content.workspace_zettels WHERE id = $1", wz
            )
    assert _run(_read_note()) == "owner-authored note"
