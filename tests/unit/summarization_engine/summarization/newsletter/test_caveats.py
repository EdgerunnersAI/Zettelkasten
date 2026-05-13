"""Tests for caveats as a first-class field in newsletter detailed_summary
plus its conditioning on brief generation.

Iter-11 held-out eval showed `brief.caveats_addressed` MISSED on the
pragmaticengineer URL (-3 to -5 pts). Iter-09 manual_review flagged
"occasional over-condensation: beehiiv MCP and Email Boosts compress
caveats and constraints" — known issue addressed here.
"""
from __future__ import annotations

from website.features.summarization_engine.summarization.newsletter.prompts import (
    STRUCTURED_EXTRACT_INSTRUCTION,
)
from website.features.summarization_engine.summarization.newsletter.schema import (
    NewsletterDetailedPayload,
    NewsletterSection,
    NewsletterStructuredPayload,
)


def _make_detailed(**overrides) -> NewsletterDetailedPayload:
    base = dict(
        publication_identity="Pragmatic Engineer",
        issue_thesis="Thesis",
        sections=[NewsletterSection(heading="Intro", bullets=["A bullet."])],
        conclusions_or_recommendations=[],
        stance="neutral",
        cta=None,
    )
    base.update(overrides)
    return NewsletterDetailedPayload(**base)


def test_schema_accepts_caveats_list_default_empty() -> None:
    """Schema must accept caveats: list[str] and default to []."""
    detailed = _make_detailed()
    assert detailed.caveats == []
    assert isinstance(detailed.caveats, list)


def test_schema_accepts_caveats_with_entries() -> None:
    """Schema must round-trip non-empty caveats."""
    caveats = [
        "I should note this is unpaid.",
        "Disclosure: no affiliate relationship.",
    ]
    detailed = _make_detailed(caveats=caveats)
    assert detailed.caveats == caveats


def test_extraction_prompt_contains_caveats_instruction() -> None:
    """Prompt must instruct the model to extract caveats with marker list."""
    prompt = STRUCTURED_EXTRACT_INSTRUCTION
    assert '"caveats"' in prompt
    # marker list
    assert "I should note" in prompt
    assert "disclosure:" in prompt.lower()
    assert "no affiliate" in prompt
    assert "however" in prompt
    assert "that said" in prompt
    # bounds
    assert "0-5" in prompt or "0 to 5" in prompt


def test_brief_is_conditioned_on_caveats() -> None:
    """Prompt must tell the model that brief MUST acknowledge caveats when present."""
    prompt = STRUCTURED_EXTRACT_INSTRUCTION
    lower = prompt.lower()
    assert "brief_summary must acknowledge" in lower or "brief_summary must" in lower
    assert "caveat" in lower
    # the inverse rule must also be present so an empty list does not force invention
    assert "if detailed_summary.caveats is empty" in lower
    assert "do not invent" in lower


def test_full_structured_payload_round_trips_caveats() -> None:
    """End-to-end: a payload with an 'unpaid' caveat round-trips through
    NewsletterStructuredPayload preserving the caveat verbatim."""
    payload = NewsletterStructuredPayload(
        mini_title="Pragmatic Engineer: Thesis",
        brief_summary=(
            "Sentence one with context. Sentence two with detail. Sentence three "
            "for substance. Sentence four for closure (note: this issue is unpaid)."
        ),
        tags=[
            "newsletter",
            "engineering",
            "analysis",
            "essay",
            "career",
            "opinion",
            "industry",
        ],
        detailed_summary=_make_detailed(
            caveats=["I should note this is unpaid."],
        ),
    )
    assert payload.detailed_summary.caveats == ["I should note this is unpaid."]
    # brief acknowledges the caveat (the test simulates a well-conditioned model)
    assert "unpaid" in payload.brief_summary.lower()
