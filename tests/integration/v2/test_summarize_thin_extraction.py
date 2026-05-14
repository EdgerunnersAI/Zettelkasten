"""SE-03: thin / unreachable extraction MUST raise BEFORE the LLM call.

Two pre-Gemini failure modes are contracted by the orchestrator
(``website.features.summarization_engine.core.orchestrator.summarize_url_bundle``):

  * ``ExtractionConfidenceError`` when the ingestor returns < 50 chars of
    real content after stripping section markers. Gemini is never called.
  * ``NewsletterURLUnreachable`` when the newsletter ingestor's preflight
    probe fails. It is re-raised so callers can surface a structured error.

Anti-pattern guard: the gemini_client supplied to the orchestrator must
NEVER be called for these scenarios. We use an exploding stub that raises
on any attribute access.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from website.features.summarization_engine.core.errors import (
    ExtractionConfidenceError,
    NewsletterURLUnreachable,
)
from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.core.orchestrator import (
    summarize_url_bundle,
)


_USER = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _ExplodingClient:
    """Any attribute access fails, proving Gemini is not invoked."""

    def __getattr__(self, name):  # noqa: D401
        raise AssertionError(
            f"Gemini was called for a pre-Gemini failure case (attr={name!r})"
        )


class _StubIngestor:
    """Registry-resolved ingestor replacement for pre-cooked results/errors."""

    version = "test-1.0.0"

    def __init__(self, *, raw_text: str = "", raise_exc: Exception | None = None):
        self._raw_text = raw_text
        self._raise = raise_exc

    async def ingest(self, url, *, config):
        if self._raise is not None:
            raise self._raise
        return IngestResult(
            source_type=SourceType.WEB,
            url=url,
            original_url=url,
            raw_text=self._raw_text,
            sections={"Article": self._raw_text},
            metadata={"title": "stub"},
            extraction_confidence="medium",
            confidence_reason="stub",
            fetched_at=datetime.now(timezone.utc),
            ingestor_version="test-1.0.0",
        )


@pytest.fixture
def patched_orchestrator(monkeypatch):
    """Patch the ingestor registry and disable the filesystem ingest cache."""

    state = {"ingestor": None}

    from website.features.summarization_engine.core import orchestrator as orch_mod

    def fake_get_ingestor(_st):
        return lambda: state["ingestor"]

    monkeypatch.setattr(orch_mod, "get_ingestor", fake_get_ingestor)
    monkeypatch.setattr(orch_mod._INGEST_CACHE, "get", lambda *_a, **_kw: None)
    monkeypatch.setattr(orch_mod._INGEST_CACHE, "put", lambda *_a, **_kw: None)

    def _set(stub):
        state["ingestor"] = stub
        return stub

    return _set


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_text",
    [
        "",
        "tiny",
        "## Video\n## Transcript\n## Description\nChannel:",
        "x" * 49,
    ],
)
async def test_thin_extraction_raises_before_gemini(
    patched_orchestrator, raw_text
) -> None:
    patched_orchestrator(_StubIngestor(raw_text=raw_text))
    with pytest.raises(ExtractionConfidenceError) as ei:
        await summarize_url_bundle(
            "https://example.com/thin",
            user_id=_USER,
            gemini_client=_ExplodingClient(),
            source_type=SourceType.WEB,
        )
    assert ei.value.url == "https://example.com/thin"


@pytest.mark.asyncio
async def test_thin_extraction_above_threshold_does_call_gemini(
    patched_orchestrator,
) -> None:
    body = "real article body " * 10
    patched_orchestrator(_StubIngestor(raw_text=body))

    with pytest.raises(AssertionError) as ei:
        await summarize_url_bundle(
            "https://example.com/real",
            user_id=_USER,
            gemini_client=_ExplodingClient(),
            source_type=SourceType.WEB,
        )
    assert "Gemini was called" in str(ei.value) or "attr=" in str(ei.value)


@pytest.mark.asyncio
async def test_newsletter_unreachable_raises_before_gemini(
    patched_orchestrator,
) -> None:
    patched_orchestrator(
        _StubIngestor(
            raise_exc=NewsletterURLUnreachable(
                url="https://dead.substack.com/p/post",
                status=404,
                reason="not_found",
            )
        )
    )
    with pytest.raises(NewsletterURLUnreachable) as ei:
        await summarize_url_bundle(
            "https://dead.substack.com/p/post",
            user_id=_USER,
            gemini_client=_ExplodingClient(),
            source_type=SourceType.NEWSLETTER,
        )
    assert ei.value.status == 404
    assert ei.value.url == "https://dead.substack.com/p/post"
