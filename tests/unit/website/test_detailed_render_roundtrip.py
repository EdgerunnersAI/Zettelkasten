# tests/unit/website/test_detailed_render_roundtrip.py
"""End-to-end Python-side render round-trip for the composed layout.

Guarantees the output of render_detailed_summary (consumed by the frontend's
renderMarkdownLite) contains:
  - `## Overview` h2
  - `### Format and speakers` and `### Core argument` h3 nested under Overview
  - `## Chapter walkthrough` h2 with per-chapter `### <title>` h3
  - `## Closing remarks` h2 (NOT `## Closing Takeaway`)
  - Only string bullets, never JSON-stringified dicts
"""
from __future__ import annotations

from website.core.summary_rendering import render_detailed_summary
from website.features.summarization_engine.summarization.youtube.layout import (
    compose_youtube_detailed,
)
from website.features.summarization_engine.summarization.youtube.schema import (
    ChapterBullet,
    YouTubeDetailedPayload,
    YouTubeStructuredPayload,
)


def _payload(**detailed_overrides) -> YouTubeStructuredPayload:
    detailed_defaults = dict(
        thesis="DMT is under-studied despite being produced in the human body.",
        format="lecture",
        chapters_or_segments=[
            ChapterBullet(
                timestamp="00:15",
                title="Intro",
                bullets=["A.", "B.", "C.", "D.", "E."],
            )
        ],
        demonstrations=[],
        closing_takeaway="DMT needs more rigorous clinical study.",
    )
    detailed_defaults.update(detailed_overrides)
    return YouTubeStructuredPayload(
        mini_title="DMT lecture",
        brief_summary=(
            "This lecture explains that DMT is a short-acting tryptamine produced "
            "endogenously. The closing takeaway is that rigorous study is needed."
        ),
        tags=[
            "psychedelics", "neuroscience", "lecture",
            "dmt", "pharmacology", "consciousness", "science",
        ],
        speakers=["Joe Rogan"],
        entities_discussed=["Rick Strassman", "MAPS"],
        detailed_summary=YouTubeDetailedPayload(**detailed_defaults),
    )


def test_markdown_round_trip_has_overview_h2():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "## Overview" in md


def test_markdown_round_trip_has_format_and_thesis_h3():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "### Format and speakers" in md
    assert "### Core argument" in md


def test_markdown_round_trip_has_chapter_walkthrough_h2_and_chapter_h3():
    # Product decision (2026-04-25): timestamps stripped from chapter headings.
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "## Chapter walkthrough" in md
    assert "### Intro" in md
    assert "### 00:15" not in md


def test_markdown_round_trip_uses_closing_remarks_not_closing_takeaway():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "## Closing remarks" in md
    assert "Closing Takeaway" not in md


def test_markdown_round_trip_has_no_json_leakage():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "{\"" not in md
    assert "{'" not in md
    assert "timestamp\":" not in md


def test_markdown_round_trip_omits_demonstrations_when_empty():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    assert "## Demonstrations" not in md


def test_markdown_round_trip_includes_demonstrations_when_present():
    md = render_detailed_summary(
        compose_youtube_detailed(_payload(demonstrations=["Live DMT extraction demo."]))
    )
    assert "## Demonstrations" in md
    assert "Live DMT extraction demo." in md
