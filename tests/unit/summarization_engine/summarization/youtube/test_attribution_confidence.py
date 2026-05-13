"""H2/C2 — first-class ``attribution_confidence`` field on YouTube payloads.

Soft-coerce + label pattern (industry standard, mirrors OpenAI structured-outputs
``refusal``). The validator never raises on placeholder speakers; instead it
records confidence as ``high`` / ``low`` / ``missing``.
"""
from __future__ import annotations

from website.features.summarization_engine.summarization.youtube.schema import (
    ChapterBullet,
    YouTubeDetailedPayload,
    YouTubeStructuredPayload,
)


def _payload(*, speakers, entities=None):
    return YouTubeStructuredPayload(
        mini_title="test title",
        brief_summary=(
            "A. B. C. D. E. Full brief needed to pass repair helpers."
        ),
        tags=["a", "b", "c", "d", "e", "f", "g"],
        speakers=speakers,
        entities_discussed=entities or [],
        detailed_summary=YouTubeDetailedPayload(
            thesis="Thesis.",
            format="lecture",
            chapters_or_segments=[
                ChapterBullet(
                    timestamp="00:00",
                    title="X",
                    bullets=["a.", "b.", "c.", "d.", "e."],
                )
            ],
            demonstrations=[],
            closing_takeaway="Done.",
        ),
    )


def test_attribution_high_when_all_real():
    payload = _payload(speakers=["Joe Rogan", "Lex Fridman"])
    assert payload.attribution_confidence == "high"
    assert payload.speakers == ["Joe Rogan", "Lex Fridman"]


def test_attribution_low_when_mixed_real_and_placeholder():
    payload = _payload(speakers=["Joe Rogan", "unidentified host"])
    assert payload.attribution_confidence == "low"
    assert payload.speakers == ["Joe Rogan"]


def test_attribution_missing_when_all_placeholders_and_empty_entities():
    payload = _payload(speakers=["unidentified host"], entities=[])
    assert payload.attribution_confidence == "missing"
    assert payload.speakers == ["The speaker"]


def test_attribution_missing_when_coerced_from_entities():
    payload = _payload(
        speakers=["unidentified host"],
        entities=["MAPS", "Rick Strassman", "FDA"],
    )
    # Coerced via step 3 — still "missing" because original speakers were unusable.
    assert payload.attribution_confidence == "missing"
    assert payload.speakers == ["Rick Strassman"]
