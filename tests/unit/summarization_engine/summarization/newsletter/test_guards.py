"""Tests for newsletter deterministic hallucination guards."""
from __future__ import annotations

from website.features.summarization_engine.summarization.newsletter.guards import (
    apply_newsletter_guards,
    find_unverified_numerals,
    strip_banned_adjectives,
)


def test_strip_banned_adjective_basic():
    stripped, removed = strip_banned_adjectives("This groundbreaking research")
    assert "groundbreaking" not in stripped.lower()
    assert removed == ["groundbreaking"]
    assert stripped == "This research"


def test_strip_banned_adjective_case_insensitive():
    stripped, removed = strip_banned_adjectives("An Innovative approach")
    assert "innovative" not in stripped.lower()
    assert removed == ["Innovative"]


def test_strip_banned_adjective_multiple():
    stripped, removed = strip_banned_adjectives(
        "A novel and impressive and robust framework"
    )
    assert set(map(str.lower, removed)) >= {"novel", "impressive", "robust"}
    assert "novel" not in stripped.lower()


def test_strip_banned_adjective_none():
    stripped, removed = strip_banned_adjectives("A neutral description of the topic")
    assert removed == []
    assert stripped == "A neutral description of the topic"


def test_find_unverified_numerals_card_number():
    unverified = find_unverified_numerals(
        "card 4000 0000 0000 9995 was used", "no numbers here"
    )
    # Each digit-group is its own match; all should be flagged
    assert "4000" in unverified
    assert "9995" in unverified


def test_find_unverified_numerals_all_present():
    unverified = find_unverified_numerals(
        "year 2024 saw $5 raised", "Foo raised $5 in 2024"
    )
    assert unverified == []


def test_find_unverified_numerals_separator_tolerant():
    # "$5,000" in summary vs "$5000" in source — normalised match
    unverified = find_unverified_numerals(
        "raised $5,000 total", "raised $5000 total"
    )
    assert unverified == []


def test_apply_newsletter_guards_full_audit():
    text = "This groundbreaking result with card 1234 was shown."
    source = "A neutral result was shown."
    guarded, audit = apply_newsletter_guards(
        summary_text=text, source_text=source
    )
    assert "groundbreaking" not in guarded.lower()
    assert "groundbreaking" in [a.lower() for a in audit["banned_adjectives_stripped"]]
    assert "1234" in audit["unverified_numerals"]
    assert audit["requires_repair"] is True


def test_apply_newsletter_guards_no_repair_needed():
    text = "A neutral description without numbers."
    source = "Some source text."
    guarded, audit = apply_newsletter_guards(
        summary_text=text, source_text=source
    )
    assert audit["unverified_numerals"] == []
    assert audit["requires_repair"] is False
    assert audit["banned_adjectives_stripped"] == []


def test_apply_newsletter_guards_strips_but_no_repair():
    # Banned adjective present but no unverified numerals → no repair needed
    text = "This innovative approach is detailed."
    source = "An approach is detailed."
    guarded, audit = apply_newsletter_guards(
        summary_text=text, source_text=source
    )
    assert "innovative" not in guarded.lower()
    assert audit["requires_repair"] is False
