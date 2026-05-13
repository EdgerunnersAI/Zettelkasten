from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from website.features.summarization_engine.source_ingest.youtube.tiers import (
    TierName,
    TierResult,
    TranscriptChain,
    build_default_chain,
    tier_gemini_audio,
    tier_gemini_youtube_url,
    tier_invidious_pool,
    tier_metadata_only,
    tier_piped_pool,
    tier_transcript_api_via_webshare,
    tier_ytdlp_cookies_impersonate,
)


@pytest.mark.asyncio
async def test_chain_calls_tiers_in_order_until_success():
    t1 = AsyncMock(
        return_value=TierResult(
            tier=TierName.YTDLP_PLAYER_ROTATION,
            transcript="",
            success=False,
        )
    )
    t2 = AsyncMock(
        return_value=TierResult(
            tier=TierName.TRANSCRIPT_API_DIRECT,
            transcript="hello",
            success=True,
        )
    )
    t3 = AsyncMock(
        return_value=TierResult(
            tier=TierName.PIPED_POOL,
            transcript="x",
            success=True,
        )
    )

    chain = TranscriptChain(tiers=[t1, t2, t3], budget_ms=60000)
    result = await chain.run(video_id="x", config={})

    assert result.tier == TierName.TRANSCRIPT_API_DIRECT
    t1.assert_called_once()
    t2.assert_called_once()
    t3.assert_not_called()


@pytest.mark.asyncio
async def test_chain_stops_when_budget_exceeded():
    import asyncio

    async def slow_tier(video_id, config):
        await asyncio.sleep(0.3)
        return TierResult(
            tier=TierName.YTDLP_PLAYER_ROTATION,
            transcript="",
            success=False,
        )

    chain = TranscriptChain(tiers=[slow_tier, slow_tier, slow_tier], budget_ms=500)
    result = await chain.run(video_id="x", config={})

    assert not result.success


def _mk_pool(result_obj):
    pool = SimpleNamespace()
    pool.generate_content_youtube_url = AsyncMock(return_value=result_obj)
    return pool


@pytest.mark.asyncio
async def test_tier_gemini_youtube_url_success():
    fake = SimpleNamespace(
        text="a" * 500, model="gemini-2.5-flash", key_index=0
    )
    pool = _mk_pool(fake)
    with patch(
        "website.features.api_key_switching.get_key_pool",
        return_value=pool,
    ):
        result = await tier_gemini_youtube_url("vid123", {})
    assert result.success is True
    assert result.tier == TierName.GEMINI_FILEDATA
    assert result.extra["model"] == "gemini-2.5-flash"
    assert result.extra["key_index"] == 0
    assert len(result.transcript) == 500


@pytest.mark.asyncio
async def test_tier_gemini_youtube_url_invalid_argument_is_non_retryable():
    pool = SimpleNamespace()
    pool.generate_content_youtube_url = AsyncMock(
        side_effect=RuntimeError("400 INVALID_ARGUMENT: must be public")
    )
    with patch(
        "website.features.api_key_switching.get_key_pool",
        return_value=pool,
    ):
        result = await tier_gemini_youtube_url("vid_private", {})
    assert result.success is False
    assert result.extra.get("non_retryable") is True
    assert "non-retryable" in result.error


@pytest.mark.asyncio
async def test_tier_gemini_youtube_url_empty_text_is_failure():
    fake = SimpleNamespace(text="EMPTY", model="gemini-2.5-flash", key_index=1)
    pool = _mk_pool(fake)
    with patch(
        "website.features.api_key_switching.get_key_pool",
        return_value=pool,
    ):
        result = await tier_gemini_youtube_url("vid_silent", {})
    assert result.success is False
    assert "empty-or-too-short" in result.error


@pytest.mark.asyncio
async def test_tier_gemini_youtube_url_disabled_via_config():
    result = await tier_gemini_youtube_url(
        "vid", {"gemini_filedata": {"enabled": False}}
    )
    assert result.success is False
    assert "disabled" in result.error


def test_build_default_chain_places_gemini_filedata_first():
    chain = build_default_chain({})
    first_tier = chain._tiers[0]
    assert first_tier is tier_gemini_youtube_url


def test_build_default_chain_has_seven_tiers_in_h3_order():
    chain = build_default_chain({})
    assert chain._tiers == [
        tier_gemini_youtube_url,
        tier_transcript_api_via_webshare,
        tier_ytdlp_cookies_impersonate,
        tier_invidious_pool,
        tier_piped_pool,
        tier_gemini_audio,
        tier_metadata_only,
    ]


@pytest.mark.asyncio
async def test_tier_transcript_api_via_webshare_fails_when_proxy_unset(monkeypatch):
    monkeypatch.delenv("YT_TRANSCRIPT_PROXY_URL", raising=False)
    result = await tier_transcript_api_via_webshare("vid123", {})
    assert result.success is False
    assert "YT_TRANSCRIPT_PROXY_URL" in (result.error or "")
    assert result.tier == TierName.TRANSCRIPT_API_DIRECT


@pytest.mark.asyncio
async def test_tier_ytdlp_cookies_impersonate_fails_when_cookies_missing(monkeypatch):
    monkeypatch.setenv("YT_COOKIES_PATH", "/nonexistent/path/yt-cookies.txt")
    result = await tier_ytdlp_cookies_impersonate("vid123", {})
    assert result.success is False
    assert "YT_COOKIES_PATH" in (result.error or "")
    assert result.tier == TierName.YTDLP_PLAYER_ROTATION


@pytest.mark.asyncio
async def test_tier_ytdlp_cookies_impersonate_fails_when_cookies_env_unset(monkeypatch):
    monkeypatch.delenv("YT_COOKIES_PATH", raising=False)
    result = await tier_ytdlp_cookies_impersonate("vid123", {})
    assert result.success is False
    assert "YT_COOKIES_PATH" in (result.error or "")


def test_invidious_instances_refreshed_to_four():
    """H4/T4 — config carries exactly the 4 refreshed Invidious instances."""
    from website.features.summarization_engine.core.config import load_config

    cfg = load_config()
    youtube = cfg.sources.get("youtube", {})
    instances = youtube.get("invidious_instances") or []
    assert instances == [
        "inv.nadeko.net",
        "invidious.nerdvpn.de",
        "inv.thepixora.com",
        "yt.chocolatemoo53.com",
    ]
