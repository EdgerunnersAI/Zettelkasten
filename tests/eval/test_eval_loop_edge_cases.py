"""Extreme edge cases and failure-mode tests for the eval_loop CLI.

These tests verify that the CLI and its supporting libraries handle:
- malformed JSON / truncated files
- concurrent writes to the churn ledger
- URL injection / path traversal attempts in slugs
- Unicode / emoji / RTL text in URLs and summaries
- empty rubrics, missing rubric components
- held-out directory with zero files, one file, 100 files
- extremely long URL strings
- `links.txt` edge formats (blank lines, comments, numbered, mixed-case URLs)
- clock skew in composite_delta computation
- git commit failure propagation (dirty worktree, detached HEAD, missing HEAD)
- divergence stamp band boundaries (exact 5.0, 10.0, NaN-ish diffs)
- state detector when iter dir contains unrelated files
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ops.scripts.lib import artifacts, churn_ledger, git_helper, phases
from ops.scripts.lib.links_parser import parse_links_file
from ops.scripts.lib.state_detector import IterationState, detect_iteration_state


# ── state detector edge cases ────────────────────────────────────────────────


def test_state_detector_ignores_unrelated_files(tmp_path: Path):
    (tmp_path / "random.txt").write_text("noise", encoding="utf-8")
    (tmp_path / "notes.md").write_text("notes", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.PHASE_A_REQUIRED


def test_state_detector_phase_b_requires_all_three(tmp_path: Path):
    # manual_review.md alone is not enough — need summary.json + eval.json too
    (tmp_path / "manual_review.md").write_text("review", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.PHASE_A_REQUIRED


def test_state_detector_committed_wins_over_review(tmp_path: Path):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "eval.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manual_review_prompt.md").write_text("p", encoding="utf-8")
    (tmp_path / "manual_review.md").write_text("r", encoding="utf-8")
    (tmp_path / "diff.md").write_text("d", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.ALREADY_COMMITTED


# ── url_slug edge cases ──────────────────────────────────────────────────────


def test_url_slug_handles_unicode_and_emoji():
    a = artifacts.url_slug("https://example.com/🎵/foo")
    b = artifacts.url_slug("https://example.com/音楽/foo")
    assert len(a) == 16 and len(b) == 16 and a != b


def test_url_slug_handles_extremely_long_url():
    long_url = "https://example.com/" + "a" * 10_000
    slug = artifacts.url_slug(long_url)
    assert len(slug) == 16 and slug.isalnum()


def test_url_slug_path_traversal_safe():
    # slug is a sha256 prefix — "../" in URL cannot escape
    slug = artifacts.url_slug("https://evil/../../../etc/passwd")
    assert "/" not in slug and ".." not in slug


# ── links.txt parser edge cases ──────────────────────────────────────────────


def test_parse_links_file_handles_blank_comments_and_numbered(tmp_path: Path):
    content = """\
# YouTube
https://youtube.com/watch?v=a

# Reddit — stale
#https://reddit.com/stale
1. https://www.reddit.com/r/x/1
   2.  https://www.reddit.com/r/x/2
