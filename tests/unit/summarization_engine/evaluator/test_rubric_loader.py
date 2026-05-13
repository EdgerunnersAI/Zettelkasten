from pathlib import Path

import pytest

from website.features.summarization_engine.evaluator.rubric_loader import (
    RubricSchemaError,
    load_rubric,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
NEWSLETTER_RUBRIC = (
    REPO_ROOT / "docs" / "summary_eval" / "_config" / "rubric_newsletter.yaml"
)


def test_load_rubric_rejects_missing_version(tmp_path: Path):
    bad = tmp_path / "rubric_youtube.yaml"
    bad.write_text(
        "source_type: youtube\ncomposite_max_points: 100\ncomponents: []\n",
        encoding="utf-8",
    )

    with pytest.raises(RubricSchemaError):
        load_rubric(bad)


def _newsletter_criterion(criterion_id: str) -> dict:
    data = load_rubric(NEWSLETTER_RUBRIC)
    brief = next(c for c in data["components"] if c["id"] == "brief_summary")
    return next(c for c in brief["criteria"] if c["id"] == criterion_id)


def test_newsletter_caveats_addressed_reweighted_to_5():
    """W8 reweight: caveat-compression is dominant brief defect (iter-09/11)."""
    assert _newsletter_criterion("brief.caveats_addressed")["max_points"] == 5


def test_newsletter_caveats_addressed_maps_to_completeness_and_faithfulness():
    metrics = _newsletter_criterion("brief.caveats_addressed")["maps_to_metric"]
    assert "finesure.faithfulness" in metrics
    assert "finesure.completeness" in metrics


def test_newsletter_stance_preserved_retired():
    """Coverage moved to editorialization_penalty + W4 banned-adjective strip."""
    crit = _newsletter_criterion("brief.stance_preserved")
    assert crit["max_points"] == 0


def test_newsletter_brief_summary_total_unchanged():
    data = load_rubric(NEWSLETTER_RUBRIC)
    brief = next(c for c in data["components"] if c["id"] == "brief_summary")
    criterion_sum = sum(c["max_points"] for c in brief["criteria"])
    assert brief["max_points"] == 25
    assert criterion_sum == 25


def test_newsletter_composite_total_unchanged():
    data = load_rubric(NEWSLETTER_RUBRIC)
    component_sum = sum(c["max_points"] for c in data["components"])
    assert data["composite_max_points"] == 100
    assert component_sum == 100
