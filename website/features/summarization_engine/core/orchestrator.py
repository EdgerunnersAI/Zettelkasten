"""Single-URL orchestrator: route, ingest, summarize, return."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from website.core.url_utils import validate_url

from website.features.summarization_engine.core.budget import budget_scope
from website.features.summarization_engine.core.cache import FsContentCache
from website.features.summarization_engine.core.config import load_config
from website.features.summarization_engine.core.errors import (
    ExtractionConfidenceError,
    NewsletterURLUnreachable,
    RoutingError,
    UnsupportedURLShapeError,
    UnsupportedVideoError,
)
from website.features.summarization_engine.core.models import (
    IngestResult,
    SourceType,
    SummaryResult,
)
from website.features.summarization_engine.core.router import detect_route_decision
from website.features.summarization_engine.source_ingest import get_ingestor
from website.features.summarization_engine.summarization import get_summarizer

logger = logging.getLogger("summarization_engine.orchestrator")

_CACHE_ROOT = Path(__file__).resolve().parents[4] / "docs" / "summary_eval" / "_cache"
_INGEST_CACHE = FsContentCache(root=_CACHE_ROOT, namespace="ingests")


def _is_youtube_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"} or host.endswith(".youtube.com")


def _yt_preflight_refuse(url: str) -> tuple[bool, str] | None:
    """H4/T7: cheap preflight refusal for hard-fail YouTube URLs.

    Returns (True, refuse_reason) if the URL is a known hard-fail case
    (private / removed / livestream / premiere / members-only); None if the
    URL should proceed through the tier chain. Uses yt-dlp metadata-only
    (--simulate --dump-single-json) — no LLM call, no proxy. Bot-detection
    or transient errors fall through to the tier chain (cookies+impersonate
    handles them).
    """
    if not _is_youtube_url(url):
        return None
    try:
        from yt_dlp import YoutubeDL
        opts = {
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
            "simulate": True,
            "dump_single_json": True,
            "extractor_args": {"youtube": {"player_client": ["tv_simply"]}},
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        if info.get("is_live"):
            return (True, "active_livestream")
        if info.get("live_status") in {"is_upcoming", "post_live"}:
            return (True, "premiere_or_post_live")
        if info.get("availability") == "needs_auth":
            return (True, "members_only_or_age_restricted")
        if info.get("availability") == "private":
            return (True, "private")
        return None
    except Exception as exc:
        msg = str(exc).lower()
        if "private video" in msg or "is private" in msg:
            return (True, "private")
        if "requested format is not available" in msg:
            return None
        if "removed" in msg or "no longer available" in msg or "video unavailable" in msg:
            return (True, "removed_or_unavailable")
        if "premiere" in msg or "scheduled" in msg or "live event" in msg:
            return (True, "premiere_or_live")
        # Bot-detection / transient: let tier chain handle.
        return None


@dataclass(frozen=True)
class OrchestratedSummary:
    """Combined ingest + summary result for downstream persistence."""

    ingest_result: IngestResult
    summary_result: SummaryResult


async def summarize_url(
    url: str,
    *,
    user_id: UUID,
    gemini_client: Any,
    source_type: SourceType | None = None,
) -> SummaryResult:
    """Run the ingest and summarize pipeline and return only the summary."""
    return (await summarize_url_bundle(
        url,
        user_id=user_id,
        gemini_client=gemini_client,
        source_type=source_type,
    )).summary_result


async def summarize_url_bundle(
    url: str,
    *,
    user_id: UUID,
    gemini_client: Any,
    source_type: SourceType | None = None,
) -> OrchestratedSummary:
    """Run the ingest and summarize pipeline for a single URL.

    The engine is a pure library here; callers compose persistence writers.

    YouTube note: transcript extraction fails on datacenter IPs (blocked by
    YouTube).  The YouTube ingestor falls back to yt-dlp metadata (title,
    description) and marks confidence as "medium".  A previous Gemini video-
    understanding fallback was removed because ``Part.from_uri`` with YouTube
    watch URLs does not actually analyse the video via the API-key SDK — it
    causes Gemini to hallucinate unrelated content, producing worse results
    than the yt-dlp metadata path.
    """
    if not validate_url(url):
        raise RoutingError("Invalid or blocked URL", url=url)

    route_decision = detect_route_decision(url)
    if source_type is not None and source_type != route_decision.source_type:
        route_decision = route_decision.__class__(
            source_type=source_type,
            subtype=route_decision.subtype,
            supported=route_decision.supported,
            reason=route_decision.reason,
        )
    if not route_decision.supported:
        raise UnsupportedURLShapeError(
            source_type=route_decision.source_type.value,
            subtype=route_decision.subtype,
            reason=route_decision.reason or "unsupported_url_shape",
            url=url,
        )
    effective_source_type = route_decision.source_type

    # H4/T7: preflight refuse for hard-fail YouTube URLs (no LLM budget burned).
    preflight = _yt_preflight_refuse(url)
    if preflight is not None:
        refused, reason = preflight
        if refused:
            logger.warning("orchestrator.yt_preflight_refuse url=%s reason=%s", url, reason)
            raise UnsupportedVideoError(reason=reason, url=url)

    config = load_config()
    logger.info(
        "orchestrator.start url=%s user_id=%s source_type=%s",
        url,
        user_id,
        effective_source_type.value,
    )

    ingestor_cls = get_ingestor(effective_source_type)
    ingestor = ingestor_cls()
    source_config = config.sources.get(effective_source_type.value, {})
    ingest_cache_key = (
        url,
        getattr(ingestor, "version", "1.0.0"),
        effective_source_type.value,
    )
    cached = _INGEST_CACHE.get(ingest_cache_key)
    if cached:
        logger.info("orchestrator.ingest_cache_hit url=%s", url)
        ingest_result = IngestResult(
            **{key: value for key, value in cached.items() if not key.startswith("_")}
        )
    else:
        try:
            ingest_result = await ingestor.ingest(url, config=source_config)
        except NewsletterURLUnreachable as exc:
            # Surface dead URL as a structured failure BEFORE calling Gemini.
            # The exception already carries status/reason for callers/eval harness.
            logger.warning(
                "orchestrator.newsletter_unreachable url=%s status=%s reason=%s",
                exc.url,
                exc.status,
                exc.reason,
            )
            raise
        _INGEST_CACHE.put(ingest_cache_key, ingest_result.model_dump(mode="json"))

    ingest_result.metadata.setdefault("route_subtype", route_decision.subtype)
    ingest_result.metadata.setdefault("route_supported", route_decision.supported)
    if route_decision.reason:
        ingest_result.metadata.setdefault("route_reason", route_decision.reason)

    if ingest_result.extraction_confidence == "low":
        logger.warning(
            "orchestrator.low_confidence url=%s reason=%s raw_text_len=%d",
            url, ingest_result.confidence_reason, len(ingest_result.raw_text),
        )

    # Refuse to summarize near-empty content — the LLM will hallucinate.
    # Strip section headers (## Video, ## Transcript, etc.) and whitespace
    # to measure actual content length.
    _MIN_CONTENT_CHARS = 50
    stripped = ingest_result.raw_text
    for marker in ("## Video", "## Transcript", "## Description", "Channel:"):
        stripped = stripped.replace(marker, "")
    if len(stripped.strip()) < _MIN_CONTENT_CHARS:
        raise ExtractionConfidenceError(
            f"Insufficient content extracted ({len(stripped.strip())} chars). "
            f"Reason: {ingest_result.confidence_reason}",
            source_type=effective_source_type.value,
            reason=ingest_result.confidence_reason,
            tier_results=ingest_result.metadata.get("tier_results") or [],
            url=url,
        )

    summarizer_cls = get_summarizer(effective_source_type)
    summarizer = summarizer_cls(gemini_client, source_config)
    # C6: enforce the per-request 3-call LLM budget. Scope wraps only the
    # summarize() call so transcript/ingest work above is unaffected.
    async with budget_scope(summarizer=effective_source_type.value):
        summary_result = await summarizer.summarize(ingest_result)
    structured_payload = dict(summary_result.metadata.structured_payload or {})
    structured_payload.setdefault("route_subtype", route_decision.subtype)
    structured_payload.setdefault("route_supported", route_decision.supported)
    summary_result.metadata.structured_payload = structured_payload
    return OrchestratedSummary(
        ingest_result=ingest_result,
        summary_result=summary_result,
    )
