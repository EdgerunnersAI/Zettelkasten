"""H2/C4 — per-summary confidence grading and HTTP 422 refusal threshold."""
from __future__ import annotations

from website.features.summarization_engine.core.confidence import (
    INSUFFICIENT_CHARS,
    LOW_CHARS,
    grade,
)


def test_high_when_long_and_real_tier():
    label, reason = grade(2000, "transcript_api_direct")
    assert label == "high"
    assert reason == ""


def test_low_when_short_but_real_tier():
    label, reason = grade(800, "transcript_api_direct")
    assert label == "low"
    assert "800" in reason


def test_low_when_long_but_metadata_only_tier():
    label, reason = grade(800, "metadata_only")
    assert label == "low"
    assert "metadata_only" in reason


def test_insufficient_when_short_and_metadata_only():
    label, reason = grade(300, "metadata_only")
    assert label == "insufficient"
    assert "300" in reason


def test_short_real_tier_is_low_not_insufficient():
    # Only the metadata-only path triggers refusal; a short transcript is "low".
    label, reason = grade(300, "transcript_api_direct")
    assert label == "low"
    assert reason


def test_boundary_low_chars_is_low_not_high():
    # `<LOW_CHARS` is low; the boundary value itself is exactly LOW_CHARS.
    label, _ = grade(LOW_CHARS, "transcript_api_direct")
    # raw_text_len == LOW_CHARS is NOT < LOW_CHARS, so high applies.
    assert label == "high"

    label, _ = grade(LOW_CHARS - 1, "transcript_api_direct")
    assert label == "low"


def test_boundary_insufficient_chars():
    # `<INSUFFICIENT_CHARS` triggers refusal only with metadata_only.
    label, _ = grade(INSUFFICIENT_CHARS - 1, "metadata_only")
    assert label == "insufficient"
    label, _ = grade(INSUFFICIENT_CHARS, "metadata_only")
    assert label == "low"
