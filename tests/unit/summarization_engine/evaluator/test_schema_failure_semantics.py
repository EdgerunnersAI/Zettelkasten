"""CF-1 (R1): schema_failure must only fire on malformed JSON or missing body
fields (mini_title / brief_summary / detailed_summary). Missing ingest-side
metadata (author, issue_date) is surfaced as advisory `metadata_partial` and
MUST NOT cap the composite.

These tests pin the contract enforced by:
  - evaluator/prompts.py SCHEMA-FAILURE RULE + METADATA-PARTIAL RULE
  - evaluator/models.py EvalResult.metadata_partial + composite_score path
"""
import json

import pytest
from pydantic import ValidationError

from website.features.summarization_engine.evaluator.models import (
    AntiPatternTrigger,
    EvalResult,
    FineSurEDimension,
    FineSurEScores,
    GEvalScores,
    RubricBreakdown,
    RubricComponent,
    SummaCLite,
    composite_score,
)
from website.features.summarization_engine.evaluator import prompts


SCHEMA_FAILURE_CAP = 60


def _build_result(
    *,
    component_scores,
    caps_applied=None,
    anti_patterns=None,
    metadata_partial=False,
    missing_meta=None,
    faithfulness=1.0,
    completeness=1.0,
):
    return EvalResult(
        g_eval=GEvalScores(
            coherence={"score": 3, "anchor": "", "reasoning": ""},
            fluency={"score": 3, "anchor": "", "reasoning": ""},
        ),
        finesure=FineSurEScores(
            faithfulness=FineSurEDimension(score=faithfulness, items=[]),
            completeness=FineSurEDimension(score=completeness, items=[]),
            conciseness=FineSurEDimension(score=1.0, items=[]),
        ),
        summac_lite=SummaCLite(score=1.0, contradicted_sentences=[], neutral_sentences=[]),
        rubric=RubricBreakdown(
            components=[
                RubricComponent(
                    id=cid,
                    score=score,
                    max_points=max_points,
                    criteria_fired=[],
                    criteria_missed=list(missed or []),
                )
                for cid, score, max_points, missed in component_scores
            ],
            caps_applied=caps_applied or {
                "hallucination_cap": None,
                "omission_cap": None,
                "generic_cap": None,
            },
            anti_patterns_triggered=anti_patterns or [],
        ),
        maps_to_metric_summary={"g_eval": 100.0, "finesure": 100.0, "qafact": 100.0, "summac": 100.0},
        editorialization_flags=[],
        metadata_partial=metadata_partial,
        missing_meta=list(missing_meta or []),
        evaluator_metadata={},
    )


# -----------------------------------------------------------------------------
# Test 1: anonymous-author newsletter (author=null) + valid body -> NO cap.
# -----------------------------------------------------------------------------
def test_anonymous_author_does_not_trigger_schema_failure_or_cap():
    result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 25, 25, []),
            ("detailed.coverage", 35, 35, []),
            ("editorial.discipline", 40, 40, []),
        ],
        metadata_partial=True,
        missing_meta=["author", "issue_date"],
    )

    # No schema_failure in any criteria_missed list.
    for component in result.rubric.components:
        assert "schema_failure" not in component.criteria_missed

    # No anti-pattern triggered, no cap applied.
    assert result.rubric.anti_patterns_triggered == []
    assert all(v is None for v in result.rubric.caps_applied.values())

    # Composite reaches full marks despite metadata_partial advisory flag.
    score = composite_score(result)
    assert score > SCHEMA_FAILURE_CAP
    assert result.metadata_partial is True
    assert result.missing_meta == ["author", "issue_date"]


# -----------------------------------------------------------------------------
# Test 2: empty brief_summary -> schema_failure fires, cap=60.
# -----------------------------------------------------------------------------
def test_empty_brief_summary_fires_schema_failure_with_cap():
    result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 0, 25, ["schema_failure"]),
            ("detailed.coverage", 0, 35, ["schema_failure"]),
            ("editorial.discipline", 40, 40, []),
        ],
        caps_applied={"hallucination_cap": SCHEMA_FAILURE_CAP, "omission_cap": None, "generic_cap": None},
        anti_patterns=[
            AntiPatternTrigger(
                id="schema_failure",
                source_region="structured_payload",
                auto_cap=SCHEMA_FAILURE_CAP,
            )
        ],
    )

    triggered_ids = {ap.id for ap in result.rubric.anti_patterns_triggered}
    assert "schema_failure" in triggered_ids
    assert composite_score(result) <= SCHEMA_FAILURE_CAP


