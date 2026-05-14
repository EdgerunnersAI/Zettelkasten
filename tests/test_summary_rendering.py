"""Tests for summary rendering helpers shared by website API callers."""

from __future__ import annotations

from website.core.summary_rendering import render_detailed_summary
from website.features.summarization_engine.core.models import DetailedSummarySection


def test_render_detailed_summary_outputs_frontend_markdown_shape():
    sections = [
        DetailedSummarySection(
            heading="Main",
            bullets=["Shared pipeline."],
            sub_sections={"Details": ["Nested point."]},
        )
    ]

    assert render_detailed_summary(sections) == (
        "## Main\n"
        "- Shared pipeline.\n"
        "\n"
        "### Details\n"
        "- Nested point."
    )
