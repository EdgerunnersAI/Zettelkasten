import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from website.features.summarization_engine.evaluator.consolidated import (
    ConsolidatedEvaluator,
    evaluator_implementation_fingerprint,
    rubric_sha256,
)
from website.features.summarization_engine.evaluator.models import EvalResult


_GOOD_RESPONSE = {
    "g_eval": {
        "coherence": {"score": 3, "anchor": "1=disjointed,2=minor jumps,3=logical", "reasoning": "ok"},
        "fluency": {"score": 3, "anchor": "1=ungrammatical,2=minor errors,3=clean", "reasoning": "ok"},
    },
    "finesure": {
        "faithfulness": {"score": 0.95, "items": []},
        "completeness": {"score": 0.88, "items": []},
        "conciseness": {"score": 0.9, "items": []},
    },
    "summac_lite": {
        "score": 0.93,
        "contradicted_sentences": [],
        "neutral_sentences": [],
    },
    "rubric": {
        "components": [
            {
                "id": "brief_summary",
                "score": 22,
                "max_points": 25,
                "criteria_fired": [],
                "criteria_missed": [],
            },
            {
                "id": "detailed_summary",
                "score": 40,
                "max_points": 45,
                "criteria_fired": [],
                "criteria_missed": [],
            },
            {
                "id": "tags",
                "score": 13,
                "max_points": 15,
                "criteria_fired": [],
                "criteria_missed": [],
            },
            {
                "id": "label",
                "score": 14,
                "max_points": 15,
                "criteria_fired": [],
                "criteria_missed": [],
            },
        ],
        "caps_applied": {
            "hallucination_cap": None,
            "omission_cap": None,
            "generic_cap": None,
        },
        "anti_patterns_triggered": [],
    },
    "maps_to_metric_summary": {
        "g_eval_composite": 90,
        "finesure_composite": 91,
        "qafact_composite": 90,
        "summac_composite": 93,
    },
    "editorialization_flags": [],
    "evaluator_metadata": {
        "prompt_version": "evaluator.v1",
        "rubric_version": "rubric_youtube.v1",
        "atomic_facts_hash": "abc",
        "model_used": "gemini-2.5-pro",
        "total_tokens_in": 100,
        "total_tokens_out": 50,
        "latency_ms": 1500,
    },
}


@pytest.mark.asyncio
async def test_consolidated_evaluator_parses_response():
    client = MagicMock()
    client._config = MagicMock()
    client._config.gemini.phase_tiers = {"evaluator": "flash"}
    client.generate = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(_GOOD_RESPONSE),
            input_tokens=100,
            output_tokens=50,
            model_used="gemini-2.5-flash",
        )
    )

    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={
            "version": "rubric_youtube.v1",
            "composite_max_points": 100,
            "source_type": "youtube",
            "components": [],
        },
        atomic_facts=[{"claim": "x", "importance": 3}],
        source_text="source",
        summary_json={"mini_title": "t"},
    )

    assert isinstance(result, EvalResult)
    assert result.rubric.total_of_100 == 89
    assert result.evaluator_metadata["model_used"] == "gemini-2.5-flash"
    assert (
        result.evaluator_metadata["implementation_fingerprint"]
        == evaluator_implementation_fingerprint()
    )
    assert result.evaluator_metadata["rubric_sha256"] == rubric_sha256(
        {
            "version": "rubric_youtube.v1",
            "composite_max_points": 100,
            "source_type": "youtube",
            "components": [],
        }
    )
    assert client.generate.await_args.kwargs["tier"] == "flash"


@pytest.mark.asyncio
async def test_consolidated_evaluator_retries_malformed_json():
    client = MagicMock()
    client._config = MagicMock()
    client._config.gemini.phase_tiers = {"evaluator": "flash"}
    client.generate = AsyncMock(
        side_effect=[
            MagicMock(
                text='```json\n{"g_eval": {"reasoning": "bad "quote""}}\n```',
                input_tokens=100,
                output_tokens=50,
                model_used="gemini-2.5-flash",
            ),
            MagicMock(
                text=json.dumps(_GOOD_RESPONSE),
                input_tokens=101,
                output_tokens=51,
                model_used="gemini-2.5-flash",
            ),
        ]
    )

    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={
            "version": "rubric_youtube.v1",
            "composite_max_points": 100,
            "source_type": "youtube",
            "components": [],
        },
        atomic_facts=[{"claim": "x", "importance": 3}],
        source_text="source",
        summary_json={"mini_title": "t"},
    )

    assert isinstance(result, EvalResult)
    assert client.generate.await_count == 2
    assert result.evaluator_metadata["total_tokens_in"] == 101


# ---------------------------------------------------------------------------
# CF-2 R2: shape-aware judge prompt + editorialization_flags hard gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_prompt_carries_shape_mask_text_for_academic_roundup():
    """When summary_json._shape == academic_roundup, the rendered judge prompt
    must contain the shape-mask override telling the judge NOT to apply
    editorialization_penalty for paper-fact language."""
    client = MagicMock()
    client._config = MagicMock()
    client._config.gemini.phase_tiers = {"evaluator": "flash"}
    flagged = dict(_GOOD_RESPONSE)
    flagged["editorialization_flags"] = [
        {"sentence": "novel synthesis", "flag_type": "added_judgment", "explanation": ""},
        {"sentence": "efficient route", "flag_type": "added_stance", "explanation": ""},
        {"sentence": "broad scope", "flag_type": "added_framing", "explanation": ""},
    ]
    client.generate = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(flagged),
            input_tokens=100,
            output_tokens=50,
            model_used="gemini-2.5-flash",
        )
    )

    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={"version": "v1", "components": []},
        atomic_facts=[],
        source_text="source",
        summary_json={"mini_title": "t", "_shape": "academic_roundup"},
    )

    sent_prompt = client.generate.await_args.args[0]
    assert "CONTENT_SHAPE: academic_roundup" in sent_prompt
    assert "do NOT apply editorialization_penalty" in sent_prompt
    # Hard gate must zero editorialization_flags for shape-exempt newsletters.
    assert result.editorialization_flags == []
    assert (
        result.evaluator_metadata["editorialization_zeroed_by_shape"]
        == "academic_roundup"
    )


@pytest.mark.asyncio
async def test_judge_prompt_defaults_shape_to_general_when_absent():
    client = MagicMock()
    client._config = MagicMock()
    client._config.gemini.phase_tiers = {"evaluator": "flash"}
    flagged = dict(_GOOD_RESPONSE)
    flagged["editorialization_flags"] = [
        {"sentence": "stance", "flag_type": "added_stance", "explanation": ""}
    ]
    client.generate = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(flagged),
            input_tokens=100,
            output_tokens=50,
            model_used="gemini-2.5-flash",
        )
    )

    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={"version": "v1", "components": []},
        atomic_facts=[],
        source_text="source",
        summary_json={"mini_title": "t"},   # no _shape key
    )

    sent_prompt = client.generate.await_args.args[0]
    assert "CONTENT_SHAPE: general" in sent_prompt
    # General shape is NOT exempt; flags must be preserved.
    assert len(result.editorialization_flags) == 1
    assert "editorialization_zeroed_by_shape" not in result.evaluator_metadata


def test_prompt_version_bumped_to_v6():
    from website.features.summarization_engine.evaluator.prompts import PROMPT_VERSION

    assert PROMPT_VERSION == "evaluator.v6"
