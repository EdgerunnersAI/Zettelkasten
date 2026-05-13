"""Per-shape newsletter prompt templates.

Each shape maps to a structured-extract instruction variant that:
1. Adjusts the slot list (thesis vs paper-list vs feature-list vs link-list)
2. Adjusts forbidden patterns (e.g. don't penalize evaluative-sounding sentences
   in academic_roundup since "novel synthesis" is THE FACT of the paper)
"""
from __future__ import annotations
from website.features.summarization_engine.summarization.newsletter.shapes import (
    NewsletterShape,
)

# Each template returns the structured-extract instruction tail; the system
# prompt (TONE CONSTRAINTS from W4) is prepended by the caller.

_COMMENTARY = """
Structure: thesis statement -> argument structure (3-5 points) -> key evidence
per point -> conclusions -> caveats / counter-arguments. Stance constraint:
preserve author's evaluative voice (commentary expects opinion).
"""

_ACADEMIC = """
Structure: per-paper enumeration. For each paper: title, authors (if given),
method/technique, key result. NO synthesis, NO thesis. Tone: descriptive only -
the paper-summary sentences MAY use "novel"/"new"/"first" when those words
appear in the abstract being summarized (they are facts of the paper, not your
editorializing).
"""

_LINK_DIGEST = """
Structure: per-item bullet with 1-sentence "what + why interesting" summary.
Preserve link order. NO synthesis, NO thesis. Allow numerical claims to flow
through verbatim from source.
"""

_NEWS_AGGREGATOR = """
Structure: top-N headlines (max 8), each with a 1-line factual blurb. Optional
"today's theme" only if explicitly stated in source. NO opinion, NO synthesis.
"""

_DEEP_DIVE = """
Structure: problem / approach / key technical insights (3-7) / caveats /
who-should-care. Treat heavily - this is commentary-essay's longer cousin.
"""

_PERSONAL_ESSAY = """
Structure: core reflection / 2-3 anchor moments / takeaway. Tolerate
first-person voice. Don't force thesis structure - personal essays may
spiral around an emotion rather than argue a point.
"""

_PRODUCT_ANNOUNCEMENT = """
Structure: what shipped (1 sentence) / who it's for / key features (<=5) / CTA.
NO opinion, NO synthesis. This shape often emits LOW signal - flag with
quality_signals.shape_value=low.
"""

_GENERAL = """
Structure: thesis + argument + evidence + caveats (commentary fallback).
"""

_TEMPLATES: dict[NewsletterShape, str] = {
    NewsletterShape.COMMENTARY_ESSAY: _COMMENTARY,
    NewsletterShape.ACADEMIC_ROUNDUP: _ACADEMIC,
    NewsletterShape.LINK_DIGEST: _LINK_DIGEST,
    NewsletterShape.NEWS_AGGREGATOR: _NEWS_AGGREGATOR,
    NewsletterShape.DEEP_DIVE_TECHNICAL: _DEEP_DIVE,
    NewsletterShape.PERSONAL_ESSAY: _PERSONAL_ESSAY,
    NewsletterShape.PRODUCT_ANNOUNCEMENT: _PRODUCT_ANNOUNCEMENT,
    NewsletterShape.GENERAL: _GENERAL,
}


def template_for(shape: NewsletterShape) -> str:
    return _TEMPLATES.get(shape, _GENERAL)
