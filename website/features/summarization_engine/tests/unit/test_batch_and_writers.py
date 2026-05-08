"""Tests for batch input and markdown writer helpers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from website.features.summarization_engine.api.models import BatchV2Request
from website.features.summarization_engine.batch.input_loader import load_batch_input
from website.features.summarization_engine.batch.processor import BatchProcessor
from website.features.summarization_engine.core.models import (
    DetailedSummarySection,
    SourceType,
    SummaryMetadata,
    SummaryResult,
)
from website.features.summarization_engine.writers.markdown import render_markdown


def test_load_batch_input_csv():
    items = load_batch_input(input_bytes=b"url,tags,note\nhttps://example.com,\"a,b\",hello\n", filename="x.csv")
    assert items[0].url == "https://example.com"
    assert items[0].user_tags == ["a", "b"]
    assert items[0].user_note == "hello"


def test_load_batch_input_json():
    items = load_batch_input(input_bytes=b'{"urls":[{"url":"https://x.test","tags":["x"]}]}', filename="x.json")
    assert items[0].url == "https://x.test"
    assert items[0].user_tags == ["x"]


def test_load_batch_input_rejects_oversized_payload():
    with pytest.raises(ValueError, match="too large"):
        load_batch_input(input_bytes=b"x" * 1025, filename="x.csv", max_size_mb=0)


def test_batch_request_rejects_too_many_urls():
    with pytest.raises(ValidationError):
        BatchV2Request(urls=["https://example.com"] * 501)


def test_batch_request_rejects_invalid_urls():
    with pytest.raises(ValidationError):
        BatchV2Request(urls=["not-a-url"])


@pytest.mark.asyncio
async def test_batch_processor_stress_uses_bounded_workers(monkeypatch):
    active = 0
    max_active = 0

    async def fake_summarize(url, *, user_id, gemini_client):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return _summary_result(url=url)

    monkeypatch.setattr(
        "website.features.summarization_engine.batch.processor.summarize_url",
        fake_summarize,
    )
    payload = "url\n" + "\n".join(f"https://example.com/{index}" for index in range(200))
    result = await BatchProcessor(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        gemini_client=object(),
    ).run(input_bytes=payload.encode(), filename="stress.csv")

    assert result["run"]["success_count"] == 200
    assert max_active <= 3


def test_render_markdown_contains_frontmatter():
    rendered = render_markdown(_summary_result())
    assert "source_type: web" in rendered
    assert "# Test note" in rendered


def _summary_result(url: str = "https://example.com") -> SummaryResult:
    return SummaryResult(
        mini_title="Test note",
        brief_summary="A useful summary.",
        tags=["one", "two", "three", "four", "five", "six", "seven", "eight"],
        detailed_summary=[DetailedSummarySection(heading="Main", bullets=["Point"])],
        metadata=SummaryMetadata(
            source_type=SourceType.WEB,
            url=url,
            extraction_confidence="high",
            confidence_reason="ok",
            total_tokens_used=0,
            total_latency_ms=0,
            date=datetime.now(timezone.utc),
        ),
    )
