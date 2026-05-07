"""iter-12 Phase 4 / Task 8: Q3 gate-skip path tests.

Root cause: citation-validation block (orchestrator.py ~L924-929) substitutes
REFUSAL_PHRASE + clears used_candidates when has_valid_citation fails. By the
time unsupported_with_gold_skip fires (~L991), answer_text is already
REFUSAL_PHRASE; iter-09 branch blindly appended _GOLD_RETRIEVED_DETAILS_TAG,
masking a refusal as a confident "reflects retrieved sources" answer.

Fix: _decide_gold_skip_action() gates the gold-skip tag on both conditions:
  1. answer_text is NOT REFUSAL_PHRASE, AND
  2. raw_content (the original generation) has a valid [id=...] citation.
Falls through to honest low-confidence labeling otherwise.
"""
import pytest

from website.features.rag_pipeline.orchestrator import (
    _decide_gold_skip_action,
    REFUSAL_PHRASE,
)


def _cited_content() -> str:
    return 'answer about verbal punctuation [id="yt-effective-public-speakin"] more text'


def _uncited_content() -> str:
    return "Short answer with no citation tag whatsoever."


_VALID_IDS = {"yt-effective-public-speakin"}


# ---------------------------------------------------------------------------
# Path 1: synth produced a real cited draft — honor the iter-09 gold-skip
# ---------------------------------------------------------------------------

def test_valid_citation_draft_keeps_gold_tag():
    """Path 1 (positive): draft is NOT refused and HAS a valid [id=...] tag.
    Must return verdict='unsupported_with_gold_skip', apply_gold_tag=True."""
    raw = _cited_content()
    decision = _decide_gold_skip_action(
        answer_text=raw,
        raw_content=raw,
        valid_ids=_VALID_IDS,
    )
    assert decision.verdict == "unsupported_with_gold_skip"
    assert decision.apply_gold_tag is True


# ---------------------------------------------------------------------------
# Path 2: citation-validation upstream substituted answer_text with REFUSAL_PHRASE
# ---------------------------------------------------------------------------

def test_refused_answer_text_falls_through_to_low_conf():
    """Path 2 (q3 fix): answer_text == REFUSAL_PHRASE (upstream substituted it)
    but raw_content has the citation. Must NOT apply gold tag — honest low-conf."""
    raw = _cited_content()
    decision = _decide_gold_skip_action(
        answer_text=REFUSAL_PHRASE,
        raw_content=raw,
        valid_ids=_VALID_IDS,
    )
    assert decision.verdict == "unsupported_no_retry"
    assert decision.apply_gold_tag is False


# ---------------------------------------------------------------------------
# Path 3: synth produced text but no valid [id=...] tag — fall through
# ---------------------------------------------------------------------------

def test_no_citation_in_raw_falls_through_to_low_conf():
    """Path 3 (q3 fix): answer is not a refusal but lacks any valid citation.
    Must fall through to unsupported_no_retry with no gold tag."""
    raw = _uncited_content()
    decision = _decide_gold_skip_action(
        answer_text=raw,
        raw_content=raw,
        valid_ids=_VALID_IDS,
    )
    assert decision.verdict == "unsupported_no_retry"
    assert decision.apply_gold_tag is False
