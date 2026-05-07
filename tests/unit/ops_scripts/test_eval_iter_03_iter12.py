"""iter-12 Phase 7 / Task 15: E1 in _qa_summary, q9 hardcode removal, latency rename."""
import pytest


def test_qa_summary_excludes_refusal_expected_via_E1():
    """_qa_summary must skip rows with expected_empty=True (E1)."""
    from ops.scripts.eval_iter_03_playwright import _qa_summary, PhaseReport, CheckResult
    checks = [
        CheckResult(name="q1", passed=True, duration_ms=100, detail={
            "primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"],
            "expected_empty": False, "refused": False, "over_refusal": False, "gold_at_1": True,
        }),
        CheckResult(name="q9", passed=True, duration_ms=100, detail={
            "primary_citation": None, "retrieved": [], "expected": [],
            "expected_empty": True, "refused": True, "gold_at_1": None,
        }),
        CheckResult(name="q3", passed=False, duration_ms=100, detail={
            "primary_citation": "n2", "retrieved": ["n2"], "expected": ["n1"],
            "expected_empty": False, "refused": False, "over_refusal": False, "gold_at_1": False,
        }),
    ]
    rep = PhaseReport(phase="rag_qa_chain")
    rep.checks = checks
    out = _qa_summary(rep)
    assert out["total"] == 3
    assert out["n_scored"] == 2
    assert out["n_not_applicable"] == 1
    # accuracy_user_visible over scored only: 1 pass / 2 = 0.5
    assert out["accuracy_user_visible"] == 0.5


def test_qa_summary_includes_legacy_synthesizer_over_refusals_count():
    from ops.scripts.eval_iter_03_playwright import _qa_summary, PhaseReport, CheckResult
    checks = [
        CheckResult(name="q1", passed=True, duration_ms=100, detail={
            "primary_citation": "n1", "retrieved": ["n1"], "expected": ["n1"],
            "expected_empty": False, "refused": True, "over_refusal": True, "gold_at_1": True,
        }),
        CheckResult(name="q2", passed=False, duration_ms=100, detail={
            "primary_citation": "n2", "retrieved": ["n2"], "expected": ["n1"],
            "expected_empty": False, "refused": False, "over_refusal": False, "gold_at_1": False,
        }),
    ]
    rep = PhaseReport(phase="rag_qa_chain")
    rep.checks = checks
    out = _qa_summary(rep)
    assert out["synthesizer_over_refusals"] == 1


def test_q9_hardcode_removed_expected_empty_sets_gold_None():
    """For refusal-expected queries, gold_at_1 is None (E1 N/A)."""
    from ops.scripts.eval_iter_03_playwright import _set_gold_for_query
    result: dict = {}
    _set_gold_for_query(result, primary="n1", expected=[])
    assert result.get("expected_empty") is True
    # gold_at_1 MUST NOT be True — it must be None (E1 N/A)
    assert result.get("gold_at_1") is None


def test_q_with_expected_sets_gold_normally():
    from ops.scripts.eval_iter_03_playwright import _set_gold_for_query
    result: dict = {}
    _set_gold_for_query(result, primary="n1", expected=["n1"])
    assert result.get("expected_empty") is False
    assert result.get("gold_at_1") is True


def test_latency_fields_have_clear_names():
    """iter-12 Task 15: latency_ms_synth_after_ttft and latency_ms_server_total exist."""
    from ops.scripts.eval_iter_03_playwright import _set_latency_fields
    result: dict = {}
    _set_latency_fields(result, synth_ms=1100, p_user_complete_ms=8500)
    assert result["latency_ms_synth_after_ttft"] == 1100
    assert result["latency_ms_server_total"] == 8500
    # Legacy field kept for one iter for transition
    assert result["latency_ms_server"] == 1100
