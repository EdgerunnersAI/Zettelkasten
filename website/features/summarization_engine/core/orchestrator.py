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
)
from website.features.summarization_engine.core.models import (
    IngestResult,
    SourceType,
    SummaryResult,
)
from website.features.summarization_engine.core.router import detect_source_type
from website.features.summarization_engine.source_ingest import get_ingestor
from website.features.summarization_engine.summarization import get_summarizer

logger = logging.getLogger("summarization_engine.orchestrator")

_CACHE_ROOT = Path(__file__).resolve().parents[4] / "docs" / "summary_eval" / "_cache"
_INGEST_CACHE = FsContentCache(root=_CACHE_ROOT, namespace="ingests")


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

    config = load_config()
    effective_source_type = source_type or detect_source_type(url)
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
    return OrchestratedSummary(
        ingest_result=ingest_result,
        summary_result=summary_result,
    )
