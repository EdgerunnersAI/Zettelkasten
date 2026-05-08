"""Tests asserting polish + caveat-strip + reddit-tag rewrite at WRITE time.

Hooks at the read boundary already polish ``/api/graph`` responses live, but
the source-of-truth row in ``kg_nodes.summary`` (and the file-backed
``graph.json``) must also be born clean so persisted text is never re-rendered
by downstream consumers without running the read-time normalizer.
"""
from __future__ import annotations

import json

from website.core.persist import _encode_summary_payload
from website.core.text_polish import polish, polish_envelope, rewrite_reddit_tag


def test_encode_summary_payload_strips_caveats_and_polishes():
    payload = {
        "brief_summary": "OP raised concerns. Caveat: divergence 65%.",
        "detailed_summary": "Along the way The speaker references Windows 10.",
    }
    encoded = _encode_summary_payload(payload)
    parsed = json.loads(encoded)
    assert "Caveat" not in parsed["brief_summary"]
    assert "OP raised concerns" in parsed["brief_summary"]
    assert "Along the way, The speaker" in parsed["detailed_summary"]


def test_encode_summary_payload_idempotent():
    payload = {
        "brief_summary": "However things changed. Caveat: skip me.",
        "detailed_summary": "Karpathy s LLM intro.",
    }
    once = _encode_summary_payload(payload)
    parsed_once = json.loads(once)
    twice = _encode_summary_payload(parsed_once)
    parsed_twice = json.loads(twice)
    assert parsed_once == parsed_twice


def test_polish_envelope_idempotent_on_clean_input():
    env = {
        "mini_title": "Karpathy's LLM",
        "brief_summary": "However, the speaker spoke.",
        "detailed_summary": [
            {"heading": "Overview", "bullets": ["Real bullet."], "sub_sections": {}}
        ],
        "closing_remarks": "He stopped.",
    }
    once = polish_envelope(env)
    twice = polish_envelope(once)
    assert once == twice


def test_polish_polish_x_equals_polish_x():
    s = (
        "Along the way The speaker references Windows 10. "
        "Karpathy s LLM intro covers 'product-minded engineers,' he said. "
        "Caveat: divergence 65%."
    )
    once = polish(s)
    twice = polish(once)
    assert once == twice


def test_reddit_tag_rewrite_at_write_boundary():
    assert rewrite_reddit_tag("r-hinduism") == "r/hinduism"
    # Already-rewritten tags untouched.
    assert rewrite_reddit_tag("r/hinduism") == "r/hinduism"
    # Unrelated tag untouched.
    assert rewrite_reddit_tag("ml") == "ml"


def test_persist_file_node_polishes_before_add(monkeypatch):
    """Asserts ``_persist_file_node`` calls ``add_node`` with a summary
    string whose envelope has been polished and whose tags are reddit-rewritten.
    """
    from website.core import persist

    captured: dict = {}

    def fake_add_node(*, title, source_type, source_url, summary, tags):
        captured["summary"] = summary
        captured["tags"] = tags
        return "rd-test"

    monkeypatch.setattr(persist, "add_node", fake_add_node)

    payload = {
        "title": "Test",
        "source_type": "reddit",
        "source_url": "https://reddit.com/r/test/comments/x",
        "brief_summary": "OP said something. Caveat: divergence 50%.",
        "detailed_summary": "However The speaker spoke.",
        "tags": ["r-test", "philosophy"],
    }
    persist._persist_file_node(payload, skip_duplicate=False)

    parsed = json.loads(captured["summary"])
    assert "Caveat" not in parsed["brief_summary"]
    assert "However, The speaker" in parsed["detailed_summary"]
    assert captured["tags"] == ["r/test", "philosophy"]


def test_engine_supabase_writer_polishes_envelope():
    """``_encode_summary_blob`` in the engine writer applies polish_envelope."""
    from website.features.summarization_engine.core.models import (
        DetailedSummarySection,
        SourceType,
        SummaryMetadata,
        SummaryResult,
    )
    from website.features.summarization_engine.writers.supabase import _encode_summary_blob

    result = SummaryResult(
        mini_title="Karpathy s talk",
        brief_summary="Caveat: pipeline note. The talk reviews LLMs.",
        tags=["llm", "ml"],
        detailed_summary=[
            DetailedSummarySection(
                heading="Overview",
                bullets=["Along the way The speaker noted X.", "Caveat: ignore."],
                sub_sections={},
            )
        ],
        metadata=SummaryMetadata(
            source_type=SourceType.YOUTUBE,
            url="https://youtu.be/x",
            extraction_confidence="high",
            confidence_reason="test",
            total_tokens_used=0,
            total_latency_ms=0,
            engine_version="2.0",
        ),
    )
    blob = _encode_summary_blob(result)
    parsed = json.loads(blob)
    assert "Caveat" not in parsed["brief_summary"]
    assert all("Caveat" not in b for b in parsed["detailed_summary"][0]["bullets"])
    assert "Along the way, The speaker" in parsed["detailed_summary"][0]["bullets"][0]


def test_render_markdown_strips_caveats_and_rewrites_reddit_tag():
    """Markdown rendered for Obsidian / GitHub writers must be clean."""
    from website.features.summarization_engine.core.models import (
        DetailedSummarySection,
        SourceType,
        SummaryMetadata,
        SummaryResult,
    )
    from website.features.summarization_engine.writers.markdown import render_markdown

    result = SummaryResult(
        mini_title="Reddit note",
        brief_summary="OP wrote. Caveat: skip.",
        tags=["r-hinduism", "philosophy"],
        detailed_summary=[
            DetailedSummarySection(
                heading="Overview",
                bullets=["Caveat: pipeline metadata.", "Real bullet."],
                sub_sections={},
            )
        ],
        metadata=SummaryMetadata(
            source_type=SourceType.REDDIT,
            url="https://reddit.com/r/hinduism/x",
            extraction_confidence="high",
            confidence_reason="test",
            total_tokens_used=0,
            total_latency_ms=0,
            engine_version="2.0",
        ),
    )
    md = render_markdown(result)
    assert "r/hinduism" in md
    assert "r-hinduism" not in md
    assert "Caveat" not in md
    assert "Real bullet." in md
