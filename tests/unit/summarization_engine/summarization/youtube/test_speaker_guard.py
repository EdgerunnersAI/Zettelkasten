"""Placeholder-speaker coercion tests for YouTubeStructuredPayload.

Design note: an earlier iteration raised ValidationError on placeholder-only
speakers, but raising cascaded into schema_fallback (raw content dump) and
collapsed downstream summary quality from ~91 to ~13 composite score. The
validator now coerces placeholders to ``["The speaker"]`` so the structured
payload survives, and downstream code can still detect the neutral label.
"""
from __future__ import annotations

from website.features.summarization_engine.summarization.youtube.schema import (
    ChapterBullet,
    YouTubeDetailedPayload,
    YouTubeStructuredPayload,
)


def _detailed(**overrides) -> YouTubeDetailedPayload:
    base = dict(
        thesis="A complete thesis sentence.",
        format="lecture",
        chapters_or_segments=[
            ChapterBullet(
                timestamp="00:15",
                title="Intro",
                bullets=["A.", "B.", "C.", "D.", "E."],
            ),
        ],
        demonstrations=[],
        closing_takeaway="A closing sentence.",
    )
    base.update(overrides)
    return YouTubeDetailedPayload(**base)


def _base_kwargs(**speaker_overrides):
    kwargs = dict(
        mini_title="Test title 60 chars",
        brief_summary=(
            "Complete sentence one here. Complete sentence two follows. "
            "Complete sentence three ends."
        ),
        tags=[
            "tag-one", "tag-two", "tag-three", "tag-four",
            "tag-five", "tag-six", "tag-seven",
        ],
        speakers=["Joe Rogan"],
        entities_discussed=["Example"],
        detailed_summary=_detailed(),
    )
    kwargs.update(speaker_overrides)
    return kwargs


def test_placeholder_only_speakers_coerced_to_neutral_label():
    payload = YouTubeStructuredPayload(
        **_base_kwargs(speakers=["narrator", "host"])
    )
    assert payload.speakers == ["The speaker"]


def test_single_placeholder_speaker_coerced():
    payload = YouTubeStructuredPayload(**_base_kwargs(speakers=["the host"]))
    assert payload.speakers == ["The speaker"]


def test_real_speaker_passes():
    payload = YouTubeStructuredPayload(**_base_kwargs(speakers=["Joe Rogan"]))
    assert payload.speakers == ["Joe Rogan"]


def test_mixed_placeholders_and_real_speaker_passes_with_placeholders_dropped():
    payload = YouTubeStructuredPayload(
        **_base_kwargs(speakers=["narrator", "Joe Rogan", "host"])
    )
    assert payload.speakers == ["Joe Rogan"]


def test_case_insensitive_placeholders_coerced():
    payload = YouTubeStructuredPayload(
        **_base_kwargs(speakers=["Narrator", "HOST", "Presenter"])
    )
    assert payload.speakers == ["The speaker"]


def test_whitespace_only_speakers_coerced():
    payload = YouTubeStructuredPayload(**_base_kwargs(speakers=["   ", ""]))
    assert payload.speakers == ["The speaker"]


def test_chess_opening_is_not_treated_as_speaker():
    payload = YouTubeStructuredPayload(
        **_base_kwargs(
            speakers=["Queen's Gambit Declined"],
            entities_discussed=["Queen's Gambit Declined", "Claude Shannon"],
        )
    )
    assert payload.speakers == ["Claude Shannon"]
