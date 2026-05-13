"""Deterministic hallucination guards for newsletter summaries.

- BANNED_ADJ: evaluative adjectives that the LLM injects but rarely exist in
  source (per iter-11 audit). Stripped silently.
- NUMERAL: numeric tokens in summary must appear verbatim or normalised in
  source — flag misses for conditional Flash repair.

Industry pattern: Amazon Bedrock contextual-grounding-check >75% catch rate
on this exact failure mode. Zero LLM calls — runs after structured-extract.
"""
from __future__ import annotations

import re

# Adjectives flagged in iter-11 + common evaluative leakage
BANNED_ADJ = re.compile(
    r"\b("
    r"groundbreaking|innovative|significant|novel|impressive|"
    r"challenging|advanced|cutting-edge|remarkable|robust|"
    r"comprehensive|powerful|seminal|sophisticated|elegant|"
    r"essential|invaluable|crucial|paramount|profound"
    r")\b",
    re.I,
)

# Match standalone numerals (digits, decimals, separators, hyphens)
# Catches: card numbers, dates, prices, percentages, version numbers
NUMERAL = re.compile(r"\b\d[\d,./\-]*\b")

# Number-word fallback (rare — usually digits suffice for our case)
NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}


def strip_banned_adjectives(text: str) -> tuple[str, list[str]]:
    """Remove evaluative adjectives silently. Returns (stripped, list_of_removed)."""
    removed = BANNED_ADJ.findall(text)
    stripped = BANNED_ADJ.sub("", text)
    # Collapse repeated spaces left behind
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return stripped, removed


def _normalise_numeral(n: str) -> str:
    """Strip separators for verbatim-match tolerance ($5M vs $5,000,000 left for future)."""
    return n.replace(",", "").replace(" ", "").strip(".-/")


def find_unverified_numerals(summary: str, source: str) -> list[str]:
    """Return numerals in summary that don't appear (normalised) in source."""
    src_nums = {_normalise_numeral(n) for n in NUMERAL.findall(source)}
    src_nums.discard("")
    unverified = []
    for n in NUMERAL.findall(summary):
        norm = _normalise_numeral(n)
        if norm and norm not in src_nums:
            unverified.append(n)
    # Dedupe preserving order
    seen: set[str] = set()
    return [n for n in unverified if not (n in seen or seen.add(n))]


def apply_newsletter_guards(
    *,
    summary_text: str,
    source_text: str,
) -> tuple[str, dict]:
    """Run all guards. Returns (guarded_summary, audit_dict).

    audit_dict contains:
      - banned_adjectives_stripped: list[str]
      - unverified_numerals: list[str]
      - requires_repair: bool (true if any unverified numerals — caller
        decides whether to invoke conditional Flash repair)
    """
    stripped, removed_adj = strip_banned_adjectives(summary_text)
    unverified = find_unverified_numerals(stripped, source_text)
    return stripped, {
        "banned_adjectives_stripped": removed_adj,
        "unverified_numerals": unverified,
        "requires_repair": bool(unverified),
    }
