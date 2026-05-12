"""SE-03: thin / unreachable extraction MUST raise BEFORE the LLM call.

Two pre-Gemini failure modes are contracted by the orchestrator
(``website.features.summarization_engine.core.orchestrator.summarize_url_bundle``):

  * ``ExtractionConfidenceError`` — when the ingestor returns < 50 chars of
    real content (after stripping section markers). Raised at orchestrator
    line ~131; Gemini is never called.
  * ``NewsletterURLUnreachable`` — when the newsletter ingestor's preflight
    probe fails. Re-raised by the orchestrator (line ~115) so callers can
    surface a structured "dead URL" error.

Plus: the legacy ``/api/summarize`` `is_raw_fallback=True` path. The current
shape is ``is_raw_fallback=False`` hardcoded in
``website.core.pipeline._to_legacy_response`` — the True path is reserved
for a future engine-side fallback exception handler. We pin the current
contract so a refactor cannot silently flip the flag without updating this
test.

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
    """Any attribute access fails — proves Gemini is not invoked."""

    def __getattr__(self, name):  # noqa: D401
        raise AssertionError(
            f"Gemini was called for a pre-Gemini failure case (attr={name!r})"
        )


class _StubIngestor:
    """Replace the registry-resolved ingestor with one that returns a
    pre-cooked ``IngestResult`` (or raises a chosen exception)."""

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
    """Patch ``get_ingestor`` to return a stub class on demand AND disable
    the FsContentCache so previously-cached real runs cannot satisfy the
    test (cache hit short-circuits the ingestor call)."""

    state = {"ingestor": None}

    from website.features.summarization_engine.core import orchestrator as orch_mod

    def fake_get_ingestor(_st):
        # The orchestrator does ``get_ingestor(...)()`` — i.e. expects a
        # zero-arg callable that yields an ingestor instance. We return a
        # lambda whose call yields the per-test stub.
        return lambda: state["ingestor"]

    monkeypatch.setattr(orch_mod, "get_ingestor", fake_get_ingestor)

    # Disable cache: get always returns None so the ingestor is invoked.
    monkeypatch.setattr(orch_mod._INGEST_CACHE, "get", lambda *_a, **_kw: None)
    monkeypatch.setattr(orch_mod._INGEST_CACHE, "put", lambda *_a, **_kw: None)

    def _set(stub):
        state["ingestor"] = stub
        return stub

    return _set


# --- Thin extraction → ExtractionConfidenceError, no Gemini call ----------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_text",
    [
        "",  # empty
        "tiny",  # 4 chars
        "## Video\n## Transcript\n## Description\nChannel:",  # all-marker
        "x" * 49,  # one short of the 50-char floor
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
    # The exception carries useful diagnostics for the API caller.
    assert ei.value.url == "https://example.com/thin"


@pytest.mark.asyncio
async def test_thin_extraction_above_threshold_does_call_gemini(
    patched_orchestrator,
) -> None:
    """Sanity: 50+ chars of real content does NOT trigger the guard."""
    body = "real article body " * 10  # ~180 chars
    patched_orchestrator(_StubIngestor(raw_text=body))

    # We don't have a real summarizer wired here — just prove that the
    # orchestrator gets PAST the thin-extraction guard. The downstream
    # summarizer call will fail on the exploding client, and that
    # AssertionError is the proof of life.
    with pytest.raises(AssertionError) as ei:
        await summarize_url_bundle(
            "https://example.com/real",
            user_id=_USER,
            gemini_client=_ExplodingClient(),
            source_type=SourceType.WEB,
        )
    assert "Gemini was called" in str(ei.value) or "attr=" in str(ei.value)


# --- Newsletter unreachable → NewsletterURLUnreachable, no Gemini call -----


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


# --- Legacy /api/summarize: is_raw_fallback flag is currently always False --


def test_legacy_response_pins_is_raw_fallback_false() -> None:
    """``website.core.pipeline._to_legacy_response`` hardcodes
    ``is_raw_fallback=False`` (per CLAUDE.md note KP-07). The True path lives
    in the engine's exception handler — when the engine surfaces a True
    fallback the wrapper will need to lift the flag. Pin the current
    contract so a silent refactor is caught.
    """
    from types import SimpleNamespace

    from website.core.pipeline import _to_legacy_response

    # Build a minimal fake SummaryResult for the wrapper.
    metadata = SimpleNamespace(
        source_type=SimpleNamespace(value="web"),
        url="https://example.com",
        total_tokens_used=100,
        total_latency_ms=200,
        model_dump=lambda mode="json", exclude_none=True: {
            "source_type": "web",
            "url": "https://example.com",
        },
    )
    engine_result = SimpleNamespace(
        mini_title="Title",
        brief_summary="Brief",
        detailed_summary=[],
        tags=["tag1"],
        metadata=metadata,
    )

    payload = _to_legacy_response(engine_result, ingest_result=None)
    assert payload["is_raw_fallback"] is False, (
        "Legacy /api/summarize wrapper should pin is_raw_fallback=False; "
        "if you intend to surface engine-side raw fallback, update both "
        "the wrapper and this contract test."
    )
    assert payload["title"] == "Title"
    assert payload["source_type"] == "web"
