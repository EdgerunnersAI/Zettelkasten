"""SE-05: BOLA / BFLA on the summarization engine API surface.

Two surfaces to defend:

  * ``POST /api/v2/summarize`` — uses ``get_optional_user``; the writer's
    ``user_id`` MUST come from the JWT ``sub`` claim, never from the body.
    A swap of JWTs MUST flip the writer's user_id.
  * ``POST /api/zettels/add`` — uses ``get_optional_user`` and threads the
    authenticated UUID into canonical persistence.

Plus the OWASP API1:2023 BOLA UUID-leak guard borrowed from
``test_pricing_bola.py``: even when persistence succeeds, error responses
between users MUST NOT echo the other user's auth_user_id.

This is unit-level — we don't run a real Gemini call (anti-pattern guard).
We monkey-patch the engine surface and inspect the args the writer would
have been called with.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def app_client(monkeypatch):
    """Build a fresh app with v2 schema bound."""
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-bola-tests")

    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None

    from website.app import create_app
    return TestClient(create_app())


def _no_uuid_leak(body_text: str, *uuids) -> None:
    for u in uuids:
        assert str(u) not in body_text, (
            f"BOLA leak: {u} echoed in response body. Body: {body_text[:400]!r}"
        )


def _mk_summary_result(url: str):
    """Minimal shape returned from ``summarize_url``."""
    from website.features.summarization_engine.core.models import (
        SourceType,
        SummaryMetadata,
    )
    metadata = SummaryMetadata(
        source_type=SourceType.WEB,
        url=url,
        extraction_confidence="high",
        confidence_reason="ok",
        total_tokens_used=10,
        total_latency_ms=20,
    )
    return SimpleNamespace(
        mini_title="T",
        brief_summary="B",
        detailed_summary=[],
        tags=["x"],
        metadata=metadata,
        model_dump=lambda mode="json": {
            "mini_title": "T",
            "brief_summary": "B",
            "detailed_summary": [],
            "tags": ["x"],
            "metadata": metadata.model_dump(mode="json"),
        },
    )


# --- v2 route: writer.user_id derives from JWT sub, not body --------------


def test_v2_summarize_user_id_derived_from_jwt(app_client, mint_user, monkeypatch):
    """Two minted users hit ``/api/v2/summarize`` with their own JWT each.
    The SupabaseWriter MUST receive each user's distinct profile_id."""
    user_a = mint_user(workspace_count=1)
    user_b = mint_user(workspace_count=1)

    seen_user_ids: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        return _mk_summary_result(url)

    class _RecordingWriter:
        def __init__(self, *_a, **_kw):
            pass

        async def write(self, result, *, user_id):
            seen_user_ids.append(str(user_id))
            return {"status": "skipped", "reason": "stubbed", "node_id": str(uuid.uuid4())}

    from website.features.summarization_engine.api import routes as v2_routes_mod
    monkeypatch.setattr(v2_routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(v2_routes_mod, "SupabaseWriter", _RecordingWriter)
    monkeypatch.setattr(
        v2_routes_mod,
        "_gemini_client",
        lambda: object(),
    )

    payload = {
        "url": "https://example.com/article",
        "write_to_supabase": True,
    }

    resp_a = app_client.post(
        "/api/v2/summarize",
        json=payload,
        headers={"Authorization": f"Bearer {user_a.jwt}"},
    )
    assert resp_a.status_code == 200, resp_a.text

    resp_b = app_client.post(
        "/api/v2/summarize",
        json=payload,
        headers={"Authorization": f"Bearer {user_b.jwt}"},
    )
    assert resp_b.status_code == 200, resp_b.text

    assert len(seen_user_ids) == 2
    assert seen_user_ids[0] == str(user_a.auth_user_id), (
        f"writer received wrong user_id for A: {seen_user_ids[0]} != {user_a.auth_user_id}"
    )
    assert seen_user_ids[1] == str(user_b.auth_user_id), (
        f"writer received wrong user_id for B: {seen_user_ids[1]} != {user_b.auth_user_id}"
    )
    assert seen_user_ids[0] != seen_user_ids[1], (
        "BOLA: both JWTs produced the same writer user_id"
    )


def test_v2_summarize_anonymous_uses_default_user(app_client, monkeypatch):
    """No JWT → user_id falls back to the default sentinel UUID, NOT to
    a previous caller's id (no per-process state leak)."""
    seen: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        seen.append(str(user_id))
        return _mk_summary_result(url)

    from website.features.summarization_engine.api import routes as v2_routes_mod
    monkeypatch.setattr(v2_routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(v2_routes_mod, "_gemini_client", lambda: object())

    payload = {"url": "https://example.com/anon", "write_to_supabase": False}
    resp = app_client.post("/api/v2/summarize", json=payload)
    assert resp.status_code == 200, resp.text

    assert seen == ["00000000-0000-0000-0000-000000000001"], (
        f"anonymous fallback wrong: {seen}"
    )


def test_v2_summarize_body_user_id_field_is_ignored(app_client, mint_user, monkeypatch):
    """Defence-in-depth: if a future schema gains a body ``user_id`` field
    or a stray attacker passes one, it MUST NOT override the JWT-derived
    user_id. Today the schema has no such field — POSTing one should be
    silently dropped (FastAPI extra='ignore' default)."""
    user_a = mint_user(workspace_count=1)
    attacker_target = uuid.uuid4()

    seen: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        seen.append(str(user_id))
        return _mk_summary_result(url)

    from website.features.summarization_engine.api import routes as v2_routes_mod
    monkeypatch.setattr(v2_routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(v2_routes_mod, "_gemini_client", lambda: object())

    payload = {
        "url": "https://example.com/x",
        "write_to_supabase": False,
        "user_id": str(attacker_target),  # attempted override
    }
    resp = app_client.post(
        "/api/v2/summarize",
        json=payload,
        headers={"Authorization": f"Bearer {user_a.jwt}"},
    )
    assert resp.status_code == 200, resp.text
    assert seen == [str(user_a.auth_user_id)], (
        f"BOLA: body user_id leaked into writer call: seen={seen}, "
        f"attacker_target={attacker_target}"
    )
    # Defence-in-depth: response body must not contain the attacker's UUID.
    _no_uuid_leak(resp.text, attacker_target)


def test_v2_summarize_invalid_jwt_falls_back_to_anon(app_client, monkeypatch):
    """A garbage Bearer token must NOT raise 500 OR leak user info — the
    optional-auth path treats it as anonymous, and the error body must not
    echo any decoded claims."""
    seen: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        seen.append(str(user_id))
        return _mk_summary_result(url)

    from website.features.summarization_engine.api import routes as v2_routes_mod
    monkeypatch.setattr(v2_routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(v2_routes_mod, "_gemini_client", lambda: object())

    payload = {"url": "https://example.com/jwt-test", "write_to_supabase": False}
    resp = app_client.post(
        "/api/v2/summarize",
        json=payload,
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    # The optional-auth flow either ignores the bad token (treating as anon)
    # or 401s. Either is acceptable — the invariant is "no 5xx".
    assert resp.status_code < 500, resp.text
    if resp.status_code == 200:
        # Anonymous fallback was taken — should match the sentinel UUID.
        assert seen == ["00000000-0000-0000-0000-000000000001"], seen


# --- writer surface: workspace_id never crosses tenant boundaries ----------


def test_writer_workspace_cache_is_per_user(monkeypatch):
    """The writer's ``_workspace_cache`` is keyed by user_id. Calls for A
    and B MUST resolve to distinct workspaces, never share a cache entry."""
    from website.features.summarization_engine.writers.supabase import (
        SupabaseWriter,
    )

    workspace_a = uuid.uuid4()
    workspace_b = uuid.uuid4()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    fake_core = MagicMock()
    fake_core.get_default_workspace_id.side_effect = lambda uid: (
        workspace_a if uid == user_a else workspace_b
    )

    writer = SupabaseWriter(repository=MagicMock(), core_repo=fake_core)

    assert writer._resolve_workspace_id(user_a) == workspace_a
    assert writer._resolve_workspace_id(user_b) == workspace_b
    # Cached now: subsequent reads must still return the right one.
    assert writer._resolve_workspace_id(user_a) == workspace_a
    assert writer._resolve_workspace_id(user_b) == workspace_b
    # And the cache must store BOTH, not collapse onto one key.
    assert writer._workspace_cache[user_a] == workspace_a
    assert writer._workspace_cache[user_b] == workspace_b
    assert workspace_a != workspace_b
