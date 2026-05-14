"""Typed exceptions for the summarization engine."""
from __future__ import annotations


class EngineError(Exception):
    """Base class for all summarization engine errors."""


class RoutingError(EngineError):
    """Raised when a URL cannot be routed to a source type."""

    def __init__(self, message: str, *, url: str = ""):
        super().__init__(message)
        self.url = url


class ExtractionError(EngineError):
    """Raised when source ingestion fails."""

    def __init__(
        self,
        message: str,
        *,
        source_type: str = "",
        reason: str = "",
    ):
        super().__init__(message)
        self.source_type = source_type
        self.reason = reason


class ExtractionConfidenceError(ExtractionError):
    """Raised when extraction confidence is low and caller rejects it.

    Optionally carries ``tier_results`` (per-tier diagnostics) and ``url`` so
    HTTP callers can surface a structured failure payload.
    """

    def __init__(
        self,
        message: str,
        *,
        source_type: str = "",
        reason: str = "",
        tier_results: list[dict] | None = None,
        url: str = "",
    ):
        super().__init__(message, source_type=source_type, reason=reason)
        self.tier_results = list(tier_results) if tier_results else []
        self.url = url


class NewsletterURLUnreachable(ExtractionError):
    """Raised when a newsletter URL is unreachable via preflight probe.

    Surfaced BEFORE any Gemini/summarization call so the eval harness (and
    API callers) can distinguish dead URLs from real extraction failures.
    """

    def __init__(self, url: str, status: int | None, reason: str):
        message = f"Newsletter URL unreachable: {url} (status={status}, reason={reason})"
        super().__init__(
            message,
            source_type="newsletter",
            reason=reason or (f"http_{status}" if status else "network"),
        )
        self.url = url
        self.status = status


class UnsupportedVideoError(EngineError):
    """Raised when a YouTube URL is a hard-fail case detected at preflight.

    Hard-fail reasons (no LLM call wasted): private, removed_or_unavailable,
    active_livestream, premiere_or_post_live, members_only_or_age_restricted,
    premiere_or_live. Surfaced as HTTP 422 with structured detail by the
    route handler.
    """

    def __init__(self, *, reason: str, url: str = ""):
        super().__init__(f"Unsupported video type: {reason} ({url})")
        self.reason = reason
        self.url = url


class UnsupportedURLShapeError(RoutingError):
    """Raised when a source family is known but the URL object is unsupported."""

    def __init__(
        self,
        *,
        source_type: str,
        subtype: str,
        reason: str,
        url: str = "",
    ):
        super().__init__(
            f"Unsupported {source_type} URL shape: {subtype} ({reason})",
            url=url,
        )
        self.source_type = source_type
        self.subtype = subtype
        self.reason = reason


class SummarizationError(EngineError):
    """Raised when the LLM summarization pipeline fails."""


class WriterError(EngineError):
    """Raised when a writer fails to persist a result."""

    def __init__(self, message: str, *, writer: str = ""):
        super().__init__(message)
        self.writer = writer


class GeminiError(EngineError):
    """Raised for Gemini API errors not handled by the key pool."""


class RateLimitedError(GeminiError):
    """Raised when rate-limited and pool exhausted."""

    def __init__(self, message: str, *, retry_after_sec: int = 0):
        super().__init__(message)
        self.retry_after_sec = retry_after_sec
