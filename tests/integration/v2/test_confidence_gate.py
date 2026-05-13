"""H2/C4 route-level integration — content confidence gate on /api/v2/summarize.

Two contracts:
  * raw_text_len < 500 AND tier_used == "metadata_only" -> HTTP 422.
  * raw_text_len >= 1500 AND non-metadata tier -> HTTP 200 with
    ``confidence: "high"`` in the response body.

The orchestrator is mocked at the routes-module symbol so we exercise only the
grading + serialization branch in the handler.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from website.features.summarization_engine.api.routes import router
from website.features.summarization_engine.core.models import (
    IngestResult,
    SourceType,
    SummaryMetadata,
)


def _summary_result():
    metadata = SummaryMetadata(
        source_type=SourceType.YOUTUBE,
        url="https://youtube.com/watch?v=abc",
        extraction_confidence="high",
        confidence_reason="ok",
        total_tokens_used=100,
        total_latency_ms=200,
    )
    return SimpleNamespace(
        metadata=metadata,
        model_dump=lambda mode="json": {"mini_title": "T", "brief_summary": "B."},
    )


def _ingest(raw_text: str, tier_used: str) -> IngestResult:
    return IngestResult(
        source_type=SourceType.YOUTUBE,
        url="https://youtube.com/watch?v=abc",
        original_url="https://youtube.com/watch?v=abc",
        raw_text=raw_text,
        sections={},
        metadata={"tier_used": tier_used},
        extraction_confidence="high",
        confidence_reason="ok",
        fetched_at=datetime.now(timezone.utc),
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@patch("website.features.summarization_engine.api.routes._gemini_client")
@patch("website.features.summarization_engine.api.routes.summarize_url_bundle")
def test_insufficient_context_returns_422(mock_bundle, mock_client):
    bundle = SimpleNamespace(
        ingest_result=_ingest("x" * 300, "metadata_only"),
        summary_result=_summary_result(),
    )
    mock_bundle.return_value = bundle  # AsyncMock-compatible
    mock_bundle.side_effect = None
    mock_bundle.__call__ = AsyncMock(return_value=bundle)
    # Patch as awaitable: side_effect with async function works most reliably.
    async def _ret(*a, **kw):
        return bundle
    mock_bundle.side_effect = _ret
    mock_client.return_value = object()

    resp = _client().post(
        "/api/v2/summarize", json={"url": "https://youtube.com/watch?v=abc"}
    )
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI nests our dict under "detail".
    detail = body.get("detail", body)
    assert detail["error"] == "insufficient_context"
    assert detail["confidence"] == "insufficient"
    assert detail["quality_signals"]["input_chars"] == 300
    assert detail["quality_signals"]["source_tier"] == "metadata_only"


@patch("website.features.summarization_engine.api.routes._gemini_client")
@patch("website.features.summarization_engine.api.routes.summarize_url_bundle")
def test_high_confidence_returns_200(mock_bundle, mock_client):
    bundle = SimpleNamespace(
        ingest_result=_ingest("x" * 2000, "transcript_api_direct"),
        summary_result=_summary_result(),
    )

    async def _ret(*a, **kw):
        return bundle
    mock_bundle.side_effect = _ret
    mock_client.return_value = object()

    resp = _client().post(
        "/api/v2/summarize", json={"url": "https://youtube.com/watch?v=abc"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["confidence"] == "high"
    assert body["confidence_reason"] is None
    assert body["quality_signals"]["input_chars"] == 2000
    assert body["quality_signals"]["source_tier"] == "transcript_api_direct"
