"""Per-summary confidence grading + HTTP 422 refusal threshold.

Industry pattern: confidence is a first-class top-level response field
(mirrors OpenAI structured-outputs ``refusal``). Three-tier:
  - high        : >=1500 chars AND non-metadata source tier
  - low         : 500-1500 chars OR metadata-only source tier
  - insufficient: <500 chars AND metadata-only source tier  ->  HTTP 422

Threshold metric: chars (not tokens) - pragmatic, no tokenizer cost. Move to
tokens if/when we have evidence the threshold is wrong.
"""
from __future__ import annotations

from typing import Literal

Confidence = Literal["high", "low", "insufficient"]

# Thresholds (chars). Move to env later if tuning needed.
INSUFFICIENT_CHARS = 500
LOW_CHARS = 1500

# Match TierName.METADATA_ONLY.value from
# website.features.summarization_engine.source_ingest.youtube.tiers.
METADATA_ONLY_TIERS = {"metadata_only"}


def grade(raw_text_len: int, source_tier: str) -> tuple[Confidence, str]:
    """Return ``(confidence_label, reason_string)``.

    ``reason_string`` is empty for the ``"high"`` tier so callers can serialize
    ``confidence_reason`` as ``None`` when there's nothing to report.
    """
    metadata_only = source_tier in METADATA_ONLY_TIERS
    if raw_text_len < INSUFFICIENT_CHARS and metadata_only:
        return (
            "insufficient",
            f"source had {raw_text_len} chars (<{INSUFFICIENT_CHARS}) "
            f"and only metadata available (tier={source_tier})",
        )
    if raw_text_len < LOW_CHARS or metadata_only:
        return (
            "low",
            f"source had {raw_text_len} chars and tier={source_tier}; "
            f"summary may be incomplete",
        )
    return ("high", "")
