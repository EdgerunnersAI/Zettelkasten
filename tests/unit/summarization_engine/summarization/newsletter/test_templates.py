"""Tests for per-shape newsletter templates."""
from __future__ import annotations

from website.features.summarization_engine.summarization.newsletter.shapes import (
    NewsletterShape,
)
from website.features.summarization_engine.summarization.newsletter.templates import (
    template_for,
)


def test_every_shape_has_a_template():
    for shape in NewsletterShape:
        tmpl = template_for(shape)
        assert isinstance(tmpl, str)
        assert tmpl.strip(), f"Empty template for shape {shape}"


def test_academic_roundup_tolerates_paper_fact_words():
    tmpl = template_for(NewsletterShape.ACADEMIC_ROUNDUP)
    lowered = tmpl.lower()
    # ACADEMIC_ROUNDUP explicitly tolerates "novel"/"new"/"first" as facts.
    assert "novel" in lowered or "new" in lowered or "first" in lowered
    # And explicitly notes they are facts, not editorializing
    assert "fact" in lowered or "abstract" in lowered


def test_product_announcement_forbids_opinion():
    tmpl = template_for(NewsletterShape.PRODUCT_ANNOUNCEMENT)
    lowered = tmpl.lower()
    assert "no opinion" in lowered
    assert "no synthesis" in lowered
