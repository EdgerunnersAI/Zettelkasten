"""YouTube ingestor transcript-chain tests."""
from unittest.mock import AsyncMock, patch

import pytest

from website.features.summarization_engine.source_ingest.youtube.ingest import (
    YouTubeIngestor,
)
from website.features.summarization_engine.source_ingest.youtube.tiers import (
    TierName,
    TierResult,
    tier_piped_pool,
    _vtt_to_plaintext,
)


@pytest.mark.asyncio
async def test_ingestor_uses_successful_transcript_tier():
    ingestor = YouTubeIngestor()
    chain = AsyncMock()
    chain.run.return_value = TierResult(
        tier=TierName.TRANSCRIPT_API_DIRECT,
        transcript="A" * 220,
        success=True,
        confidence="high",
        latency_ms=321,
        extra={"title": "Transcript Title", "channel": "Transcript Channel"},
    )

    with patch(
        "website.features.summarization_engine.source_ingest.youtube.ingest.build_default_chain",
        return_value=chain,
    ):
        result = await ingestor.ingest(
            "https://www.youtube.com/watch?v=abc123",
            config={},
        )

    chain.run.assert_awaited_once_with(video_id="abc123", config={})
    assert result.metadata["tier_used"] == TierName.TRANSCRIPT_API_DIRECT.value
    assert result.metadata["tier_latency_ms"] == 321
    assert result.extraction_confidence == "high"
    assert "transcript via tier=transcript_api_direct" in result.confidence_reason
    assert "Transcript Title" in result.raw_text
    assert "Transcript Channel" in result.raw_text


@pytest.mark.asyncio
async def test_ingestor_marks_metadata_only_as_low_confidence():
    ingestor = YouTubeIngestor()
    chain = AsyncMock()
    chain.run.return_value = TierResult(
        tier=TierName.METADATA_ONLY,
        transcript="Fallback title\n\nFallback description",
        success=True,
        confidence="low",
        latency_ms=111,
        extra={"title": "Fallback title", "channel": "Fallback channel"},
    )

    with patch(
        "website.features.summarization_engine.source_ingest.youtube.ingest.build_default_chain",
        return_value=chain,
    ):
        result = await ingestor.ingest(
            "https://www.youtube.com/watch?v=abc123",
            config={},
        )

    assert result.extraction_confidence == "low"
    assert "metadata-only fallback" in result.confidence_reason
    assert result.metadata["tier_used"] == TierName.METADATA_ONLY.value


@pytest.mark.asyncio
async def test_ingestor_reports_last_error_when_all_tiers_fail():
    ingestor = YouTubeIngestor()
    chain = AsyncMock()
    chain.run.return_value = TierResult(
        tier=TierName.GEMINI_AUDIO,
        transcript="",
        success=False,
        error="upstream timeout",
        latency_ms=999,
    )

    with patch(
        "website.features.summarization_engine.source_ingest.youtube.ingest.build_default_chain",
        return_value=chain,
    ):
        result = await ingestor.ingest(
            "https://youtu.be/abc123",
            config={"transcript_budget_ms": 1000},
        )

    chain.run.assert_awaited_once_with(
        video_id="abc123",
        config={"transcript_budget_ms": 1000},
    )
    assert result.url == "https://www.youtube.com/watch?v=abc123"
    assert result.extraction_confidence == "low"
    assert result.confidence_reason == "All tiers failed; last error: upstream timeout"


def test_vtt_to_plaintext_preserves_grounding_timestamps():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:04.000
<c.colorE5E5E5>Opening line</c>

00:00:04.000 --> 00:00:08.000
<c.colorE5E5E5>Opening line</c>

00:00:09.250 --> 00:00:12.000
Next idea

01:05:00.000 --> 01:05:03.000
Later point
"""

    assert _vtt_to_plaintext(vtt) == (
        "[00:00] Opening line [00:09] Next idea [1:05:00] Later point"
    )


@pytest.mark.asyncio
async def test_piped_pool_dereferences_caption_url(httpx_mock):
    httpx_mock.add_response(
        url="https://piped.video/streams/abc123",
        json={
            "subtitles": [
                {
                    "code": "en",
                    "url": "https://piped.video/api/v1/captions/abc123/en.vtt",
                }
            ],
        },
    )
    httpx_mock.add_response(
        url="https://piped.video/api/v1/captions/abc123/en.vtt",
        text="""WEBVTT

00:00:00.000 --> 00:00:04.000
This caption is long enough to prove the URL was dereferenced and converted.

00:00:04.000 --> 00:00:08.000
More transcript text with enough content for the pool tier to accept it.
""",
    )

    result = await tier_piped_pool(
        "abc123",
        {"piped_instances": ["piped.video"], "instance_health_ttl_hours": 1},
    )

    assert result.success is True
    assert result.tier == TierName.PIPED_POOL
    assert "This caption is long enough" in result.transcript
    assert result.extra["caption_url"].endswith("/en.vtt")
