"""YouTube transcript fallback chain scaffold."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_HEALTH_CACHE_PATH = (
    Path(__file__).resolve().parents[5]
    / "docs"
    / "summary_eval"
    / "_cache"
    / "youtube_instance_health.json"
)


class TierName(str, Enum):
    YTDLP_PLAYER_ROTATION = "ytdlp_player_rotation"
    GEMINI_FILEDATA = "gemini_filedata"
    TRANSCRIPT_API_DIRECT = "transcript_api_direct"
    PIPED_POOL = "piped_pool"
    INVIDIOUS_POOL = "invidious_pool"
    GEMINI_AUDIO = "gemini_audio"
    METADATA_ONLY = "metadata_only"


@dataclass
class TierResult:
    tier: TierName
    transcript: str
    success: bool
    confidence: str = "low"
    latency_ms: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


TierFn = Callable[[str, dict], Awaitable[TierResult]]


class TranscriptChain:
    def __init__(self, tiers: list[TierFn], budget_ms: int = 90000) -> None:
        self._tiers = tiers
        self._budget_ms = budget_ms

    async def run(self, *, video_id: str, config: dict) -> TierResult:
        start = time.monotonic()
        last_result: TierResult | None = None
        attempts: list[dict[str, Any]] = []

        def _record(result: TierResult) -> None:
            attempts.append(
                {
                    "tier": result.tier.value,
                    "status": "success" if result.success else "failed",
                    "reason": (result.error or "")[:200] if not result.success else "",
                    "latency_ms": result.latency_ms,
                }
            )

        for tier in self._tiers:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms >= self._budget_ms:
                attempts.append(
                    {
                        "tier": "budget_exhausted",
                        "status": "skipped",
                        "reason": f"budget {self._budget_ms}ms exceeded",
                        "latency_ms": elapsed_ms,
                    }
                )
                break
            last_result = await tier(video_id, config)
            _record(last_result)
            if last_result.success:
                last_result.extra.setdefault("all_tier_results", attempts)
                return last_result

        final = last_result or TierResult(
            tier=TierName.METADATA_ONLY,
            transcript="",
            success=False,
        )
        final.extra.setdefault("all_tier_results", attempts)
        return final


async def tier_ytdlp_cookies_impersonate(video_id: str, config: dict) -> TierResult:
    """Tier 3: yt-dlp with --cookies-from-burner-account + --impersonate chrome
    (curl_cffi) + PO-token from bgutil sidecar. Unlocks age-restricted +
    members-only + bot-gate. Operator must configure YT_COOKIES_PATH,
    YT_USER_AGENT, YTDLP_POT_PROVIDER_URL. See docs/runbooks/yt-fallback-stack.md.
    Reuses TierName.YTDLP_PLAYER_ROTATION to avoid enum churn."""
    from yt_dlp import YoutubeDL

    start = time.monotonic()
    cookies_path = os.environ.get("YT_COOKIES_PATH", "")
    user_agent = os.environ.get("YT_USER_AGENT", "")
    pot_provider_url = os.environ.get("YTDLP_POT_PROVIDER_URL", "")

    if not cookies_path or not os.path.exists(cookies_path):
        return TierResult(
            tier=TierName.YTDLP_PLAYER_ROTATION,
            transcript="",
            success=False,
            latency_ms=0,
            error="YT_COOKIES_PATH not set or missing — see docs/runbooks/yt-fallback-stack.md",
        )

    clients = config.get(
        "ytdlp_player_clients",
        ["tv_simply", "android_sdkless", "ios", "web_safari", "web"],
    )
    url = f"https://www.youtube.com/watch?v={video_id}"

    for client in clients:
        with tempfile.TemporaryDirectory() as tmp:
            opts = {
                "quiet": True,
                "skip_download": True,
                "no_warnings": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": config.get("transcript_languages", ["en"]),
                "subtitlesformat": "vtt",
                "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
                "cookiefile": cookies_path,
                "extractor_args": {
                    "youtube": {
                        "player_client": [client],
                        **(
                            {"pot_provider_url": [pot_provider_url]}
                            if pot_provider_url
                            else {}
                        ),
                    }
                },
                # curl_cffi impersonation via yt-dlp's impersonate option
                "impersonate": "chrome",
            }
            if user_agent:
                opts["user_agent"] = user_agent
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True) or {}
                vtts = list(Path(tmp).glob("*.vtt"))
                if vtts:
                    transcript = _vtt_to_plaintext(
                        vtts[0].read_text(encoding="utf-8")
                    )
                    if len(transcript) > 100:
                        latency = int((time.monotonic() - start) * 1000)
                        logger.info(
                            "[yt-tier3] client=%s success len=%d",
                            client,
                            len(transcript),
                        )
                        return TierResult(
                            tier=TierName.YTDLP_PLAYER_ROTATION,
                            transcript=transcript,
                            success=True,
                            confidence="high",
                            latency_ms=latency,
                            extra={
                                "player_client": client,
                                "impersonate": "chrome",
                                "pot_provider_url_configured": bool(pot_provider_url),
                                "title": info.get("title", ""),
                            },
                        )
            except Exception as exc:
                logger.warning(
                    "[yt-tier3] client=%s failed: %s", client, str(exc)[:200]
                )
                continue

    return TierResult(
        tier=TierName.YTDLP_PLAYER_ROTATION,
        transcript="",
        success=False,
        latency_ms=int((time.monotonic() - start) * 1000),
        error="all player clients failed even with cookies+impersonate+PO-token",
    )


def _vtt_to_plaintext(vtt: str) -> str:
    """Convert WEBVTT into de-duplicated text with coarse grounding timestamps."""

    cue_entries: list[str] = []
    current_timestamp: str | None = None
    current_lines: list[str] = []
    skip_block = False
    last_text: str | None = None

    def flush_current() -> None:
        nonlocal current_lines, current_timestamp, last_text
        if not current_lines:
            current_timestamp = None
            return
        text = " ".join(current_lines).strip()
        current_lines = []
        if not text or text == last_text:
            current_timestamp = None
            return
        last_text = text
        if current_timestamp:
            cue_entries.append(f"[{current_timestamp}] {text}")
        else:
            cue_entries.append(text)
        current_timestamp = None

    for raw in vtt.splitlines():
        line = raw.strip()

        if skip_block:
            if not line:
                skip_block = False
            continue

        if not line:
            flush_current()
            continue
        if line == "WEBVTT":
            continue
        if line.startswith(("NOTE", "STYLE")):
            flush_current()
            skip_block = True
            continue
        if re.match(r"^\d+$", line) and not current_lines and current_timestamp is None:
            continue

        timing_match = re.match(
            r"(?P<start>\d{1,2}:\d{2}:\d{2}\.\d{3})\s*-->",
            line,
        )
        if timing_match:
            flush_current()
            current_timestamp = _format_vtt_timestamp(timing_match.group("start"))
            continue

        cleaned = re.sub(r"<[^>]+>", "", line).strip()
        if cleaned:
            current_lines.append(cleaned)

    flush_current()
    return " ".join(cue_entries)


def _format_vtt_timestamp(timestamp: str) -> str:
    hours, minutes, remainder = timestamp.split(":")
    seconds = remainder.split(".", 1)[0]
    if hours == "00":
        return f"{minutes}:{seconds}"
    return f"{int(hours)}:{minutes}:{seconds}"


async def tier_transcript_api_via_webshare(video_id: str, config: dict) -> TierResult:
    """Tier 2: youtube-transcript-api routed through Webshare free-tier residential
    proxies. Handles cases where Gemini's US-edge fetcher can't reach the video
    (region-lock visible from US but accessible from rotating residential IPs).
    DO NOT call this tier from a datacenter IP without a proxy — it will fail
    with `IpBlocked`. Reuses TierName.TRANSCRIPT_API_DIRECT to avoid enum churn."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import WebshareProxyConfig

    start = time.monotonic()
    cfg = config.get("transcript_api", {}) if isinstance(config, dict) else {}
    proxy_url = os.environ.get("YT_TRANSCRIPT_PROXY_URL", "")
    languages = cfg.get(
        "languages", config.get("transcript_languages", ["en", "en-US", "en-GB"])
    )

    if not proxy_url:
        return TierResult(
            tier=TierName.TRANSCRIPT_API_DIRECT,
            transcript="",
            success=False,
            latency_ms=0,
            error="YT_TRANSCRIPT_PROXY_URL not configured — see docs/runbooks/yt-fallback-stack.md",
        )

    try:
        # WebshareProxyConfig expects username + password — operator sets them
        # via YT_TRANSCRIPT_PROXY_USER / YT_TRANSCRIPT_PROXY_PASS or via combined
        # YT_TRANSCRIPT_PROXY_URL=http://user:pass@proxy.webshare.io:port format.
        user = os.environ.get("YT_TRANSCRIPT_PROXY_USER", "")
        password = os.environ.get("YT_TRANSCRIPT_PROXY_PASS", "")
        api = YouTubeTranscriptApi(
            proxy_config=(
                WebshareProxyConfig(proxy_username=user, proxy_password=password)
                if user and password
                else None
            ),
        )
        result = await asyncio.to_thread(api.fetch, video_id, languages=languages)
        text = "\n".join(
            snippet.text for snippet in result.snippets if snippet.text
        ).strip()
        if len(text) < 100:
            return TierResult(
                tier=TierName.TRANSCRIPT_API_DIRECT,
                transcript="",
                success=False,
                error=f"too-short: len={len(text)}",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        return TierResult(
            tier=TierName.TRANSCRIPT_API_DIRECT,
            transcript=text,
            success=True,
            confidence="high",
            latency_ms=int((time.monotonic() - start) * 1000),
            extra={"path": "transcript_api_webshare", "language": result.language},
        )
    except Exception as exc:
        return TierResult(
            tier=TierName.TRANSCRIPT_API_DIRECT,
            transcript="",
            success=False,
            error=str(exc)[:200],
            latency_ms=int((time.monotonic() - start) * 1000),
        )


def _load_health() -> dict[str, str]:
    if not _HEALTH_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_health(health: dict[str, str]) -> None:
    try:
        _HEALTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HEALTH_CACHE_PATH.write_text(json.dumps(health, indent=2), encoding="utf-8")
    except OSError:
        return


def _is_healthy(instance: str, ttl_hours: int) -> bool:
    health = _load_health()
    last_bad = health.get(instance)
    if not last_bad:
        return True
    try:
        when = datetime.fromisoformat(last_bad)
        return datetime.now(timezone.utc) - when > timedelta(hours=ttl_hours)
    except Exception:
        return True


def _mark_unhealthy(instance: str) -> None:
    health = _load_health()
    health[instance] = datetime.now(timezone.utc).isoformat()
    _save_health(health)


async def _try_pool(
    video_id: str,
    instances: list[str],
    pattern: str,
    ttl_hours: int,
    tier_name: TierName,
) -> TierResult:
    start = time.monotonic()
    for instance in instances:
        if not _is_healthy(instance, ttl_hours):
            continue
        url = pattern.format(instance=instance, vid=video_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    _mark_unhealthy(instance)
                    continue
                data = resp.json()
                caption_url = _extract_caption_url_from_pool_response(data)
                transcript = ""
                if caption_url:
                    caption_resp = await client.get(urljoin(str(resp.url), caption_url))
                    if caption_resp.status_code == 200:
                        transcript = _caption_text_to_plaintext(caption_resp.text)
                if transcript and len(transcript) > 100:
                    return TierResult(
                        tier=tier_name,
                        transcript=transcript,
                        success=True,
                        confidence="high",
                        latency_ms=int((time.monotonic() - start) * 1000),
                        extra={"instance": instance, "caption_url": caption_url},
                    )
        except Exception as exc:
            logger.warning("[%s] instance=%s exc=%s", tier_name.value, instance, exc)
            _mark_unhealthy(instance)
            continue
    return TierResult(
        tier=tier_name,
        transcript="",
        success=False,
        latency_ms=int((time.monotonic() - start) * 1000),
    )


def _extract_caption_url_from_pool_response(data: dict) -> str:
    """Return the first English captions URL advertised by the pool response."""
    subtitles = data.get("subtitles") or data.get("captions") or []
    for subtitle in subtitles:
        code = (
            subtitle.get("code")
            or subtitle.get("languageCode")
            or subtitle.get("label", "")
        ).lower()
        if "en" in code:
            return subtitle.get("url", "") or ""
    return ""


def _caption_text_to_plaintext(text: str) -> str:
    stripped = (text or "").lstrip()
    if stripped.startswith("WEBVTT"):
        return _vtt_to_plaintext(text)
    try:
        parsed = json.loads(text)
    except Exception:
        return text.strip()
    if isinstance(parsed, list):
        parts = []
        for item in parsed:
            if isinstance(item, dict):
                value = item.get("text") or item.get("transcript") or item.get("body")
                if value:
                    parts.append(str(value))
        return " ".join(parts).strip()
    if isinstance(parsed, dict):
        value = parsed.get("text") or parsed.get("transcript")
        if value:
            return str(value).strip()
    return text.strip()


async def tier_piped_pool(video_id: str, config: dict) -> TierResult:
    instances = [f"https://{instance}" for instance in config.get("piped_instances", [])]
    ttl = config.get("instance_health_ttl_hours", 1)
    return await _try_pool(
        video_id,
        instances,
        "{instance}/streams/{vid}",
        ttl,
        TierName.PIPED_POOL,
    )


async def tier_invidious_pool(video_id: str, config: dict) -> TierResult:
    instances = [
        f"https://{instance}" for instance in config.get("invidious_instances", [])
    ]
    ttl = config.get("instance_health_ttl_hours", 1)
    return await _try_pool(
        video_id,
        instances,
        "{instance}/api/v1/captions/{vid}",
        ttl,
        TierName.INVIDIOUS_POOL,
    )


async def tier_gemini_audio(video_id: str, config: dict) -> TierResult:
    """Tier 5: download audio locally, then upload bytes to Gemini File API."""
    if not config.get("enable_gemini_audio_fallback", True):
        return TierResult(
            tier=TierName.GEMINI_AUDIO,
            transcript="",
            success=False,
            error="disabled",
        )

    start = time.monotonic()
    max_size_mb = config.get("gemini_audio_max_filesize_mb", 50)
    max_duration_min = config.get("gemini_audio_max_duration_min", 60)
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        from yt_dlp import YoutubeDL

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / f"{video_id}.m4a"
            opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio[ext=m4a]/bestaudio",
                "outtmpl": str(out_path),
                "max_filesize": max_size_mb * 1024 * 1024,
                "match_filter": (
                    lambda info: None
                    if (info.get("duration") or 0) <= max_duration_min * 60
                    else "video too long"
                ),
            }
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

            if not out_path.exists():
                return TierResult(
                    tier=TierName.GEMINI_AUDIO,
                    transcript="",
                    success=False,
                    error="yt-dlp audio download did not produce file",
                    latency_ms=int((time.monotonic() - start) * 1000),
                )

            import google.generativeai as genai

            api_key = _first_available_key()
            if not api_key:
                return TierResult(
                    tier=TierName.GEMINI_AUDIO,
                    transcript="",
                    success=False,
                    error="no gemini key available",
                )

            genai.configure(api_key=api_key)
            uploaded = genai.upload_file(path=str(out_path), mime_type="audio/mp4")
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content(
                [
                    uploaded,
                    (
                        "Transcribe this audio into plain text with rough timestamps "
                        "every ~60 seconds. Return only the transcription, no preamble."
                    ),
                ]
            )
            text = (resp.text or "").strip()
            if len(text) > 200:
                return TierResult(
                    tier=TierName.GEMINI_AUDIO,
                    transcript=text,
                    success=True,
                    confidence="high",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    extra={"audio_bytes_uploaded": out_path.stat().st_size},
                )
    except Exception as exc:
        return TierResult(
            tier=TierName.GEMINI_AUDIO,
            transcript="",
            success=False,
            error=str(exc),
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    return TierResult(
        tier=TierName.GEMINI_AUDIO,
        transcript="",
        success=False,
        latency_ms=int((time.monotonic() - start) * 1000),
    )


def _first_available_key() -> str | None:
    import os
    from website.features.api_key_switching.key_pool import (
        _load_keys_from_file,
        candidate_api_env_paths,
    )

    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        if os.environ.get(name):
            return os.environ[name]
    if os.environ.get("GEMINI_API_KEYS"):
        for key in os.environ["GEMINI_API_KEYS"].split(","):
            stripped = key.strip()
            if stripped:
                return stripped
    for path in candidate_api_env_paths():
        loaded = _load_keys_from_file(str(path))
        if not loaded:
            continue
        first = loaded[0]
        return first[0] if isinstance(first, tuple) else first
    return None


async def tier_gemini_youtube_url(video_id: str, config: dict) -> TierResult:
    """Tier 1: Gemini 2.5 fileData(YouTube URL). Google fetches video
    server-side, bypassing client-IP bot detection. Public videos only.

    Non-retryable signals (INVALID_ARGUMENT / FAILED_PRECONDITION / NOT_FOUND /
    PERMISSION_DENIED / empty parts) fall through immediately. RESOURCE_EXHAUSTED
    and transient 5xx are retried by the key-pool layer (key rotation + backoff).
    Counts as 1 LLM call against the 3-call summarization budget.
    """
    start = time.monotonic()
    cfg_gem = (
        config.get("gemini_filedata", {}) if isinstance(config, dict) else {}
    )
    if not cfg_gem.get("enabled", True):
        return TierResult(
            tier=TierName.GEMINI_FILEDATA,
            transcript="",
            success=False,
            latency_ms=0,
            error="gemini-filedata-disabled-via-config",
        )

    try:
        from website.features.api_key_switching import get_key_pool
        from website.features.summarization_engine.core.budget import get_budget
    except Exception as exc:  # noqa: BLE001
        return TierResult(
            tier=TierName.GEMINI_FILEDATA,
            transcript="",
            success=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"import-failure: {exc}",
        )

    prompt = (
        "Transcribe the spoken content of this video verbatim. "
        "Output plain text only — no timestamps, no speaker labels, no headers. "
        "Do not summarize. If the video has no audible speech, output the "
        "visible on-screen text. If the video is empty or unintelligible, "
        "output the single token EMPTY."
    )

    try:
        pool = get_key_pool()
    except Exception as exc:  # noqa: BLE001
        return TierResult(
            tier=TierName.GEMINI_FILEDATA,
            transcript="",
            success=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=f"key-pool-unavailable: {exc}",
        )

    try:
        # Transcript fetch is part of the summarization pipeline by spec —
        # account this Gemini call against the 3-call budget.
        get_budget().consume(role="gemini_filedata")
        result = await pool.generate_content_youtube_url(
            video_id=video_id,
            prompt=prompt,
            model_hint=cfg_gem.get("model", "gemini-2.5-flash"),
            temperature=0.0,
            max_output_tokens=cfg_gem.get("max_output_tokens", 8192),
        )
        text = (result.text or "").strip()
        if text == "EMPTY" or len(text) < 100:
            return TierResult(
                tier=TierName.GEMINI_FILEDATA,
                transcript="",
                success=False,
                latency_ms=int((time.monotonic() - start) * 1000),
                error=f"empty-or-too-short: len={len(text)}",
            )
        return TierResult(
            tier=TierName.GEMINI_FILEDATA,
            transcript=text,
            success=True,
            confidence="high",
            latency_ms=int((time.monotonic() - start) * 1000),
            extra={
                "path": "gemini_filedata",
                "model": result.model,
                "key_index": result.key_index,
            },
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        non_retryable = any(
            sig in msg
            for sig in (
                "INVALID_ARGUMENT",
                "FAILED_PRECONDITION",
                "NOT_FOUND",
                "PERMISSION_DENIED",
                "must be public",
                "contents.parts must not be empty",
            )
        )
        return TierResult(
            tier=TierName.GEMINI_FILEDATA,
            transcript="",
            success=False,
            latency_ms=int((time.monotonic() - start) * 1000),
            error=(
                f"{'non-retryable' if non_retryable else 'retryable'}: {msg[:200]}"
            ),
            extra={"non_retryable": non_retryable},
        )


async def tier_metadata_only(video_id: str, config: dict) -> TierResult:
    """Tier 6: yt-dlp metadata-only fallback, with oEmbed + HTML og-tags safety net.

    yt-dlp is routinely blocked on datacenter IPs even for metadata-only
    extraction. When that happens, fall back to YouTube's public oEmbed
    endpoint (title + author, no auth required) and scrape the watch page
    for ``og:title`` / ``og:description``. At least one of these almost
    always returns enough text for the 50-char ingest floor.
    """
    from yt_dlp import YoutubeDL

    start = time.monotonic()
    url = f"https://www.youtube.com/watch?v={video_id}"
    title = ""
    description = ""
    channel = ""
    duration = 0
    ytdlp_err: str | None = None

    try:
        with YoutubeDL(
            {"quiet": True, "skip_download": True, "no_warnings": True}
        ) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        title = info.get("title", "") or ""
        description = info.get("description", "") or ""
        channel = info.get("channel", "") or ""
        duration = info.get("duration", 0) or 0
    except Exception as exc:
        ytdlp_err = str(exc)
        logger.warning("[yt-tier6] yt-dlp failed, trying oEmbed/og: %s", exc)

    if not title or not description:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                oembed = await client.get(
                    "https://www.youtube.com/oembed",
                    params={"url": url, "format": "json"},
                )
                if oembed.status_code == 200:
                    data = oembed.json()
                    title = title or data.get("title", "") or ""
                    channel = channel or data.get("author_name", "") or ""
        except Exception as exc:
            logger.warning("[yt-tier6] oEmbed failed: %s", exc)

    if not description:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ZettelkastenBot/1.0)"},
            ) as client:
                page = await client.get(url)
                if page.status_code == 200:
                    og_title = re.search(
                        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                        page.text,
                    )
                    og_desc = re.search(
                        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
                        page.text,
                    )
                    if og_title and not title:
                        title = og_title.group(1)
                    if og_desc and not description:
                        description = og_desc.group(1)
        except Exception as exc:
            logger.warning("[yt-tier6] og-scrape failed: %s", exc)

    text = "\n\n".join(part for part in (title, description) if part)
    raw_text_len = len(text)
    # H4/T6: emit explicit low-confidence signal so the route-boundary quality
    # gate (api/routes.py) can refuse to summarize near-empty metadata via 422
    # rather than silently letting Gemini hallucinate.
    logger.info(
        "[yt-tier-metadata_only] raw_text_len=%d title=%r channel=%r ytdlp_err=%s",
        raw_text_len, title[:80], channel[:80], (ytdlp_err or "")[:120],
    )
    return TierResult(
        tier=TierName.METADATA_ONLY,
        transcript=text,
        success=bool(title or description),
        confidence="low",
        latency_ms=int((time.monotonic() - start) * 1000),
        error=ytdlp_err if not (title or description) else None,
        extra={
            "title": title,
            "channel": channel,
            "duration": duration,
            "path": "metadata_only",
            "metadata_only": True,
            "raw_text_len": raw_text_len,
        },
    )


def build_default_chain(config: dict) -> TranscriptChain:
    return TranscriptChain(
        tiers=[
            tier_gemini_youtube_url,            # T1 — server-side fetch (H1)
            tier_transcript_api_via_webshare,   # T2 — Webshare proxy (H3)
            tier_ytdlp_cookies_impersonate,     # T3 — cookies+impersonate+POT (H3)
            tier_invidious_pool,                # T4 — Invidious pool
            tier_piped_pool,                    # T5 — Piped pool
            tier_gemini_audio,                  # T6 — Gemini audio fallback
            tier_metadata_only,                 # T7 — metadata-only (H2 gate refuses)
        ],
        budget_ms=config.get("transcript_budget_ms", 90000),
    )