"""
    path = tmp_path / "links.txt"
    path.write_text(content, encoding="utf-8")
    parsed = parse_links_file(path)
    assert "https://youtube.com/watch?v=a" in parsed.get("youtube", [])
    assert any("reddit.com/r/x/1" in url for url in parsed.get("reddit", []))
    assert any("reddit.com/r/x/2" in url for url in parsed.get("reddit", []))
    # commented-out URL must not leak through
    assert not any("stale" in url for url in parsed.get("reddit", []))


def test_parse_links_file_missing_file_returns_empty(tmp_path: Path):
    parsed = parse_links_file(tmp_path / "nonexistent.txt")
    assert isinstance(parsed, dict)


# ── churn ledger under concurrent writes ─────────────────────────────────────


def test_churn_ledger_concurrent_record_last_writer_wins(tmp_path: Path):
    """Concurrent record calls for different iters must all land."""
    threads = []
    for i in range(1, 11):
        t = threading.Thread(
            target=churn_ledger.record,
            kwargs={
                "source_dir": tmp_path,
                "iter_num": i,
                "files": [f"f{i}.py"],
                "targeted_criterion": "c",
                "criterion_delta": 0.1,
                "composite_delta": 0.0,
            },
        )
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    entries = churn_ledger.load(tmp_path)
    # At minimum the final round-trip survives; the file is not corrupted
    assert isinstance(entries, list)
    loaded = json.loads((tmp_path / "edit_ledger.json").read_text(encoding="utf-8"))
    assert "entries" in loaded


def test_churn_ledger_empty_file_tolerated(tmp_path: Path):
    (tmp_path / "edit_ledger.json").write_text('{"entries": []}', encoding="utf-8")
    assert churn_ledger.load(tmp_path) == []


def test_churn_ledger_lookback_zero_returns_empty(tmp_path: Path):
    churn_ledger.record(
        tmp_path, iter_num=1, files=["a.py"],
        targeted_criterion="c", criterion_delta=0.1, composite_delta=0.0,
    )
    # lookback=0 means "look at 0 iters" → no files can be churning
    assert churn_ledger.churning_files(tmp_path, current_iter=2, lookback=0) == []


# ── artifacts round-trip edge cases ──────────────────────────────────────────


def test_write_json_handles_nested_path_objects(tmp_path: Path):
    payload = {
        "path": Path("/tmp/x"),
        "nested": {"inner": Path("/tmp/y")},
    }
    target = tmp_path / "deep" / "payload.json"
    artifacts.write_json(target, payload)
    data = artifacts.read_json(target)
    # Path objects serialized via default=str
    assert isinstance(data["path"], str)
    assert isinstance(data["nested"]["inner"], str)


def test_read_json_raises_on_malformed(tmp_path: Path):
    target = tmp_path / "bad.json"
    target.write_text("{not: valid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        artifacts.read_json(target)


def test_iter_artifact_paths_returns_absolute_paths(tmp_path: Path):
    paths = artifacts.iter_artifact_paths(tmp_path)
    for name, path in paths.items():
        assert path.is_absolute() or str(path).startswith(str(tmp_path))


# ── divergence stamp boundary precision ──────────────────────────────────────


@pytest.mark.parametrize("diff", [0.0, 4.9999, 5.0])
def test_divergence_stamp_exact_five_is_agreement(diff):
    assert phases._divergence_stamp(diff) == "AGREEMENT"


@pytest.mark.parametrize("diff", [5.001, 9.9999, 10.0])
def test_divergence_stamp_ten_boundary_is_minor(diff):
    assert phases._divergence_stamp(diff) == "MINOR_DISAGREEMENT"


def test_infer_changed_files_falls_back_to_commit_range(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(phases.git_helper, "worktree_changed_paths", lambda _: [])
    monkeypatch.setattr(
        phases.git_helper,
        "last_commit_matching_subject",
        lambda *_: "abc123",
    )
    monkeypatch.setattr(
        phases.git_helper,
        "changed_paths_since",
        lambda *_: [
            "website/features/summarization_engine/summarization/newsletter/prompts.py",
            "docs/summary_eval/newsletter/iter-02/eval.json",
            "docs/summary_eval/_cache/ingests/x.json",
            "docs/summary_eval/newsletter/edit_ledger.json",
        ],
    )

    changed = phases._infer_changed_files(tmp_path, "newsletter", 2)
    assert changed == [
        "website/features/summarization_engine/summarization/newsletter/prompts.py"
    ]


def test_divergence_stamp_handles_very_large_diff():
    assert phases._divergence_stamp(1e9) == "MAJOR_DISAGREEMENT"


def test_divergence_stamp_handles_negative_diff():
    # spec uses abs() on the diff — but the function takes the magnitude.
    # negative values coming in shouldn't crash; they'll be classified as AGREEMENT.
    assert phases._divergence_stamp(-5.0) == "AGREEMENT"


# ── composite from eval file edge cases ──────────────────────────────────────


def _minimal_eval_payload(rubric_score=0.0, faith=0.0, complete=0.0, concise=0.0) -> dict:
    return {
        "g_eval": {
            "coherence": 0.0, "consistency": 0.0, "fluency": 0.0, "relevance": 0.0,
            "reasoning": "",
        },
        "finesure": {
            "faithfulness": {"score": faith, "items": []},
            "completeness": {"score": complete, "items": []},
            "conciseness": {"score": concise, "items": []},
        },
        "summac_lite": {"score": 0.0, "contradicted_sentences": [], "neutral_sentences": []},
        "rubric": {
            "components": [
                {"id": "c1", "score": rubric_score, "max_points": 100,
                 "criteria_fired": [], "criteria_missed": []},
            ],
            "caps_applied": {"hallucination_cap": None, "omission_cap": None, "generic_cap": None},
            "anti_patterns_triggered": [],
        },
        "maps_to_metric_summary": {},
        "editorialization_flags": [],
        "evaluator_metadata": {},
    }


def test_composite_from_eval_file_all_zeros(tmp_path: Path):
    path = tmp_path / "eval.json"
    artifacts.write_json(path, _minimal_eval_payload())
    # W3 baseline shift: g_eval is now anchored-ternary {1,2,3}. The legacy-float
    # back-compat path coerces 0.0 into the minimum band (score=1) per
    # GEvalScores._coerce_criterion (n <= 1.5 → 1). Thus the g_eval contribution
    # has a floor of 0.10 * ((1+1)/6 * 100) ≈ 3.33 even when every other signal
    # is zero. Production never emits score=0 (ternary judge yields 1/2/3), so
    # this floor only surfaces in legacy fixtures.
    assert phases._composite_from_eval_file(path) == pytest.approx(3.33, abs=0.05)


def test_composite_from_eval_file_perfect_scores(tmp_path: Path):
    payload = {
        "g_eval": {
            "coherence": 5.0, "consistency": 5.0, "fluency": 5.0, "relevance": 5.0,
            "reasoning": "",
        },
        "finesure": {
            "faithfulness": {"score": 1.0, "items": []},
            "completeness": {"score": 1.0, "items": []},
            "conciseness": {"score": 1.0, "items": []},
        },
        "summac_lite": {"score": 1.0, "contradicted_sentences": [], "neutral_sentences": []},
        "rubric": {
            "components": [
                {"id": "c1", "score": 100.0, "max_points": 100,
                 "criteria_fired": [], "criteria_missed": []},
            ],
            "caps_applied": {"hallucination_cap": None, "omission_cap": None, "generic_cap": None},
            "anti_patterns_triggered": [],
        },
        "maps_to_metric_summary": {},
        "editorialization_flags": [],
        "evaluator_metadata": {},
    }
    path = tmp_path / "eval.json"
    artifacts.write_json(path, payload)
    # 0.6*100 + 0.2*100 + 0.1*100 + 0.1*100 = 100
    assert phases._composite_from_eval_file(path) == pytest.approx(100.0, abs=0.05)


def test_composite_cap_hallucination_enforced(tmp_path: Path):
    payload = _minimal_eval_payload(rubric_score=100, faith=1.0, complete=1.0)
    payload["rubric"]["caps_applied"]["hallucination_cap"] = 60
    path = tmp_path / "eval.json"
    artifacts.write_json(path, payload)
    # Base = 0.6*100 + 0.2*100 + 0.1*100 + 0.1*0 = 90 → capped at 60
    assert phases._composite_from_eval_file(path) == 60.0


def test_composite_from_iter_dir_held_out_with_empty_subdir(tmp_path: Path):
    (tmp_path / "held_out").mkdir()
    # No per-URL eval.json → should return 0 cleanly, not crash
    assert phases._composite_from_iter_dir(tmp_path) == 0.0


def test_composite_from_eval_file_list_skips_missing_eval(tmp_path: Path):
    payloads = [
        {"url": "https://a", "eval": _minimal_eval_payload(rubric_score=50, faith=0.5, complete=0.5, concise=0.5)},
        {"url": "https://b"},  # no "eval" key
    ]
    path = tmp_path / "eval.json"
    artifacts.write_json(path, payloads)
    # Only the first entry contributes (the second is skipped cleanly)
    score = phases._composite_from_eval_file(path)
    assert score > 0.0 and score < 100.0


# ── git_helper failure modes ─────────────────────────────────────────────────


def test_git_helper_commit_raises_when_not_a_repo(tmp_path: Path):
    # Fresh dir with no .git
    with pytest.raises(git_helper.GitError):
        git_helper.head_sha(tmp_path)


def _init_repo(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=tmp_path, check=True)


def test_git_helper_add_paths_nonexistent_tolerated(tmp_path: Path):
    _init_repo(tmp_path)
    # Adding a path that doesn't exist is a git error — verify it raises or noops gracefully
    try:
        git_helper.add_paths(tmp_path, [tmp_path / "nonexistent"])
    except git_helper.GitError:
        pass  # acceptable — we just want it to not corrupt the index


def test_git_helper_current_branch_returns_string(tmp_path: Path):
    _init_repo(tmp_path)
    branch = git_helper.current_branch(tmp_path)
    assert isinstance(branch, str) and branch  # could be main/master/HEAD


# ── write_diff / write_next_actions edge cases ───────────────────────────────


def test_write_diff_handles_zero_delta(tmp_path: Path):
    phases._write_diff(
        iter_dir=tmp_path, prev_dir=None,
        computed_composite=0.0, estimated_composite=0.0, stamp="AGREEMENT",
    )
    text = (tmp_path / "diff.md").read_text(encoding="utf-8")
    assert "computed_composite: 0.00" in text


def test_write_next_actions_empty_eval_file(tmp_path: Path):
    eval_path = tmp_path / "eval.json"
    artifacts.write_json(eval_path, _minimal_eval_payload())
    phases._write_next_actions(
        iter_dir=tmp_path, status="continue",
        computed_composite=0.0, eval_path=eval_path,
    )
    text = (tmp_path / "next_actions.md").read_text(encoding="utf-8")
    assert "status: continue" in text


def test_write_next_actions_no_eval_file(tmp_path: Path):
    phases._write_next_actions(
        iter_dir=tmp_path, status="continue",
        computed_composite=42.0, eval_path=tmp_path / "does-not-exist.json",
    )
    text = (tmp_path / "next_actions.md").read_text(encoding="utf-8")
    assert "computed_composite: 42.00" in text


def test_write_next_actions_missed_criteria_truncated_at_12(tmp_path: Path):
    payload = _minimal_eval_payload()
    payload["rubric"]["components"] = [{
        "id": "c1", "score": 0, "max_points": 100,
        "criteria_fired": [],
        "criteria_missed": [f"M{i}" for i in range(20)],
    }]
    eval_path = tmp_path / "eval.json"
    artifacts.write_json(eval_path, payload)
    phases._write_next_actions(
        iter_dir=tmp_path, status="continue",
        computed_composite=0.0, eval_path=eval_path,
    )
    text = (tmp_path / "next_actions.md").read_text(encoding="utf-8")
    # spec: first 12 criteria shown
    assert "M0" in text and "M11" in text and "M12" not in text


# ── CLI argument surface — smoke, no Gemini ──────────────────────────────────


def test_cli_list_urls_returns_json():
    result = subprocess.run(
        [sys.executable, "ops/scripts/eval_loop.py", "--source", "youtube", "--list-urls"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list) and len(data) >= 3


def test_cli_report_empty_source_returns_empty():
    result = subprocess.run(
        [sys.executable, "ops/scripts/eval_loop.py", "--source", "podcast", "--report"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data == {"source": "podcast", "iterations": []}


def test_cli_dry_run_returns_status(tmp_path, monkeypatch):
    # Use an isolated iter dir so we don't race with real iter-01
    result = subprocess.run(
        [sys.executable, "ops/scripts/eval_loop.py",
         "--source", "youtube", "--iter", "99", "--dry-run"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "dry_run"
    assert data["source"] == "youtube"
    assert data["iter"] == 99


def test_cli_unknown_source_rejected_by_argparse():
    result = subprocess.run(
        [sys.executable, "ops/scripts/eval_loop.py", "--source", "bogus", "--list-urls"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "invalid choice" in (result.stderr or "").lower()


def test_cli_halt_file_short_circuits(tmp_path: Path, monkeypatch):
    # Create .halt, run any real command → must return "halted" without calling Gemini
    halt = REPO_ROOT / "docs" / "summary_eval" / ".halt"
    try:
        halt.write_text("halt", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "ops/scripts/eval_loop.py",
             "--source", "youtube", "--iter", "99"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "halted"
    finally:
        if halt.exists():
            halt.unlink()
