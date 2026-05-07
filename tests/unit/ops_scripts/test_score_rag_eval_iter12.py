"""iter-12 Phase 7: Class S + W + I9 — trust-first metrics + composite reweight + per-class breakdown."""
import pytest


def test_accuracy_user_visible_excludes_refused():
    """Class S: a row that's gold@1 but refused is NOT user-visible pass."""
    from ops.scripts.score_rag_eval import _aggregate_gold_metrics
    rows = [
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "refused": False, "over_refusal": False, "expected_empty": False},
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "refused": True,  "over_refusal": True,  "expected_empty": False},
        {"primary_citation": None, "retrieved": [], "expected": [], "refused": True, "over_refusal": False, "expected_empty": True},  # E1 N/A
        {"primary_citation": "n2", "retrieved": ["n2"], "expected": ["n1"], "refused": False, "over_refusal": False, "expected_empty": False},
    ]
    out = _aggregate_gold_metrics(rows)
    # Scored rows (excl. E1 N/A) = 3. User-visible passes = 1 (only first row). 1/3.
    assert out["accuracy_user_visible"] == round(1/3, 4)
    # over_refusal: 1 of 3 scored rows.
    assert out["over_refusal_rate"] == round(1/3, 4)


def test_aggregate_includes_retrieval_recall_at_1():
    """I9: retrieval_recall_at_1 (retrieved[0] in expected) is a separate diagnostic."""
    from ops.scripts.score_rag_eval import _aggregate_gold_metrics
    rows = [
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},
        {"primary_citation": "n1", "retrieved": ["n2"], "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},  # rerank reorder
        {"primary_citation": "n2", "retrieved": ["n1"], "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},  # primary mismatch
    ]
    out = _aggregate_gold_metrics(rows)
    assert out["gold_at_1_unconditional"] == round(2/3, 4)
    assert out["retrieval_recall_at_1"] == round(2/3, 4)


def test_under_refusal_rate_uses_faithfulness_below_05():
    """Under-refusal: answered queries with faithfulness < 0.5 count."""
    from ops.scripts.score_rag_eval import _aggregate_gold_metrics
    rows = [
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "refused": False, "over_refusal": False, "expected_empty": False, "faithfulness": 0.9},
        {"primary_citation": "n2", "retrieved": ["n2"], "expected": ["n1"], "refused": False, "over_refusal": False, "expected_empty": False, "faithfulness": 0.4},
        {"primary_citation": "n3", "retrieved": ["n3"], "expected": ["n3"], "refused": True, "over_refusal": False, "expected_empty": False},  # refused, not counted in answered
    ]
    out = _aggregate_gold_metrics(rows)
    # answered = 2; under_refusal = 1 (faithfulness 0.4 < 0.5); 1/2 = 0.5
    assert out["under_refusal_rate"] == 0.5


def test_per_class_breakdown_emits_each_class():
    from ops.scripts.score_rag_eval import _per_class_breakdown
    rows = [
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "query_class": "lookup", "refused": False, "over_refusal": False, "expected_empty": False},
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "query_class": "lookup", "refused": True,  "over_refusal": True,  "expected_empty": False},
        {"primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"], "query_class": "thematic", "refused": False, "over_refusal": False, "expected_empty": False},
    ]
    out = _per_class_breakdown(rows)
    assert "lookup" in out
    assert "thematic" in out
    assert out["lookup"]["accuracy_user_visible"] == 0.5
    assert out["thematic"]["accuracy_user_visible"] == 1.0


def test_composite_uses_trust_first_weights_for_iter12_plus():
    """Class W: iter-12+ composite weights = trust 0.40 / accuracy 0.30 / retrieval 0.15 / calibration 0.10 / latency 0.05."""
    from ops.scripts.score_rag_eval import _composite_weights_for_iter
    weights = _composite_weights_for_iter("iter-12")
    assert weights["trust"] == 0.40
    assert weights["accuracy"] == 0.30
    assert weights["retrieval"] == 0.15
    assert weights["calibration"] == 0.10
    assert weights["latency"] == 0.05
    # Sum to 1.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_composite_uses_legacy_weights_for_iter11_minus():
    """Pre-iter-12 evals stay at legacy weights for historical comparability."""
    from ops.scripts.score_rag_eval import _composite_weights_for_iter
    weights = _composite_weights_for_iter("iter-11")
    # legacy: chunking 0.10 / retrieval 0.25 / reranking 0.20 / synthesis 0.45
    assert weights["chunking"] == 0.10
    assert weights["retrieval"] == 0.25
    assert weights["reranking"] == 0.20
    assert weights["synthesis"] == 0.45
