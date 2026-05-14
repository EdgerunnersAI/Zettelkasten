"""Port of renderMarkdownLite (user_zettels.js lines 1318-1368) to Python.

Mirrors the frontend parser's state machine so we can assert the composed
YouTube markdown only uses constructs the frontend actually understands
(## h2, ### h3, - bullets, paragraphs). If this test starts failing, either
the backend is emitting something the frontend can't parse or the frontend
added a construct we need to mirror.
"""
from __future__ import annotations

import re

from website.core.summary_rendering import render_detailed_summary
from website.features.summarization_engine.summarization.youtube.layout import (
    compose_youtube_detailed,
)
from website.features.summarization_engine.summarization.youtube.schema import (
    ChapterBullet,
    YouTubeDetailedPayload,
    YouTubeStructuredPayload,
)


def parse_markdown_lite(md: str) -> list[dict]:
    """Mirror of renderMarkdownLite. Returns a flat list of node dicts."""
    lines = md.split("\n")
    nodes: list[dict] = []
    para_buf: list[str] = []
    list_open = False

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            text = " ".join(para_buf).strip()
            if text:
                nodes.append({"type": "para", "text": text})
            para_buf = []

    def close_list() -> None:
        nonlocal list_open
        list_open = False

    for raw in lines:
        trimmed = re.sub(r"\s+$", "", raw)
        if not trimmed.strip():
            flush_para()
            close_list()
            continue
        h3 = re.match(r"^###\s+(.*)$", trimmed)
        h2 = re.match(r"^##\s+(.*)$", trimmed)
        bullet = re.match(r"^\s*[-*]\s+(.*)$", trimmed)
        if h2 or h3:
            flush_para()
            close_list()
            if h2:
                nodes.append({"type": "h2", "text": h2.group(1).strip()})
            else:
                nodes.append({"type": "h3", "text": h3.group(1).strip()})
            continue
        if bullet:
            flush_para()
            list_open = True
            nodes.append({"type": "li", "text": bullet.group(1).strip()})
            continue
        close_list()
        para_buf.append(trimmed.strip())
    flush_para()
    close_list()
    return nodes


def _payload() -> YouTubeStructuredPayload:
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
        entities_discussed=["Strassman"],
        detailed_summary=YouTubeDetailedPayload(
            thesis="DMT is under-studied.",
            format="lecture",
            chapters_or_segments=[
                ChapterBullet(
                    timestamp="00:15",
                    title="Intro",
                    bullets=["A.", "B.", "C.", "D.", "E."],
                )
            ],
            demonstrations=["Live demo."],
            closing_takeaway="DMT needs more study.",
        ),
    )


def test_frontend_parser_sees_overview_and_chapter_and_closing():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    nodes = parse_markdown_lite(md)
    h2_texts = [n["text"] for n in nodes if n["type"] == "h2"]
    h3_texts = [n["text"] for n in nodes if n["type"] == "h3"]
    assert "Overview" in h2_texts
    assert "Chapter walkthrough" in h2_texts
    assert "Demonstrations" in h2_texts
    assert "Closing remarks" in h2_texts
    assert "Format and speakers" in h3_texts
    assert "Core argument" in h3_texts
    # Product decision (2026-04-25): timestamps stripped from chapter headings.
    assert "Intro" in h3_texts
    assert not any(h.startswith("00:") for h in h3_texts)


def test_frontend_parser_finds_all_chapter_bullets():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    nodes = parse_markdown_lite(md)
    bullets = [n["text"] for n in nodes if n["type"] == "li"]
    # Overview first-sentence bullet + 2 format/speakers + 1 thesis + 5 chapter + 1 demo + 1 closing = 11
    assert len(bullets) >= 9
    # No leaked JSON braces.
    assert not any("{" in b or "}" in b for b in bullets)


def test_no_unknown_markdown_constructs_emitted():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    # renderMarkdownLite does not parse: h1, h4, h5, numbered lists, code blocks, blockquotes.
    forbidden = (r"^# ", r"^#### ", r"^##### ", r"^\s*\d+\.\s", r"^```", r"^> ")
    for line in md.split("\n"):
        for pat in forbidden:
            assert not re.match(pat, line), f"forbidden construct in line: {line!r}"


def test_frontend_parser_preserves_bullet_text_intact():
    md = render_detailed_summary(compose_youtube_detailed(_payload()))
    nodes = parse_markdown_lite(md)
    bullets = [n["text"] for n in nodes if n["type"] == "li"]
    # The 5 chapter bullets should appear verbatim.
    for expected in ["A.", "B.", "C.", "D.", "E."]:
        assert expected in bullets, f"missing chapter bullet: {expected!r}"
    # The closing takeaway bullet — layout prefixes YouTube closings with "Recap: ".
    assert "Recap: DMT needs more study." in bullets
    # The demo bullet.
    assert "Live demo." in bullets
