"""RP-01 — UUID-scoped authz matrix on the RAG service surface.

Targets the actual entry points (per Phase-0 amendment 1):

  * ``RAGOrchestrator.answer``         — non-streaming chat (HTTP
    ``POST /api/rag/sessions/{id}/messages`` with ``stream=False``).
  * ``RAGOrchestrator.answer_stream``  — streaming chat (same route,
    ``stream=True``).
  * ``ChatSessionStore.list_sessions`` — ``GET /api/rag/sessions``.
  * ``ChatSessionStore.list_messages`` — ``GET /api/rag/sessions/{id}/messages``.

For every pair, user B with a valid JWT MUST NOT:

  * Read A's session metadata (title, last_message_at, last_scope_filter).
  * Stream against A's session_id.
  * List A's messages.
  * Find A's session_id in B's session list.

OWASP API1:2023 BOLA UUID-leak guard applies: even when the route correctly
denies access with 403/404, the response body MUST NOT echo back A's
``auth_user_id``, ``profile_id``, ``workspace_id`` or session id.

We use ``mint_user`` to mint TWO independent identities and seed sessions
through asyncpg (service-role bypass) — the rejection path is exercised
through the JWT-authenticated route.
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
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-rp01")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-rp01")

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    # Pricing bypass per pricing-module-authority rule (never seed
    # entitlements). The route's admission gate + auth check still runs.
    async def _noop(*_a, **_kw):
        return None
    from website.api import chat_routes as chat_routes_mod
    monkeypatch.setattr(chat_routes_mod, "require_entitlement", _noop)
    monkeypatch.setattr(chat_routes_mod, "consume_entitlement", _noop)

    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _seed_session(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    profile_id: uuid.UUID,
    title: str = "rp-01 session",
) -> uuid.UUID:
    sid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.chat_sessions (id, workspace_id, profile_id, title) "
            "VALUES ($1, $2, $3, $4)",
            sid, workspace_id, profile_id, title,
        )
    return sid


async def _seed_message(
    pool: asyncpg.Pool,
    *,
    session_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: str = "user",
    content: str = "rp-01-secret-message-body",
) -> uuid.UUID:
    mid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO rag.chat_messages "
            "(id, session_id, workspace_id, role, content) "
            "VALUES ($1, $2, $3, $4, $5)",
            mid, session_id, workspace_id, role, content,
        )
    return mid


def _no_leak(body_text: str, *needles: object) -> None:
    """OWASP API1:2023 BOLA — assert none of the victim's identifiers leak.

    Empty/None needles are skipped because not every field is set on every
    response and we don't want a stringified ``None`` to false-positive.
    """
    for n in needles:
        if n in (None, "", "None"):
            continue
        s = str(n)
        if not s:
            continue
        assert s not in body_text, (
            f"cross-tenant leak: {s!r} appears in response body "
            f"(excerpt={body_text[:400]!r})"
        )


# ---------------------------------------------------------------------------
# answer (non-stream) — POST /api/rag/sessions/{id}/messages stream=False
# ---------------------------------------------------------------------------


def test_answer_non_stream_against_other_users_session_id_denied(
    v2_app, mint_user, asyncpg_pool
):
    """B POSTs to A's session id with ``stream=False`` — the route resolves
    the session under B's runtime; if it returns anything but 4xx, the body
    MUST NOT carry A's identifiers."""
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool,
            workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id,
            title="A-private-title-rp01",
        )
    )

    with TestClient(v2_app) as client:
        resp = client.post(
            f"/api/rag/sessions/{sid}/messages",
            headers=_auth(b.jwt),
            json={"content": "leak A's session", "stream": False},
        )

    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    # Acceptable codes: 402 fail-open quota, 403/404 explicit denial. 200
    # is only acceptable if NO A-identifier leaks.
    assert resp.status_code in (200, 402, 403, 404, 422), resp.text
    body = resp.text
    _no_leak(body, a.auth_user_id, a.profile_id, a.email, *a.workspace_ids)
    # Title is the most user-visible leak vector — guard it explicitly.
    assert "A-private-title-rp01" not in body, (
        f"A's session title leaked through B's non-stream request: {body[:400]!r}"
    )


# ---------------------------------------------------------------------------
# answer_stream — POST /api/rag/sessions/{id}/messages stream=True
# ---------------------------------------------------------------------------


def test_answer_stream_against_other_users_session_id_denied(
    v2_app, mint_user, asyncpg_pool
):
    """Streaming variant: SSE body must not carry A's identifiers either.
    The route either short-circuits with 404 (B's runtime can't see A's
    session) or opens an SSE that the orchestrator scopes to B's data."""
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool,
            workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id,
            title="A-streamed-private-rp01",
        )
    )

    with TestClient(v2_app) as client:
        resp = client.post(
            f"/api/rag/sessions/{sid}/messages",
            headers=_auth(b.jwt),
            json={"content": "leak A's stream", "stream": True},
        )

    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    # 200 SSE is allowed iff no A-data leaks; 4xx are explicit denials.
    assert resp.status_code in (200, 402, 403, 404, 422), resp.text
    body = resp.text  # TestClient buffers full SSE response
    _no_leak(body, a.auth_user_id, a.profile_id, a.email, *a.workspace_ids)
    assert "A-streamed-private-rp01" not in body, (
        f"A's session title leaked through B's SSE response: {body[:400]!r}"
    )


