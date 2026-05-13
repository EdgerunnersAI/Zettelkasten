"""Newsletter content-shape taxonomy.

Per max-coverage research 2026-05-13: end-users paste any newsletter; current
pipeline assumes commentary-essay and penalizes academic-roundup / link-digest
/ news-aggregator / product-announcement as "evaluative drift" when those
shapes are LEGITIMATELY enumerative or feature-list-shaped.
"""
from __future__ import annotations
from enum import StrEnum


class NewsletterShape(StrEnum):
    COMMENTARY_ESSAY = "commentary_essay"           # thesis + evidence + caveats
    ACADEMIC_ROUNDUP = "academic_roundup"           # paper-by-paper enumeration
    LINK_DIGEST = "link_digest"                     # bullet-with-link enumeration
    NEWS_AGGREGATOR = "news_aggregator"             # multiple headlines + blurbs
    DEEP_DIVE_TECHNICAL = "deep_dive_technical"     # long-form single-topic
    PERSONAL_ESSAY = "personal_essay"               # first-person reflective
    PRODUCT_ANNOUNCEMENT = "product_announcement"   # feature list + CTA
    GENERAL = "general"                             # fallback


# Stance-penalty mask — shapes for which the source-stance heuristic
# should be DISABLED (these shapes legitimately use evaluative-looking
# language that's not editorialization).
NO_STANCE_PENALTY: frozenset[NewsletterShape] = frozenset({
    NewsletterShape.ACADEMIC_ROUNDUP,
    NewsletterShape.LINK_DIGEST,
    NewsletterShape.NEWS_AGGREGATOR,
    NewsletterShape.PRODUCT_ANNOUNCEMENT,
})
