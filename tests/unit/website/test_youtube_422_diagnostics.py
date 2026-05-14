"""UX-4: /api/zettels/add 422 diagnostic payload for extraction failures."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from website.app import create_app
from website.features.summarization_engine.core.errors import ExtractionConfidenceError


@pytest.fixture
def client() -> TestClient:
    from website.api import zettels_routes

    zettels_routes._RATE_STORE.clear()
    zettels_routes._IDEMPOTENCY_CACHE.clear()
    return TestClient(create_app())


_YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _yt_tier_results() -> list[dict]:
    return [
        {
            "tier": "ytdlp_player_rotation",
            "status": "failed",
            "reason": "all player clients failed",
            "latency_ms": 1200,
        },
        {"tier": "transcript_api_direct", "status": "failed", "reason": "no captions", "latency_ms": 300},
        {"tier": "piped_pool", "status": "failed", "reason": "all instances unhealthy", "latency_ms": 50},
        {"tier": "invidious_pool", "status": "failed", "reason": "all instances unhealthy", "latency_ms": 50},
        {"tier": "gemini_audio", "status": "failed", "reason": "yt-dlp blocked", "latency_ms": 800},
        {"tier": "metadata_only", "status": "failed", "reason": "oembed 429", "latency_ms": 200},
    ]


class TestYouTube422Diagnostics:
    def test_youtube_extraction_failure_returns_problem_detail(
        self, client: TestClient, monkeypatch
    ) -> None:
        from website.api import zettels_routes
        from website.api.module_runners import summarization as runner

        exc = ExtractionConfidenceError(
            "Insufficient content extracted (12 chars). Reason: All tiers failed",
            source_type="youtube",
            reason="All tiers failed",
            tier_results=_yt_tier_results(),
            url=_YT_URL,
        )
        monkeypatch.setattr(runner, "require_entitlement", AsyncMock())
        monkeypatch.setattr(runner, "resolve_redirects", AsyncMock(return_value=_YT_URL))
        monkeypatch.setattr(runner, "normalize_url", lambda url: url)
        monkeypatch.setattr(zettels_routes, "_gemini_client", lambda: object())
        monkeypatch.setattr(runner, "summarize_url_bundle", AsyncMock(side_effect=exc))

        resp = client.post(
            "/api/zettels/add",
            json={
                "url": _YT_URL,
                "client_action_id": "yt-422",
                "persist": True,
                "surface": "landing",
                "mode": "sync",
            },
        )

        assert resp.status_code == 422
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["title"] == "Insufficient content"
        assert body["reason"] == "All tiers failed"
        assert isinstance(body["tier_results"], list)
        tier_names = [t["tier"] for t in body["tier_results"]]
        assert "ytdlp_player_rotation" in tier_names
        assert "metadata_only" in tier_names

    def test_successful_youtube_add_zettel_returns_200(
        self, client: TestClient, monkeypatch
    ) -> None:
        from website.api import zettels_routes
        from website.api.module_runners import summarization as runner
        from website.features.summarization_engine.core.models import (
            IngestResult,
            SourceType,
            SummaryMetadata,
            SummaryResult,
        )

        metadata = SummaryMetadata(
            source_type=SourceType.YOUTUBE,
            url=_YT_URL,
            extraction_confidence="high",
            confidence_reason="ok",
            total_tokens_used=50,
            total_latency_ms=250,
        )
        bundle = SimpleNamespace(
            summary_result=SummaryResult(
                mini_title="YT Title",
                brief_summary="Brief",
                detailed_summary=[],
                tags=["source/youtube"],
                metadata=metadata,
            ),
            ingest_result=IngestResult(
                source_type=SourceType.YOUTUBE,
                url=_YT_URL,
                original_url=_YT_URL,
                raw_text="youtube content " * 20,
                metadata={"tier_used": "primary"},
                extraction_confidence="high",
                confidence_reason="ok",
                fetched_at=datetime.now(timezone.utc),
            ),
        )
        monkeypatch.setattr(runner, "require_entitlement", AsyncMock())
        monkeypatch.setattr(runner, "consume_entitlement", AsyncMock())
        monkeypatch.setattr(runner, "resolve_redirects", AsyncMock(return_value=_YT_URL))
        monkeypatch.setattr(runner, "normalize_url", lambda url: url)
        monkeypatch.setattr(zettels_routes, "_gemini_client", lambda: object())
        monkeypatch.setattr(runner, "summarize_url_bundle", AsyncMock(return_value=bundle))
        monkeypatch.setattr(
            runner,
            "persist_summarized_result",
            AsyncMock(
                return_value=SimpleNamespace(
                    result={},
                    file_node_id="yt-title",
                    supabase_node_id=None,
                    file_saved=True,
                    supabase_saved=False,
                    supabase_duplicate=False,
                )
            ),
        )

        resp = client.post(
            "/api/zettels/add",
            json={
                "url": _YT_URL,
                "client_action_id": "yt-ok",
                "persist": True,
                "surface": "landing",
                "mode": "sync",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["summary"]["title"] == "YT Title"