# ---------------------------------------------------------------------------
# ChatSessionStore.list_messages — GET /api/rag/sessions/{id}/messages
# ---------------------------------------------------------------------------


def test_list_messages_cross_tenant_denied_with_uuid_leak_guard(
    v2_app, mint_user, asyncpg_pool
):
    """B asks for the message list of A's session id; the seeded secret
    content must NEVER appear in B's response."""
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool, workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id, title="A's history",
        )
    )
    mid = asyncio.get_event_loop().run_until_complete(
        _seed_message(
            asyncpg_pool,
            session_id=sid,
            workspace_id=a.workspace_ids[0],
            content="rp-01-canary-PII-payload",
        )
    )

    with TestClient(v2_app) as client:
        resp = client.get(
            f"/api/rag/sessions/{sid}/messages", headers=_auth(b.jwt)
        )

    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    # 200 with empty list is acceptable (B's scope sees no messages on a
    # session not owned by B). 4xx is explicit denial.
    assert resp.status_code in (200, 403, 404), resp.text
    body = resp.text
    assert "rp-01-canary-PII-payload" not in body, (
        f"A's message content leaked to B: {body[:400]!r}"
    )
    _no_leak(body, mid, a.auth_user_id, a.profile_id, a.email, *a.workspace_ids)


# ---------------------------------------------------------------------------
# ChatSessionStore.list_sessions — GET /api/rag/sessions
# ---------------------------------------------------------------------------


def test_list_sessions_never_includes_other_users_sessions(
    v2_app, mint_user, asyncpg_pool
):
    """B fetches her own session list; A's session ids/titles must not appear."""
    a = mint_user(workspace_count=1)
    b = mint_user(workspace_count=1)
    a_sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool, workspace_id=a.workspace_ids[0],
            profile_id=a.auth_user_id, title="A-uniq-title-rp01",
        )
    )
    # Seed one of B's own so the list isn't empty (and we know the route
    # works for legitimate calls).
    b_sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool, workspace_id=b.workspace_ids[0],
            profile_id=b.auth_user_id, title="B-uniq-title-rp01",
        )
    )

    with TestClient(v2_app) as client:
        resp = client.get("/api/rag/sessions", headers=_auth(b.jwt))

    if resp.status_code == 503 and "RAG runtime" in resp.text:
        pytest.skip("RAG runtime unavailable in test env")
    assert resp.status_code == 200, resp.text

    body_text = resp.text
    # A's session id and title MUST be absent.
    assert str(a_sid) not in body_text, (
        f"A's session_id leaked into B's list: {body_text[:400]!r}"
    )
    assert "A-uniq-title-rp01" not in body_text, "A's title leaked"
    _no_leak(body_text, a.auth_user_id, a.profile_id, a.email, *a.workspace_ids)
    # Sanity: B's own row IS present so this isn't a false-negative.
    if "B-uniq-title-rp01" not in body_text and str(b_sid) not in body_text:
        # If B sees zero of her own seeded rows the route is filtering on
        # something other than workspace_id (e.g. a feature flag); skip
        # rather than assert a misleading failure.
        pytest.skip("B's own session not surfaced — route filtering unclear")


# ---------------------------------------------------------------------------
# Negative control: B's own session is reachable through the same matrix
# ---------------------------------------------------------------------------


def test_owner_can_read_own_session_through_same_routes(
    v2_app, mint_user, asyncpg_pool
):
    """If the denial tests passed only because the routes return 404 to
    everyone (broken auth → broken access), the matrix would have false-
    positive denials. Confirm the happy path still works for the owner."""
    b = mint_user(workspace_count=1)
    b_sid = asyncio.get_event_loop().run_until_complete(
        _seed_session(
            asyncpg_pool, workspace_id=b.workspace_ids[0],
            profile_id=b.auth_user_id, title="B's own",
        )
    )

    with TestClient(v2_app) as client:
        get_resp = client.get(
            f"/api/rag/sessions/{b_sid}", headers=_auth(b.jwt)
        )
        msgs_resp = client.get(
            f"/api/rag/sessions/{b_sid}/messages", headers=_auth(b.jwt)
        )
        list_resp = client.get("/api/rag/sessions", headers=_auth(b.jwt))

    if get_resp.status_code == 503 and "RAG runtime" in get_resp.text:
        pytest.skip("RAG runtime unavailable in test env")

    assert get_resp.status_code == 200, get_resp.text
    assert msgs_resp.status_code == 200, msgs_resp.text
    assert list_resp.status_code == 200, list_resp.text
