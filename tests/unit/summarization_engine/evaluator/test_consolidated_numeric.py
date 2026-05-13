"""Tests for the numeric_grounding sub-signal wiring in consolidated.py."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from website.features.summarization_engine.evaluator.consolidated import (
    ConsolidatedEvaluator,
    compute_numeric_grounding_signal,
)


_BASE_GOOD_RESPONSE = {
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
            {"id": "brief_summary", "score": 22, "max_points": 25,
             "criteria_fired": [], "criteria_missed": []},
            {"id": "detailed_summary", "score": 40, "max_points": 45,
             "criteria_fired": [], "criteria_missed": []},
            {"id": "tags", "score": 13, "max_points": 15,
             "criteria_fired": [], "criteria_missed": []},
            {"id": "label", "score": 14, "max_points": 15,
             "criteria_fired": [], "criteria_missed": []},
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
        "model_used": "gemini-2.5-flash",
        "total_tokens_in": 100,
        "total_tokens_out": 50,
        "latency_ms": 1500,
    },
}


def _make_client() -> MagicMock:
    client = MagicMock()
    client._config = MagicMock()
    client._config.gemini.phase_tiers = {"evaluator": "flash"}
    client.generate = AsyncMock(
        return_value=MagicMock(
            text=json.dumps(_BASE_GOOD_RESPONSE),
            input_tokens=100,
            output_tokens=50,
            model_used="gemini-2.5-flash",
        )
    )
    return client


# ---------- compute_numeric_grounding_signal direct tests ----------

def test_signal_all_grounded_returns_score_one():
    summary = {"detailed": "Revenue grew 42% to $1,299 in 2024."}
    source = "In 2024, revenue grew 42% reaching $1,299 in Q3."
    sig = compute_numeric_grounding_signal(summary, source)
    assert sig["numeric_grounding_score"] == 1.0
    assert sig["unsupported_numeric_claims"] == []


def test_signal_half_grounded_returns_score_half_with_two_entries():
    # 4 distinct numeric tokens in summary (42%, 2024, 2025, 1500),
    # 2 grounded in source -> ratio 0.5, 2 entries listed.
    summary = {
        "text": "Year 2024 saw 42% growth, 1500 hires, and a 2025 forecast."
    }
    source = "Year 2024 saw 42% growth."
    sig = compute_numeric_grounding_signal(summary, source)
    assert sig["numeric_grounding_score"] == 0.5
    assert len(sig["unsupported_numeric_claims"]) == 2
    assert "2025" in sig["unsupported_numeric_claims"]
    assert "1500" in sig["unsupported_numeric_claims"]


def test_signal_zero_numbers_returns_score_one_empty_list():
    summary = {"detailed": "No numbers anywhere in this summary."}
    source = "Source has 2024 data and $1,299 numbers."
    sig = compute_numeric_grounding_signal(summary, source)
    assert sig["numeric_grounding_score"] == 1.0
    assert sig["unsupported_numeric_claims"] == []


def test_signal_cap_respected_at_five_for_eight_ungrounded():
    # 8 ungrounded distinct numeric tokens -> list capped at 5
    summary = {
        "a": "Years 2001 2002 2003 2004 2005",
        "b": "More years 2006 2007 2008",
    }
    source = "Source contains no matching years."
    sig = compute_numeric_grounding_signal(summary, source)
    assert sig["numeric_grounding_score"] == 0.0
    assert len(sig["unsupported_numeric_claims"]) == 5


def test_signal_walks_nested_summary_structure():
    summary = {
        "mini_title": "T",
        "sections": [
            {"heading": "intro", "body": "We saw 42% growth."},
            {"heading": "outro", "body": "Year was 2024."},
        ],
        "tags": ["x", "y"],
    }
    source = "42% growth in 2024."
    sig = compute_numeric_grounding_signal(summary, source)
    assert sig["numeric_grounding_score"] == 1.0
    assert sig["unsupported_numeric_claims"] == []


def test_signal_raises_on_none_summary():
    with pytest.raises(TypeError, match="summary_json must not be None"):
        compute_numeric_grounding_signal(None, "src")  # type: ignore[arg-type]


def test_signal_raises_on_none_source():
    with pytest.raises(TypeError, match="source_text must not be None"):
        compute_numeric_grounding_signal({"x": "y"}, None)  # type: ignore[arg-type]


def test_signal_raises_on_non_dict_summary():
    with pytest.raises(TypeError, match="summary_json must be dict"):
        compute_numeric_grounding_signal("not a dict", "src")  # type: ignore[arg-type]


# ---------- ConsolidatedEvaluator integration tests ----------

@pytest.mark.asyncio
async def test_evaluator_attaches_numeric_signal_when_grounded():
    client = _make_client()
    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={"version": "rubric_youtube.v1", "components": []},
        atomic_facts=[{"claim": "x", "importance": 3}],
        source_text="In 2024 revenue grew 42% reaching $1,299.",
        summary_json={"detailed": "Revenue grew 42% to $1,299 in 2024."},
    )
    assert result.evaluator_metadata["numeric_grounding_score"] == 1.0
    assert result.evaluator_metadata["unsupported_numeric_claims"] == []


@pytest.mark.asyncio
async def test_evaluator_attaches_numeric_signal_when_partial():
    client = _make_client()
    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={"version": "rubric_youtube.v1", "components": []},
        atomic_facts=[],
        source_text="Year 2024 saw 42% growth.",
        summary_json={
            "text": "Year 2024 saw 42% growth, 1500 hires, and a 2025 forecast."
        },
    )
    assert result.evaluator_metadata["numeric_grounding_score"] == 0.5
    assert "2025" in result.evaluator_metadata["unsupported_numeric_claims"]
    assert "1500" in result.evaluator_metadata["unsupported_numeric_claims"]
