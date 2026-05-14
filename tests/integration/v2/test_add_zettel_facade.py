"""Add Zettel facade contract tests.

These tests pin the website-facing Add Zettel path to the summarization engine
entry point plus canonical persistence, without the deprecated summarize route
response shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

ZORO_AUTH_ID = UUID("a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e")


def _make_bundle(url: str):
    from website.features.summarization_engine.core.models import (
        DetailedSummarySection,
        IngestResult,
        SourceType,
        SummaryMetadata,
        SummaryResult,
    )

    metadata = SummaryMetadata(
        source_type=SourceType.WEB,
        url=url,
        extraction_confidence="high",
        confidence_reason="primary content extracted",
        total_tokens_used=42,
        total_latency_ms=123,
    )
    summary = SummaryResult(
        mini_title="Typed Facade",
        brief_summary="Brief facade summary.",
        detailed_summary=[
            DetailedSummarySection(heading="Why it matters", bullets=["One API path."])
        ],
        tags=["architecture/api"],
        metadata=metadata,
    )
    ingest = IngestResult(
        source_type=SourceType.WEB,
        url=url,
        original_url=url,
        raw_text="Enough extracted text for the summarizer to trust. " * 40,
        metadata={"tier_used": "primary"},
        extraction_confidence="high",
        confidence_reason="primary content extracted",
        fetched_at=datetime.now(timezone.utc),
    )
    return SimpleNamespace(summary_result=summary, ingest_result=ingest)


@pytest.fixture
def facade_client(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-add-zettel-tests")

    import website.api.zettels_routes as zettels_routes
    import website.api.module_runners.summarization as runner
    from website.api import auth as auth_mod
    from website.core import persist as persist_mod

    auth_mod._jwks_client = None
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    zettels_routes._IDEMPOTENCY_CACHE.clear()
    zettels_routes._IN_FLIGHT.clear()
    zettels_routes._OPERATIONS.clear()

    async def fake_require(*_args, **_kwargs):
        return None

    async def fake_consume(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runner, "require_entitlement", fake_require)
    monkeypatch.setattr(runner, "consume_entitlement", fake_consume)
    monkeypatch.setattr(zettels_routes, "_gemini_client", lambda: object())

    from website.app import create_app

    app = create_app()
    return TestClient(app), zettels_routes, runner


def test_add_zettel_contract_summarizes_then_persists(facade_client, monkeypatch):
    client, _zettels_routes, runner = facade_client
    calls: list[str] = []
    seen_user_ids: list[UUID] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        calls.append("summarize")
        seen_user_ids.append(user_id)
        return _make_bundle(url)

    async def fake_persist(result, *, user_sub=None, captured_on=None):
        calls.append("persist")
        assert user_sub == str(ZORO_AUTH_ID)
        return SimpleNamespace(
            result={**result, "captured_at": "2026-05-14"},
            file_node_id="web-typed-facade",
            supabase_node_id="00000000-0000-0000-0000-000000000222",
            file_saved=True,
            supabase_saved=True,
            supabase_duplicate=False,
            kg_user_id=str(ZORO_AUTH_ID),
        )

    monkeypatch.setattr(runner, "summarize_url_bundle", fake_summarize)
    monkeypatch.setattr(runner, "persist_summarized_result", fake_persist)

    resp = client.post(
        "/api/zettels/add",
        json={
            "url": "https://example.com/post",
            "client_action_id": "landing-1",
            "persist": True,
            "surface": "landing",
            "mode": "sync",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert calls == ["summarize", "persist"]
    assert seen_user_ids == [ZORO_AUTH_ID]
    assert body["status"] == "succeeded"
    assert body["operation_id"] == "landing-1"
    assert body["summary"]["title"] == "Typed Facade"
    assert body["summary"]["source_url"] == "https://example.com/post"
    assert body["persistence"] == {
        "requested": True,
        "persisted": True,
        "file_store": True,
        "supabase": True,
        "duplicate": False,
    }
    assert body["node_id"] == "web-typed-facade"
    assert body["workspace_zettel_id"] == "00000000-0000-0000-0000-000000000222"
    assert body["quality"]["confidence"] == "high"


def test_add_zettel_uses_authenticated_uuid_and_can_skip_persistence(
    facade_client, monkeypatch
):
    client, _zettels_routes, runner = facade_client
    user_id = uuid4()
    seen: list[UUID] = []
    persisted: list[bool] = []

    async def fake_user():
        return {"sub": str(user_id), "email": "person@example.test"}

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        seen.append(user_id)
        return _make_bundle(url)

    async def fake_persist(*_args, **_kwargs):
        persisted.append(True)
        raise AssertionError("persist should not run when persist=false")

    from website.api.auth import get_optional_user

    client.app.dependency_overrides[get_optional_user] = fake_user
    monkeypatch.setattr(runner, "summarize_url_bundle", fake_summarize)
    monkeypatch.setattr(runner, "persist_summarized_result", fake_persist)

    resp = client.post(
        "/api/zettels/add",
        json={
            "url": "https://example.com/no-write",
            "client_action_id": "home-1",
            "persist": False,
            "surface": "home",
            "mode": "sync",
        },
        headers={"Authorization": "Bearer test"},
    )

    assert resp.status_code == 200
    assert seen == [user_id]
    assert persisted == []
    body = resp.json()
    assert body["persistence"]["requested"] is False
    assert body["persistence"]["persisted"] is False
    assert body["node_id"] is None


def test_add_zettel_idempotency_reuses_original_response(facade_client, monkeypatch):
    client, _zettels_routes, runner = facade_client
    calls: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        calls.append("summarize")
        return _make_bundle(url)

    async def fake_persist(result, *, user_sub=None, captured_on=None):
        calls.append("persist")
        return SimpleNamespace(
            result=result,
            file_node_id="web-idempotent",
            supabase_node_id=None,
            file_saved=True,
            supabase_saved=False,
            supabase_duplicate=False,
            kg_user_id=user_sub,
        )

    monkeypatch.setattr(runner, "summarize_url_bundle", fake_summarize)
    monkeypatch.setattr(runner, "persist_summarized_result", fake_persist)

    payload = {
        "url": "https://example.com/idempotent",
        "client_action_id": "same-click",
        "persist": True,
        "surface": "zettels",
        "mode": "sync",
    }
    first = client.post("/api/zettels/add", json=payload)
    second = client.post("/api/zettels/add", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["summarize", "persist"]
    assert second.json() == first.json()


def test_add_zettel_idempotency_rejects_same_key_different_request(
    facade_client, monkeypatch
):
    client, _zettels_routes, runner = facade_client
    calls: list[str] = []

    async def fake_summarize(url, *, user_id, gemini_client, source_type=None):
        calls.append(url)
        return _make_bundle(url)

    async def fake_persist(result, *, user_sub=None, captured_on=None):
        return SimpleNamespace(
            result=result,
            file_node_id="web-idempotent",
            supabase_node_id=None,
            file_saved=True,
            supabase_saved=False,
            supabase_duplicate=False,
            kg_user_id=user_sub,
        )

    monkeypatch.setattr(runner, "summarize_url_bundle", fake_summarize)
    monkeypatch.setattr(runner, "persist_summarized_result", fake_persist)

    first = client.post(
        "/api/zettels/add",
        json={
            "url": "https://example.com/idempotent-a",
            "client_action_id": "same-click-different-request",
            "persist": True,
            "surface": "zettels",
            "mode": "sync",
        },
    )
    second = client.post(
        "/api/zettels/add",
        json={
            "url": "https://example.com/idempotent-b",
            "client_action_id": "same-click-different-request",
            "persist": True,
            "surface": "zettels",
            "mode": "sync",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.headers["content-type"].startswith("application/problem+json")
    assert second.json()["type"].endswith("/errors/idempotency-conflict")
    assert calls == ["https://example.com/idempotent-a"]


def test_add_zettel_idempotency_returns_accepted_for_running_duplicate(facade_client):
    client, zettels_routes, _runner = facade_client
    payload = {
        "url": "https://example.com/running",
        "client_action_id": "running-click",
        "persist": True,
        "surface": "home",
        "mode": "sync",
    }
    request_model = zettels_routes.AddZettelRequest.model_validate(payload)
    request_hash = zettels_routes._request_hash(request_model)
    zoro_key = (str(ZORO_AUTH_ID), "running-click")
    zettels_routes._IN_FLIGHT[zoro_key] = (request_hash, "running-click")

    resp = client.post("/api/zettels/add", json=payload)

    assert resp.status_code == 202
    assert resp.headers["location"] == "/api/operations/running-click"
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["operation_id"] == "running-click"


def test_add_zettel_problem_detail_failure_is_json(facade_client, monkeypatch):
    client, _zettels_routes, runner = facade_client

    async def fake_summarize(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "summarize_url_bundle", fake_summarize)

    resp = client.post(
        "/api/zettels/add",
        json={
            "url": "https://example.com/fail",
            "client_action_id": "fail-1",
            "persist": True,
            "surface": "landing",
            "mode": "sync",
        },
    )

    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"].endswith("/errors/add-zettel-failed")
    assert body["status"] == 500
    assert body["title"] == "Add Zettel failed"
    assert body["instance"] == "/api/zettels/add/fail-1"
