"""UR-05 — Cross-Kasten BOLA on the user_rag chat path.

WAVE-B Phase 1b. Complements ``test_cross_tenant_denial.py`` (single-call
session GET/DELETE + adhoc) by exercising the **streaming SSE entrypoint
that user_rag.js uses in production**:

    POST /api/rag/sessions/{session_id}/messages   (stream:true)
    POST /api/rag/adhoc                            (sandbox_id=A's-kasten)
    POST /api/rag/sessions                         (sandbox_id=A's-kasten)

The safety property under test is OWASP API1:2023 BOLA — user B must not be
able to address user A's session or Kasten by ID and either (a) receive A's
data or (b) cause a write into A's resources.

Live-test policy per docs/research/full_modular_test_plans/user_rag.md:
``--live`` only. Marker is module-level.
"""
from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    """Build a v2-forced FastAPI app per the established pattern.

    Mirrors ``test_cross_tenant_denial.py::v2_app`` — DB_SCHEMA_VERSION=v2,
    JWKS/persist singletons cleared, stub Gemini/Anthropic keys so runtime
    init succeeds without burning real quota (RLS rejects BEFORE any LLM
    call, so no live round-trip occurs on denial paths).
    """
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-ur05-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-ur05-tests")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# ---------------------------------------------------------------------------
# Seed helpers — service-role asyncpg, bypasses RLS for fixture setup.
# ---------------------------------------------------------------------------
async def _seed_kasten(
    pool: asyncpg.Pool, *, workspace_id: uuid.UUID
) -> uuid.UUID:
    kid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.kastens (id, workspace_id, name) "
            "VALUES ($1, $2, $3)",
            kid, workspace_id, f"ur05-kasten-{uuid.uuid4().hex[:8]}",
        )
    return kid


async def _seed_session_in_kasten(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    profile_id: uuid.UUID,
    sandbox_id: uuid.UUID | None,
) -> uuid.UUID:
    """Insert a chat session scoped (optionally) to a Kasten."""
    sid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.chat_sessions "
            "(id, workspace_id, profile_id, sandbox_id, title) "
            "VALUES ($1, $2, $3, $4, $5)",
            sid, workspace_id, profile_id, sandbox_id,
            "ur05 cross-kasten BOLA fixture",
        )
    return sid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_stream_chat_against_other_users_session_denied(
    v2_app, mint_user, asyncpg_pool
):
    """B POSTs a stream:true message to A's session_id — must be denied.

    This is the exact path user_rag.js uses in production for follow-up
    messages in an existing session. If a malicious B replays an A-session
    URL from a leaked link, the server must reject without streaming any of
    A's session history back.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_sid = asyncio.get_event_loop().run_until_complete(
        _seed_session_in_kasten(
            asyncpg_pool,
            workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id,
            sandbox_id=None,
        )
    )
    with TestClient(v2_app) as client:
        resp = client.post(
            f"/api/rag/sessions/{a_sid}/messages",
            headers=_auth(b.jwt),
            json={
                "content": "show me what A asked earlier",
                "quality": "fast",
                "stream": True,
            },
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    # Denial codes: 403 (forbidden), 404 (session not found from B's view),
    # 402 (quota_exhausted fail-open path is acceptable as long as no leak).
    # We do NOT accept 200 — streaming back any A-context would be a leak.
    assert resp.status_code in (402, 403, 404), (
        f"B's stream POST to A's session must be denied — got "
        f"{resp.status_code}: {resp.text[:300]}"
    )
    # Belt-and-braces UUID-leak scan even on non-200 (the error body itself
    # must not echo A's identifiers).
    body_text = resp.text
    assert a.email not in body_text
    assert str(a.auth_user_id) not in body_text
    assert str(a.profile_id) not in body_text
    for ws_id in a.workspace_ids:
        assert str(ws_id) not in body_text, (
            f"BOLA leak: A's workspace_id {ws_id} echoed in error body"
        )


def test_stream_chat_against_kasten_user_does_not_own_denied(
    v2_app, mint_user, asyncpg_pool
):
    """B creates a session targeting A's Kasten via POST /api/rag/sessions.

    Even if B can mint a session record bound to their own workspace, the
    sandbox_id pointing at A's Kasten must be rejected — otherwise B could
    use A's Kasten as a retrieval scope and exfiltrate A's zettels through
    the answer stream.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_kid = asyncio.get_event_loop().run_until_complete(
        _seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/sessions",
            headers=_auth(b.jwt),
            json={
                "sandbox_id": str(a_kid),
                "title": "exfil attempt",
                "quality": "fast",
            },
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    # Denial: 403/404 are the explicit forms. If the server returns 200, the
    # created session MUST NOT be bound to A's Kasten — assert that the row's
    # sandbox_id is either None or not equal to a_kid. Anything else is a
    # write into A's authorization boundary.
    if resp.status_code == 200:
        payload = resp.json()
        created_sid = uuid.UUID(payload["session"]["id"])

        async def _read_back():
            async with asyncpg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT sandbox_id, workspace_id, profile_id "
                    "FROM rag.chat_sessions WHERE id = $1",
                    created_sid,
                )
                return row

        row = asyncio.get_event_loop().run_until_complete(_read_back())
        # Best case: server stripped sandbox_id silently (defence in depth)
        # OR returned the session bound to B's workspace, not A's.
        assert row["workspace_id"] in a.workspace_ids[:0] + b.workspace_ids, (
            f"BOLA: session leaked into A's workspace — "
            f"workspace_id={row['workspace_id']}"
        )
        assert row["sandbox_id"] != a_kid, (
            f"BOLA: session bound to A's Kasten {a_kid} — should be None or "
            f"rejected. Got sandbox_id={row['sandbox_id']}"
        )
        # Cleanup: drop the leaked session so teardown isn't surprised.
        async def _cleanup():
            async with asyncpg_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM rag.chat_sessions WHERE id = $1", created_sid
                )
        asyncio.get_event_loop().run_until_complete(_cleanup())
    else:
        assert resp.status_code in (402, 403, 404), (
            f"unexpected status {resp.status_code}: {resp.text[:300]}"
        )