# -----------------------------------------------------------------------------
# Test 3: malformed JSON / parse error -> schema_failure fires, cap=60.
# -----------------------------------------------------------------------------
def test_malformed_payload_fires_schema_failure_with_cap():
    # Simulate the evaluator's own decision to emit a schema_failure anti-pattern
    # when the upstream structured_payload could not be JSON-parsed.
    raw_payload = '{"mini_title": "x", "brief_summary":'  # truncated
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw_payload)

    result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 0, 25, ["schema_failure"]),
            ("detailed.coverage", 0, 35, ["schema_failure"]),
            ("editorial.discipline", 0, 40, ["schema_failure"]),
        ],
        caps_applied={"hallucination_cap": SCHEMA_FAILURE_CAP, "omission_cap": None, "generic_cap": None},
        anti_patterns=[
            AntiPatternTrigger(
                id="schema_failure",
                source_region="structured_payload",
                auto_cap=SCHEMA_FAILURE_CAP,
            )
        ],
    )

    assert composite_score(result) <= SCHEMA_FAILURE_CAP
    triggered_ids = {ap.id for ap in result.rubric.anti_patterns_triggered}
    assert "schema_failure" in triggered_ids


# -----------------------------------------------------------------------------
# Test 4: metadata_partial=true alone -> NO cap (composite unaffected).
# -----------------------------------------------------------------------------
def test_metadata_partial_alone_does_not_cap_composite():
    full_result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 25, 25, []),
            ("detailed.coverage", 35, 35, []),
            ("editorial.discipline", 40, 40, []),
        ],
        metadata_partial=False,
    )
    partial_result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 25, 25, []),
            ("detailed.coverage", 35, 35, []),
            ("editorial.discipline", 40, 40, []),
        ],
        metadata_partial=True,
        missing_meta=["author"],
    )

    assert composite_score(full_result) == composite_score(partial_result)
    assert composite_score(partial_result) > SCHEMA_FAILURE_CAP


# -----------------------------------------------------------------------------
# Test 5: metadata_partial=true AND schema_failure -> only schema_failure caps.
# -----------------------------------------------------------------------------
def test_metadata_partial_and_schema_failure_only_schema_failure_caps():
    result = _build_result(
        component_scores=[
            ("brief.thesis_capture", 0, 25, ["schema_failure"]),
            ("detailed.coverage", 0, 35, ["schema_failure"]),
            ("editorial.discipline", 40, 40, []),
        ],
        caps_applied={"hallucination_cap": SCHEMA_FAILURE_CAP, "omission_cap": None, "generic_cap": None},
        anti_patterns=[
            AntiPatternTrigger(
                id="schema_failure",
                source_region="structured_payload",
                auto_cap=SCHEMA_FAILURE_CAP,
            )
        ],
        metadata_partial=True,
        missing_meta=["author", "issue_date"],
    )

    # Cap derives from schema_failure only; metadata_partial is advisory.
    assert composite_score(result) <= SCHEMA_FAILURE_CAP
    assert result.metadata_partial is True
    # The metadata advisory must not appear as an anti-pattern.
    triggered_ids = {ap.id for ap in result.rubric.anti_patterns_triggered}
    assert "metadata_partial" not in triggered_ids


# -----------------------------------------------------------------------------
# Prompt-text contract guards (defense-in-depth: prompt drift breaks contract).
# -----------------------------------------------------------------------------
def test_prompt_text_mentions_metadata_partial_negative_example():
    text = prompts.CONSOLIDATED_USER_TEMPLATE
    assert "metadata_partial" in text
    assert "SCHEMA-FAILURE RULE" in text
    # The author/issue_date case is explicitly listed as a NEGATIVE example.
    assert "DO NOT fire schema_failure for" in text
    assert "author" in text and "issue_date" in text


def test_prompt_version_bumped_for_v5():
    assert prompts.PROMPT_VERSION == "evaluator.v5"
