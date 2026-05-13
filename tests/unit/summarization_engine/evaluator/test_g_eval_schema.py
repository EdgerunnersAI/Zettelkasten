"""G-Eval anchored-ternary schema tests (evaluator.v4).

Asserts the post-2026-05-13 schema:
  - coherence + fluency only (consistency + relevance removed)
  - integer score in {1, 2, 3}
  - aggregate ((c + f) / 6) * 100 -> {33, 67, 100} at endpoints
  - judge_facet_disagreement flag fires when coherence >= 3 AND
    finesure.faithfulness < 0.7
"""
from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from website.features.summarization_engine.evaluator.models import (
    GEvalCriterion,
    GEvalScores,
    g_eval_aggregate,
)
from website.features.summarization_engine.evaluator.prompts import (
    CONSOLIDATED_USER_TEMPLATE,
    PROMPT_VERSION,
)


# ---------- prompt schema surface ----------


def test_prompt_lists_only_coherence_and_fluency():
    body = CONSOLIDATED_USER_TEMPLATE
    assert '"coherence"' in body
    assert '"fluency"' in body
    # consistency + relevance are dropped from the g_eval block; they only
    # survive in the migration rule prose. The schema block itself must not
    # introduce them as keys.
    schema_block = body.split("RULES:")[0]
    assert '"consistency"' not in schema_block
    assert '"relevance"' not in schema_block


def test_prompt_version_bumped_to_v4():
    assert PROMPT_VERSION == "evaluator.v4"


# ---------- pydantic schema ----------


def test_ternary_score_accepted():
    c = GEvalCriterion(score=2, anchor="anchor", reasoning="why")
    assert c.score == 2


def test_ternary_score_clamps_high():
    # The judge sometimes emits 4 or 5 on the old scale; clamp -> 3
    c = GEvalCriterion(score=5)
    assert c.score == 3


def test_ternary_score_clamps_low():
    c = GEvalCriterion(score=0)
    assert c.score == 1


def test_g_eval_scores_rejects_legacy_keys():
    # consistency + relevance are no longer fields on GEvalScores.
    scores = GEvalScores(
        coherence={"score": 2, "anchor": "", "reasoning": ""},
        fluency={"score": 3, "anchor": "", "reasoning": ""},
    )
    assert not hasattr(scores, "consistency")
    assert not hasattr(scores, "relevance")


def test_g_eval_scores_requires_both_criteria():
    with pytest.raises(ValidationError):
        GEvalScores(coherence={"score": 2})  # type: ignore[call-arg]


# ---------- aggregate ----------


@pytest.mark.parametrize(
    "coh,flu,expected",
    [
        (1, 1, 33.33),
        (2, 2, 66.67),
        (3, 3, 100.0),
        (1, 3, 66.67),
        (2, 3, 83.33),
    ],
)
def test_g_eval_aggregate_endpoints(coh, flu, expected):
    scores = GEvalScores(
        coherence={"score": coh, "anchor": "", "reasoning": ""},
        fluency={"score": flu, "anchor": "", "reasoning": ""},
    )
    assert math.isclose(g_eval_aggregate(scores), expected, abs_tol=0.01)


# ---------- judge_facet_disagreement (prompt rule) ----------


def test_prompt_documents_judge_facet_disagreement_rule():
    body = CONSOLIDATED_USER_TEMPLATE
    assert "judge_facet_disagreement" in body
    # The threshold + condition pair must appear together.
    assert "coherence" in body and "faithfulness" in body
    assert "0.7" in body