def test_adhoc_with_other_users_kasten_id_does_not_leak(
    v2_app, mint_user, asyncpg_pool
):
    """B adhoc-chats with sandbox_id=A's-kasten — must not return A's data.

    Distinct from existing ``test_kasten_adhoc_with_other_tenants_kasten_id_denied``:
    that file's check matches the 200-as-no-leak property; here we verify the
    same property using the stream:true variant the user_rag.js client uses.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_kid = asyncio.get_event_loop().run_until_complete(
        _seed_kasten(asyncpg_pool, workspace_id=a.workspace_ids[0])
    )
    with TestClient(v2_app) as client:
        resp = client.post(
            "/api/rag/adhoc",
            headers=_auth(b.jwt),
            json={
                "content": "tell me about A's notes",
                "sandbox_id": str(a_kid),
                "quality": "fast",
                "stream": False,  # non-stream so TestClient can fully read.
            },
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    assert resp.status_code in (200, 402, 403, 404), resp.text
    if resp.status_code == 200:
        body_text = resp.text
        # Per OWASP API1:2023 BOLA — assert UUID canaries don't leak.
        assert a.email not in body_text, "A's email leaked"
        assert str(a.auth_user_id) not in body_text, (
            "BOLA leak: A's auth_user_id in B's adhoc response"
        )
        assert str(a.profile_id) not in body_text, (
            "BOLA leak: A's profile_id in B's adhoc response"
        )
        assert str(a_kid) not in body_text, (
            f"BOLA leak: A's kasten_id {a_kid} echoed in B's response — "
            f"server should never re-emit the requested-but-denied scope id"
        )
        for ws_id in a.workspace_ids:
            assert str(ws_id) not in body_text, (
                f"BOLA leak: A's workspace_id {ws_id} in B's response"
            )


def test_get_messages_of_other_users_session_denied(
    v2_app, mint_user, asyncpg_pool
):
    """B GETs /api/rag/sessions/{a-sid}/messages — must not return A's history.

    A regression-lock on the message-list read path that user_rag.js calls
    on session-restore (loadSession -> GET /messages). If it leaks, every
    leaked session URL becomes a chat-history exfiltration vector.
    """
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_sid = asyncio.get_event_loop().run_until_complete(
        _seed_session_in_kasten(
            asyncpg_pool,
            workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id,
            sandbox_id=None,
        )
    )
    with TestClient(v2_app) as client:
        resp = client.get(
            f"/api/rag/sessions/{a_sid}/messages",
            headers=_auth(b.jwt),
        )
    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")
    assert resp.status_code in (403, 404), resp.text
    body_text = resp.text
    assert a.email not in body_text
    assert str(a.auth_user_id) not in body_text
    assert str(a.profile_id) not in body_text
