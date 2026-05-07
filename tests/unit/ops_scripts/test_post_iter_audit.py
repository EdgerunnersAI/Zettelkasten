"""iter-12 Task 36: post-iter audit aggregator tests."""
import json
from pathlib import Path
import pytest
from ops.scripts.post_iter_audit import run_audit, AuditFindings


def _make_verif(checks: list) -> str:
    return json.dumps({
        "iter": "iter-12",
        "qa_summary": {"total": len(checks), "accuracy_user_visible": 0.9},
        "phases": [{"phase": "rag_qa_chain", "checks": checks}],
    })


def test_audit_aggregates_scores_and_failures(tmp_path):
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text(
        "# 06 Scorecard\n**Composite:** 88.50\n"
        "## Holistic monitoring (iter-12 trust-first)\n"
        "- accuracy_user_visible: 0.9231\n"
        "- over_refusal_rate: 0.0769\n"
        "- under_refusal_rate: 0.0500\n"
    )
    (iter_dir / "verification_results.json").write_text(_make_verif([
        {"name": "Q-A q1", "passed": True, "detail": {
            "qid": "q1", "expected": ["gh-zk-org-zk"],
            "primary_citation": "gh-zk-org-zk", "refused": False, "over_refusal": False,
            "gold_at_1": True, "retry_outcome_class": "success",
            "t_db_wait_ms": 120, "t_rerank_ms": 80, "t_synth_ms": 1100,
            "p_user_complete_ms": 8500,
        }},
        {"name": "Q-A q5", "passed": False, "detail": {
            "qid": "q5", "expected": ["yt-walker", "nl-pragmatic"],
            "primary_citation": "gh-zk-org-zk", "refused": False, "over_refusal": False,
            "gold_at_1": False, "retry_outcome_class": "still_unsupported",
            "t_db_wait_ms": 180, "t_rerank_ms": 120, "t_synth_ms": 2200,
            "p_user_complete_ms": 14000,
        }},
    ]))

    findings = run_audit(iter_dir)
    assert findings.composite == 88.50
    assert findings.accuracy_user_visible == 0.9231
    assert len(findings.failed_gold_at_1) == 1
    assert findings.failed_gold_at_1[0]["qid"] == "q5"
    assert findings.failed_gold_at_1[0]["primary_citation"] == "gh-zk-org-zk"
    assert findings.failed_gold_at_1[0]["retry_outcome_class"] == "still_unsupported"


def test_audit_handles_missing_files(tmp_path):
    iter_dir = tmp_path / "iter-empty"
    iter_dir.mkdir()
    findings = run_audit(iter_dir)
    assert findings.composite is None
    assert findings.failed_gold_at_1 == []


def test_audit_writes_report(tmp_path):
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text("**Composite:** 75.00\n")
    (iter_dir / "verification_results.json").write_text(json.dumps({
        "qa_summary": {"total": 1, "accuracy_user_visible": 0.5},
        "phases": [],
    }))
    findings = run_audit(iter_dir)
    report_path = iter_dir / "post_iter_audit.md"
    findings.write_report(report_path)
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "Composite" in text and "75.00" in text
    # Section 5 must be present
    assert "Live env-during-eval" in text


def test_audit_emits_live_env_section(tmp_path):
    """Synthetic env_capture file → audit report contains live env table with match column."""
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text("**Composite:** 82.00\n")
    (iter_dir / "verification_results.json").write_text(json.dumps({
        "phases": [],
    }))
    # Pre-populate live env capture file as operator would
    audit_dir = iter_dir / "_audit"
    audit_dir.mkdir()
    env_lines = (
        "RAG_ANCHOR_BOOST_ENABLED=true\n"
        "RAG_ANCHOR_SEED_INJECTION_ENABLED=true\n"
        "RAG_EXECUTOR_MAX_WORKERS=8\n"
        "RAG_RPC_GLOBAL_SEMAPHORE=8\n"
        "RAG_HTTPX_MAX_CONNECTIONS=16\n"
        "RAG_HTTPX_MAX_KEEPALIVE=8\n"
        "RAG_ENTITY_GATHER_SEMAPHORE=3\n"
        "RAG_SCORE_RANK_GAP_BYPASS=1.5\n"
        "RAG_RETRY_GAP_BYPASS=1.5\n"
        "RAG_TITLE_OVERLAP_PERCENTILE=75\n"
        "RAG_TITLE_OVERLAP_FLOOR_FALLBACK=0.10\n"
        "RAG_ROUTER_VERSION=v4\n"
        "RAG_SCORE_RANK_DEMOTE_SLOPE=0.20\n"
        "RAG_ANCHOR_BANDIT_ENABLED=true\n"
    )
    (audit_dir / "live_env_capture.txt").write_text(env_lines)

    findings = run_audit(iter_dir)

    # All expected knobs should be captured
    assert findings.live_env_state.get("RAG_ANCHOR_BOOST_ENABLED") == "true"
    assert findings.live_env_state.get("RAG_EXECUTOR_MAX_WORKERS") == "8"
    assert findings.live_env_state.get("RAG_ROUTER_VERSION") == "v4"
    # No drift when all match
    assert findings.live_env_drift == []

    report_path = iter_dir / "post_iter_audit.md"
    findings.write_report(report_path)
    text = report_path.read_text()
    assert "Live env-during-eval" in text
    assert "RAG_ANCHOR_BOOST_ENABLED" in text
    assert "expected" in text.lower() and "actual" in text.lower()
    # MATCH column should show true for all
    assert "MATCH" in text


def test_audit_flags_live_env_drift(tmp_path):
    """When a knob diverges from expected, live_env_drift is populated and report flags it."""
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text("**Composite:** 70.00\n")
    (iter_dir / "verification_results.json").write_text(json.dumps({"phases": []}))
    audit_dir = iter_dir / "_audit"
    audit_dir.mkdir()
    # RAG_ANCHOR_BOOST_ENABLED is false (wrong!) and EXECUTOR_MAX_WORKERS=4 (wrong!)
    env_lines = (
        "RAG_ANCHOR_BOOST_ENABLED=false\n"
        "RAG_EXECUTOR_MAX_WORKERS=4\n"
        "RAG_RPC_GLOBAL_SEMAPHORE=8\n"
    )
    (audit_dir / "live_env_capture.txt").write_text(env_lines)

    findings = run_audit(iter_dir)
    assert "RAG_ANCHOR_BOOST_ENABLED" in findings.live_env_drift
    assert "RAG_EXECUTOR_MAX_WORKERS" in findings.live_env_drift

    report_path = iter_dir / "post_iter_audit.md"
    findings.write_report(report_path)
    text = report_path.read_text(encoding="utf-8")
    assert "DRIFT" in text or "drift" in text.lower()
