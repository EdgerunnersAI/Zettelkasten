"""SE-10: evaluator determinism.

Three deterministic surfaces in the evaluator subpackage:

  * ``rubric_loader.load_rubric`` — schema validation: required keys
    present, ``components`` max_points sum to ``composite_max_points``.
    A regression flipping the sum check would silently mis-score every
    summary; pin it.
  * ``numeric_grounding.extract_numeric_tokens`` — pattern set:
    ``$N`` / ``N%`` / ISO date / year / bare integer (default 3-digit
    floor). Output order: pattern-then-input, deduplicated. Pin a golden
    snapshot.
  * ``numeric_grounding.numeric_validator`` — ratio + ungrounded list.
    Anchor the contract: ``ratio == 1.0`` when there are no tokens (vacuous
    grounding) and falsy threshold breaches return ``grounded=False``.

These tests load the live rubric YAMLs in
``docs/summary_eval/_config/`` and the production numeric_grounding code,
so a drift in EITHER will be caught here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from website.features.summarization_engine.evaluator.numeric_grounding import (
    extract_numeric_tokens,
    ground_numeric_claims,
    numeric_validator,
)
from website.features.summarization_engine.evaluator.rubric_loader import (
    RubricSchemaError,
    load_rubric,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUBRIC_DIR = _REPO_ROOT / "docs" / "summary_eval" / "_config"

_RUBRIC_FILES = [
    "rubric_universal.yaml",
    "rubric_youtube.yaml",
    "rubric_github.yaml",
    "rubric_newsletter.yaml",
    "rubric_reddit.yaml",
]


# --- rubric_loader: schema is enforced + max_points sum to total -----------


@pytest.mark.parametrize("filename", _RUBRIC_FILES)
def test_all_rubric_files_load_and_validate(filename: str) -> None:
    """Every shipped rubric must (a) parse, (b) carry the required keys,
    (c) have component max_points summing to composite_max_points (the
    rubric_loader invariant)."""
    path = _RUBRIC_DIR / filename
    if not path.exists():
        pytest.skip(f"rubric file missing: {path}")
    data = load_rubric(path)
    assert data["composite_max_points"] == 100, (
        f"{filename}: composite_max_points must be 100, got "
        f"{data['composite_max_points']}"
    )
    component_sum = sum(c.get("max_points", 0) for c in data["components"])
    assert component_sum == data["composite_max_points"], (
        f"{filename}: component sum {component_sum} != composite "
        f"{data['composite_max_points']}"
    )


def test_rubric_loader_rejects_missing_required_keys(tmp_path) -> None:
    """A rubric missing ``version`` must raise ``RubricSchemaError`` — not
    silently default. The eval harness assumes all required fields are
    present and would index-error or compute garbage scores otherwise."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source_type: x\n"
        "composite_max_points: 100\n"
        "components: []\n",
        encoding="utf-8",
    )
    with pytest.raises(RubricSchemaError, match="missing required keys"):
        load_rubric(bad)


def test_rubric_loader_rejects_max_points_mismatch(tmp_path) -> None:
    """Component max_points summing to 90 with composite=100 must fail."""
    bad = tmp_path / "mismatch.yaml"
    bad.write_text(
        "version: v1\n"
        "source_type: x\n"
        "composite_max_points: 100\n"
        "components:\n"
        "  - id: a\n"
        "    max_points: 50\n"
        "  - id: b\n"
        "    max_points: 40\n",
        encoding="utf-8",
    )
    with pytest.raises(RubricSchemaError, match="composite_max_points"):
        load_rubric(bad)


# --- numeric_grounding: extract_numeric_tokens golden ---------------------


