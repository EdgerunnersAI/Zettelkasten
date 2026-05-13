"""3-tier newsletter shape classifier.

Tier 1 - deterministic DOM/markdown heuristics (0 LLM calls).
Tier 2 - small-LLM tie-breaker when Tier-1 confidence < threshold.
Tier 3 - confidence-reject (return GENERAL + low_confidence flag).

Industry pattern: SELECT-THEN-ROUTE EMNLP 2025 + Refuel labeling-with-confidence.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from website.features.summarization_engine.summarization.newsletter.shapes import (
    NewsletterShape,
)

CONFIDENCE_THRESHOLD = 0.65


@dataclass
class ShapeClassification:
    shape: NewsletterShape
    confidence: float
    method: str  # "tier1_heuristic" | "tier2_llm" | "tier3_reject"
    signals: dict  # for debugging/telemetry


_PRODUCT_TITLE = re.compile(
    r"\b(introducing|we're launching|what's new|now available|announcing|"
    r"shipping|today we|new feature|early access|product update)\b",
    re.I,
)
_PERSONAL_PRONOUNS = re.compile(r"\b(I|my|me|myself|I've|I'm)\b")
_HEADER_PATTERN = re.compile(r"^#{2,3}\s+\S", re.MULTILINE)


def classify_tier1(markdown: str, title: str = "") -> ShapeClassification | None:
    """Cheap deterministic heuristics; returns None if no high-confidence match."""
    if not markdown:
        return None

    links = len(re.findall(r"\[.*?\]\(.*?\)", markdown))
    words = max(1, len(markdown.split()))
    link_density = links / words
    list_items = len(re.findall(r"^\s*[-*]\s", markdown, re.MULTILINE))
    body_lines = max(1, len([line for line in markdown.splitlines() if line.strip()]))
    list_ratio = list_items / body_lines
    headers = len(_HEADER_PATTERN.findall(markdown))
    pronoun_count = len(_PERSONAL_PRONOUNS.findall(markdown))
    pronoun_per_sentence = pronoun_count / max(1, markdown.count(".") + markdown.count("?"))

    signals = {
        "link_density": round(link_density, 4),
        "list_ratio": round(list_ratio, 4),
        "headers": headers,
        "pronoun_per_sentence": round(pronoun_per_sentence, 4),
        "words": words,
    }

    # Product announcement: title pattern + low body length
    if title and _PRODUCT_TITLE.search(title) and words < 1500:
        return ShapeClassification(
            NewsletterShape.PRODUCT_ANNOUNCEMENT, 0.85, "tier1_heuristic", signals,
        )

    # Link digest: very high link density OR list_ratio
    if link_density > 0.08 or list_ratio > 0.7:
        return ShapeClassification(
            NewsletterShape.LINK_DIGEST, 0.80, "tier1_heuristic", signals,
        )

    # Academic roundup: >=5 H2/H3 headers with similar pattern + low link density
    if headers >= 5 and link_density < 0.03 and list_ratio < 0.3:
        return ShapeClassification(
            NewsletterShape.ACADEMIC_ROUNDUP, 0.75, "tier1_heuristic", signals,
        )

    # News aggregator: many headers + medium link density (each headline has link)
    if headers >= 5 and link_density >= 0.03 and link_density < 0.08:
        return ShapeClassification(
            NewsletterShape.NEWS_AGGREGATOR, 0.75, "tier1_heuristic", signals,
        )

    # Personal essay: high first-person pronoun density
    if pronoun_per_sentence > 0.4:
        return ShapeClassification(
            NewsletterShape.PERSONAL_ESSAY, 0.70, "tier1_heuristic", signals,
        )

    # Deep dive: long-form (>3000 words) with few headers (<5)
    if words > 3000 and headers < 5:
        return ShapeClassification(
            NewsletterShape.DEEP_DIVE_TECHNICAL, 0.65, "tier1_heuristic", signals,
        )

    # No high-confidence match
    return None


def classify(
    markdown: str,
    *,
    title: str = "",
    llm_classifier=None,  # callable(markdown, title) -> (shape, confidence) | None
) -> ShapeClassification:
    """Run 3-tier classification. Returns ShapeClassification (never None)."""
    tier1 = classify_tier1(markdown, title)
    if tier1 and tier1.confidence >= CONFIDENCE_THRESHOLD:
        return tier1

    if llm_classifier is not None:
        try:
            result = llm_classifier(markdown, title)
            if result:
                shape, conf = result
                if conf >= CONFIDENCE_THRESHOLD:
                    return ShapeClassification(
                        shape, conf, "tier2_llm",
                        {"tier1_signals": tier1.signals if tier1 else {}},
                    )
        except Exception:
            pass  # Fall through to general

    # Tier-3 reject: default to commentary_essay (current behavior preserved)
    # rather than GENERAL to avoid breaking existing happy-path summaries.
    fallback_shape = (
        tier1.shape if tier1 else NewsletterShape.COMMENTARY_ESSAY
    )
    fallback_conf = tier1.confidence if tier1 else 0.50
    return ShapeClassification(
        fallback_shape, fallback_conf, "tier3_reject",
        {"tier1_signals": tier1.signals if tier1 else {}},
    )
