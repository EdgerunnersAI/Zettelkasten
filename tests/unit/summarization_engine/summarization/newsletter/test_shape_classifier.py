"""Tests for the 3-tier newsletter shape classifier."""
from __future__ import annotations

from website.features.summarization_engine.source_ingest.newsletter.shape_classifier import (
    CONFIDENCE_THRESHOLD,
    classify,
    classify_tier1,
)
from website.features.summarization_engine.summarization.newsletter.shapes import (
    NO_STANCE_PENALTY,
    NewsletterShape,
)


def _many_words(n: int) -> str:
    return " ".join(["word"] * n)


def test_link_digest_high_link_density():
    md = (
        _many_words(50)
        + "\n"
        + "\n".join(f"- [item{i}](http://x.com/{i}) blurb" for i in range(20))
    )
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.LINK_DIGEST


def test_academic_roundup_many_headers_low_links():
    body = "\n\n".join(
        [f"## Paper {i}\n\n" + _many_words(100) for i in range(8)]
    )
    cls = classify_tier1(body, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.confidence >= 0.65


def test_product_announcement_title_short_body():
    md = _many_words(200)
    cls = classify_tier1(md, "Introducing our new feature")
    assert cls is not None
    assert cls.shape == NewsletterShape.PRODUCT_ANNOUNCEMENT


def test_personal_essay_high_pronoun_density():
    # Many first-person pronouns relative to sentence count
    md = "I think my journey matters. I felt I learned myself. I'm sure I've grown. My view changed."
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.PERSONAL_ESSAY


def test_deep_dive_long_form_few_headers():
    md = _many_words(4000) + "\n\n## Single Section\n\n" + _many_words(50)
    cls = classify_tier1(md, "")
    assert cls is not None
    assert cls.shape == NewsletterShape.DEEP_DIVE_TECHNICAL


def test_ambiguous_falls_to_tier3_reject():
    # Short ambiguous markdown -> tier1 None -> classify() returns commentary_essay
    md = "Just a couple of plain sentences. Nothing notable here."
    assert classify_tier1(md, "") is None
    result = classify(md, title="")
    assert result.method == "tier3_reject"
    assert result.shape == NewsletterShape.COMMENTARY_ESSAY


def test_organicsynthesis_style_academic_roundup():
    # Simulate organicsynthesis: many H2/H3 headers, paper-by-paper, low link density
    body_parts = []
    for i in range(7):
        body_parts.append(
            f"## Paper {i}: Synthesis of compound X{i}\n\n"
            + _many_words(80)
            + f"\n\n### Method {i}\n\n"
            + _many_words(60)
        )
    md = "\n\n".join(body_parts)
    cls = classify(md, title="Weekly Organic Synthesis Roundup")
    assert cls.shape == NewsletterShape.ACADEMIC_ROUNDUP
    assert cls.confidence >= CONFIDENCE_THRESHOLD


def test_no_stance_penalty_contains_expected_shapes():
    assert NO_STANCE_PENALTY == frozenset({
        NewsletterShape.ACADEMIC_ROUNDUP,
        NewsletterShape.LINK_DIGEST,
        NewsletterShape.NEWS_AGGREGATOR,
        NewsletterShape.PRODUCT_ANNOUNCEMENT,
    })