def test_extract_numeric_tokens_default_floor_three_digits() -> None:
    """The default 3-digit floor for bare integers ignores small counts
    (e.g., '50 offices') but catches '1500 employees', dates, currency,
    and percents. Pin the exact set + order."""
    text = (
        "Revenue grew 35% to $1,200 in 2024, "
        "with 50 offices and 1500 employees. "
        "Released on 2024-03-15."
    )
    tokens = extract_numeric_tokens(text)
    # Order: $, %, ISO date, year, then bare-int (>=3 digits) input order.
    # Note: bare-int pattern matches '200' inside '$1,200' (regex doesn't
    # see the dollar sign as part of the number) — that's the documented
    # behaviour and the dedupe keys on string identity, not span overlap.
    assert tokens == [
        "$1,200",
        "35%",
        "2024-03-15",
        "2024",
        "200",
        "1500",
    ], f"golden mismatch: {tokens}"


def test_extract_numeric_tokens_strict_one_digit_floor() -> None:
    """Newsletter intra-summarizer mode: 1-digit floor catches every bare
    integer including '50'. Order MUST be deterministic across runs."""
    text = "There are 5 reasons and 50 offices in 2024."
    tokens = extract_numeric_tokens(text, min_bare_integer_digits=1)
    # Year regex matches before bare-int, so 2024 lands first.
    assert tokens == ["2024", "5", "50"], tokens


def test_extract_numeric_tokens_dedupe_preserves_first_seen() -> None:
    """Dedupe is by string identity — '$100' and '100' (the bare int inside
    it) are distinct tokens. Repeated '$100' / '25%' collapse, the bare
    '100' from '$100' shows up once."""
    text = "$100 then $100 again and 25% later 25%"
    tokens = extract_numeric_tokens(text)
    assert tokens == ["$100", "25%", "100"]


def test_extract_numeric_tokens_empty() -> None:
    assert extract_numeric_tokens("") == []
    assert extract_numeric_tokens("no numbers here at all") == []


# --- numeric_grounding: validator contract -------------------------------


def test_numeric_validator_no_tokens_is_vacuously_grounded() -> None:
    """No numeric claims = nothing to ground = ratio 1.0. The eval harness
    treats this as a pass; a regression dropping it to 0.0 would penalise
    every text-only summary."""
    out = numeric_validator(summary="prose with no numbers", source="anything")
    assert out == {"grounded": True, "ungrounded": [], "ratio": 1.0}


def test_numeric_validator_full_grounding() -> None:
    summary = "In 2024 revenue was $1,200 (35%)."
    source = "Source mentions 2024, $1,200, and 35% growth."
    out = numeric_validator(summary=summary, source=source)
    assert out["grounded"] is True
    assert out["ungrounded"] == []
    assert out["ratio"] == 1.0


def test_numeric_validator_partial_grounding_under_threshold() -> None:
    """A summary asserting numbers not in source returns
    ``grounded=False`` and lists the offenders. Ungrounded list MUST be
    deterministic order-of-appearance."""
    summary = "Revenue $500 grew to $1,200 in 2024."
    source = "Revenue grew to $1,200."
    out = numeric_validator(summary=summary, source=source, threshold=1.0)
    assert out["grounded"] is False
    # Tokens extracted: ['$500', '$1,200', '2024', '500', '200'].
    # Source contains '$1,200' and the bare '200'. Ungrounded:
    # $500, 2024, 500.
    assert out["ungrounded"] == ["$500", "2024", "500"]
    # 2 of 5 grounded ('$1,200' and '200').
    assert out["ratio"] == pytest.approx(2 / 5, rel=1e-6)


def test_ground_numeric_claims_returns_tuple_shape() -> None:
    """Companion API ``ground_numeric_claims`` returns
    ``(all_grounded, ungrounded_tokens)`` — pin the tuple shape so callers
    that destructure it can't break under refactor."""
    res = ground_numeric_claims(
        summary="$100 and 25%", source="just $100"
    )
    assert isinstance(res, tuple) and len(res) == 2
    all_grounded, ungrounded = res
    assert all_grounded is False
    assert ungrounded == ["25%"]


# --- determinism across re-invocation ------------------------------------


def test_extract_numeric_tokens_idempotent() -> None:
    """Running the extractor twice on identical input MUST produce the
    same list (no shared mutable state, no rng)."""
    text = "$1,500 in 2024 (15%) covering 250 hosts on 2024-06-01"
    a = extract_numeric_tokens(text)
    b = extract_numeric_tokens(text)
    assert a == b
