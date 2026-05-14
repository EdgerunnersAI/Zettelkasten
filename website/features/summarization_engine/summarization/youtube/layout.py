"""Dynamic composition of YouTube DetailedSummarySection hierarchy.

The renderer (website/core/summary_rendering.py::render_detailed_summary) converts
DetailedSummarySection + sub_sections into ``## h2`` / ``### h3`` / ``-``
bullet markdown, which the frontend's renderMarkdownLite parses directly.
Keeping layout logic here — not in common/structured.py — means it can be
unit-tested without importing the extractor.
"""
from __future__ import annotations

import re
from typing import Iterable

from website.features.summarization_engine.core.models import DetailedSummarySection
from website.features.summarization_engine.summarization.youtube.schema import (
    YouTubeStructuredPayload,
)

_TIMESTAMP_PLACEHOLDERS = {"", "n/a", "none", "00:00"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _first_sentence(text: str) -> str:
    cleaned = _clean(text)
    if not cleaned:
        return ""
    # Use the abbreviation-aware sentence splitter so the overview bullet
    # never truncates inside an abbreviation like ``U.S.`` (iter-20
    # regression: ``"...implications for U."`` on the Petrodollar
    # lecture).
    from website.features.summarization_engine.summarization.common.text_guards import (
        split_sentences,
    )
    sentences = split_sentences(cleaned)
    if sentences:
        return sentences[0]
    return cleaned


def _speaker_line(speakers: Iterable[str]) -> str:
    names = [_clean(s) for s in speakers if _clean(s)]
    if not names:
        return ""
    if len(names) == 1:
        return f"Speakers: {names[0]}"
    head, extra = names[0], len(names) - 1
    return f"Speakers: {head} (+{extra} more)"


def _format_and_speakers_bullets(payload: YouTubeStructuredPayload) -> list[str]:
    bullets: list[str] = []
    fmt = _clean(payload.detailed_summary.format or "")
    if fmt:
        bullets.append(f"Format: {fmt}")
    speaker_line = _speaker_line(payload.speakers)
    if speaker_line:
        bullets.append(speaker_line)
    return bullets


def _overview_section(payload: YouTubeStructuredPayload) -> DetailedSummarySection:
    primary = _first_sentence(payload.brief_summary) or "This video is captured in the Zettelkasten."
    sub_sections: dict[str, list[str]] = {}
    fmt_bullets = _format_and_speakers_bullets(payload)
    if fmt_bullets:
        sub_sections["Format and speakers"] = fmt_bullets
    thesis = _clean(payload.detailed_summary.thesis or "")
    if thesis:
        sub_sections["Core argument"] = [thesis]
    return DetailedSummarySection(
        heading="Overview",
        bullets=[primary],
        sub_sections=sub_sections,
    )


def _chapter_walkthrough_section(
    payload: YouTubeStructuredPayload,
) -> DetailedSummarySection | None:
    subs: dict[str, list[str]] = {}
    for chapter in payload.detailed_summary.chapters_or_segments or []:
        title = _clean(chapter.title) or "Segment"
        # Product decision (2026-04-25): never render timestamps in detailed
        # summaries. They are easy to hallucinate across sources and offer
        # little reader value. Keep only the topic heading.
        heading = title
        bullets = [_clean(b) for b in (chapter.bullets or []) if _clean(b)]
        if not bullets:
            continue
        base_heading = heading
        idx = 2
        while heading in subs:
            heading = f"{base_heading} ({idx})"
            idx += 1
        subs[heading] = bullets
    if not subs:
        return None
    return DetailedSummarySection(
        heading="Chapter walkthrough",
        bullets=[],
        sub_sections=subs,
    )


def _demonstrations_section(
    payload: YouTubeStructuredPayload,
) -> DetailedSummarySection | None:
    demos = [_clean(d) for d in (payload.detailed_summary.demonstrations or []) if _clean(d)]
    if not demos:
        return None
    return DetailedSummarySection(heading="Demonstrations", bullets=demos)


def _closing_remarks_section(
    payload: YouTubeStructuredPayload,
) -> DetailedSummarySection | None:
    """YouTube closing remarks are framed as a video recap.

    The bullet is labelled ``Recap: ...`` so the reader immediately orients to
    "this is the takeaway after watching" rather than a generic closing line.
    """
    takeaway = _clean(payload.detailed_summary.closing_takeaway or "")
    if not takeaway:
        return None
    bullet = takeaway if takeaway.lower().startswith("recap") else f"Recap: {takeaway}"
    return DetailedSummarySection(heading="Closing remarks", bullets=[bullet])


def compose_youtube_detailed(
    payload: YouTubeStructuredPayload,
) -> list[DetailedSummarySection]:
    sections: list[DetailedSummarySection] = [_overview_section(payload)]
    for maker in (
        _chapter_walkthrough_section,
        _demonstrations_section,
        _closing_remarks_section,
    ):
        section = maker(payload)
        if section is not None:
            sections.append(section)
    return sections
