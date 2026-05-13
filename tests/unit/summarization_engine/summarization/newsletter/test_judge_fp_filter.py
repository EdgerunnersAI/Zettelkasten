"""Regression tests for R3 post-judge FP filter (CF-3).

Targets ops.scripts.lib.phases.filter_judge_false_positives, ensuring that
judge-emitted flags whose spans actually appear verbatim in the source
(modulo whitespace / digit-grouping) are dropped, with hallucination caps
cleared when the only remaining hallucination AP was the false positive.

Industry pattern citations:
- vLLM HaluGate two-stage filter (2025)
- Mirage EMNLP 2025 (~10-25% LLM-judge FP rate on factual claims)
- JudgeBench ICLR 2025
"""
from __future__ import annotations

from ops.scripts.lib.phases import filter_judge_false_positives


def _make_eval_dict(
    *,
    contradicted: list[dict] | None = None,
    anti_patterns: list[dict] | None = None,
    hallucination_cap: int | None = None,
) -> dict:
    return {
        "summac_lite": {
            "score": 1.0,
            "contradicted_sentences": list(contradicted or []),
            "neutral_sentences": [],
        },
        "rubric": {
            "components": [],
            "caps_applied": {
                "hallucination_cap": hallucination_cap,
                "omission_cap": None,
                "generic_cap": None,
            },
            "anti_patterns_triggered": list(anti_patterns or []),
        },
    }


def test_stripe_card_summac_contradicted_dropped_and_cap_cleared():
    """Source contains Stripe test card; judge flags it → filter drops + clears cap."""
    source = "Use Stripe's test card 4000 0000 0000 9995 in checkout."
    eval_d = _make_eval_dict(
        contradicted=[{"sentence": "4000 0000 0000 9995", "reason": "not in source"}],
        anti_patterns=[
            {
                "id": "invented_number",
                "source_region": "4000 0000 0000 9995",
                "auto_cap": 40,
            }
        ],
        hallucination_cap=40,
    )
    out = filter_judge_false_positives(eval_d, source)
    assert out["summac_lite"]["contradicted_sentences"] == []
    assert out["rubric"]["anti_patterns_triggered"] == []
    assert out["rubric"]["caps_applied"]["hallucination_cap"] is None
    kinds = [d["kind"] for d in out["judge_false_positives_dropped"]]
    assert "summac_contradicted_sentence" in kinds
    assert "invented_number_fp" in kinds


def test_genuine_invented_number_is_kept():
    """Source says $3M, summary claims $5M → filter MUST KEEP the flag."""
    source = "Series A raised $3 million from investors."
    eval_d = _make_eval_dict(
        anti_patterns=[
            {
                "id": "invented_number",
                "source_region": "$5 million",
                "auto_cap": 40,
            }
        ],
        hallucination_cap=40,
    )
    out = filter_judge_false_positives(eval_d, source)
    assert len(out["rubric"]["anti_patterns_triggered"]) == 1
    assert out["rubric"]["caps_applied"]["hallucination_cap"] == 40
    assert "judge_false_positives_dropped" not in out


def test_whitespace_tolerant_drop():
    source = "Card 4000 0000 0000 9995 was charged."
    eval_d = _make_eval_dict(
        anti_patterns=[
            {
                "id": "invented_number",
                "source_region": "4000000000009995",
                "auto_cap": 40,
            }
        ],
        hallucination_cap=40,
    )
    out = filter_judge_false_positives(eval_d, source)
    assert out["rubric"]["anti_patterns_triggered"] == []
    assert out["rubric"]["caps_applied"]["hallucination_cap"] is None


def test_comma_separator_tolerant_drop():
    source = "The deal was worth $5,000,000 over five years."
    eval_d = _make_eval_dict(
        anti_patterns=[
            {
                "id": "invented_number",
                "source_region": "$5000000",
                "auto_cap": 40,
            }
        ],
        hallucination_cap=40,
    )
    out = filter_judge_false_positives(eval_d, source)
    assert out["rubric"]["anti_patterns_triggered"] == []
    assert out["rubric"]["caps_applied"]["hallucination_cap"] is None


def test_empty_span_no_crash():
    eval_d = _make_eval_dict(
        contradicted=[{"sentence": "", "reason": ""}],
        anti_patterns=[{"id": "invented_number", "source_region": "", "auto_cap": None}],
    )
    out = filter_judge_false_positives(eval_d, "some source text")
    # Empty span is treated as unverified → kept (no crash)
    assert len(out["summac_lite"]["contradicted_sentences"]) == 1
    assert len(out["rubric"]["anti_patterns_triggered"]) == 1


def test_dropped_telemetry_key_labels():
    source = "card 4000 0000 0000 9995"
    eval_d = _make_eval_dict(
        contradicted=[{"sentence": "4000 0000 0000 9995"}],
        anti_patterns=[
            {"id": "invented_number", "source_region": "4000 0000 0000 9995"}
        ],
    )
    out = filter_judge_false_positives(eval_d, source)
    dropped = out["judge_false_positives_dropped"]
    kinds = {d["kind"] for d in dropped}
    assert kinds == {"summac_contradicted_sentence", "invented_number_fp"}


def test_non_invented_number_ap_passes_through():
    """Filter only targets invented_number; other APs untouched."""
    eval_d = _make_eval_dict(
        anti_patterns=[
            {"id": "editorialization_penalty", "source_region": "totally invented"}
        ],
    )
    out = filter_judge_false_positives(eval_d, "irrelevant source")
    assert len(out["rubric"]["anti_patterns_triggered"]) == 1


def test_pydantic_eval_result_path():
    """Filter must also work on the Pydantic EvalResult instance returned by evaluate()."""
    from website.features.summarization_engine.evaluator.models import EvalResult

    payload = {
        "g_eval": {
            "coherence": {"score": 3, "anchor": "", "reasoning": ""},
            "fluency": {"score": 3, "anchor": "", "reasoning": ""},
        },
        "finesure": {
            "faithfulness": {"score": 1.0, "items": []},
            "completeness": {"score": 1.0, "items": []},
            "conciseness": {"score": 1.0, "items": []},
        },
        "summac_lite": {
            "score": 1.0,
            "contradicted_sentences": [{"sentence": "4000 0000 0000 9995"}],
            "neutral_sentences": [],
        },
        "rubric": {
            "components": [],
            "caps_applied": {"hallucination_cap": 40, "omission_cap": None, "generic_cap": None},
            "anti_patterns_triggered": [
                {"id": "invented_number", "source_region": "4000 0000 0000 9995", "auto_cap": 40}
            ],
        },
        "maps_to_metric_summary": {},
        "editorialization_flags": [],
        "evaluator_metadata": {},
    }
    model = EvalResult.model_validate(payload)
    source = "Stripe test card 4000 0000 0000 9995 — use this."
    filter_judge_false_positives(model, source)
    assert model.summac_lite.contradicted_sentences == []
    assert model.rubric.anti_patterns_triggered == []
    assert model.rubric.caps_applied["hallucination_cap"] is None
    assert "judge_false_positives_dropped" in model.evaluator_metadata
