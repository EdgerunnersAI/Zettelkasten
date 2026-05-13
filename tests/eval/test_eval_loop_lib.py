"""Unit tests for ops/scripts/lib modules used by eval_loop.py.

Scope (pure-unit, no Gemini, no network):
- state_detector.detect_iteration_state — all four states
- churn_ledger.record + churning_files — detection logic, window edges
- artifacts helpers — url_slug determinism, JSON round-trip, iter_artifact_paths keys
- phases._divergence_stamp — band boundaries
- phases._composite_from_eval_file / _composite_from_iter_dir — single + list eval.json, held_out layout
- phases._write_diff / _write_next_actions — deterministic markdown output
- phases.run_phase_b — happy path, missing review, blind-review violation (with stubbed git)
- eval_loop._urls_for_iter — LOOP_URL_COUNTS lookup + held-out branching
- git_helper — commit no-op, commit with staged content (temp repo)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ops.scripts.lib import artifacts, churn_ledger, git_helper, phases
from ops.scripts.lib.state_detector import IterationState, detect_iteration_state
from website.features.summarization_engine.evaluator.consolidated import (
    evaluator_implementation_fingerprint,
    rubric_sha256,
)
from website.features.summarization_engine.evaluator.prompts import PROMPT_VERSION


# ── state_detector ───────────────────────────────────────────────────────────


def test_state_detector_returns_phase_a_when_empty(tmp_path: Path):
    assert detect_iteration_state(tmp_path) == IterationState.PHASE_A_REQUIRED


def test_state_detector_returns_awaiting_review_when_prompt_present(tmp_path: Path):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "eval.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manual_review_prompt.md").write_text("prompt", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.AWAITING_MANUAL_REVIEW


def test_state_detector_returns_phase_b_when_review_present(tmp_path: Path):
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "eval.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manual_review_prompt.md").write_text("prompt", encoding="utf-8")
    (tmp_path / "manual_review.md").write_text("review", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.PHASE_B_REQUIRED


def test_state_detector_returns_committed_when_diff_present(tmp_path: Path):
    (tmp_path / "diff.md").write_text("diff", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.ALREADY_COMMITTED


def test_state_detector_returns_awaiting_review_for_held_out_layout(tmp_path: Path):
    (tmp_path / "held_out").mkdir()
    (tmp_path / "aggregate.md").write_text("aggregate", encoding="utf-8")
    (tmp_path / "manual_review_prompt.md").write_text("prompt", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.AWAITING_MANUAL_REVIEW


def test_state_detector_returns_phase_b_for_held_out_layout(tmp_path: Path):
    (tmp_path / "held_out").mkdir()
    (tmp_path / "aggregate.md").write_text("aggregate", encoding="utf-8")
    (tmp_path / "manual_review_prompt.md").write_text("prompt", encoding="utf-8")
    (tmp_path / "manual_review.md").write_text("review", encoding="utf-8")
    assert detect_iteration_state(tmp_path) == IterationState.PHASE_B_REQUIRED


# ── churn_ledger ─────────────────────────────────────────────────────────────


def test_churn_ledger_record_deduplicates_per_iter(tmp_path: Path):
    churn_ledger.record(
        tmp_path, iter_num=2, files=["a.py"],
        targeted_criterion="c1", criterion_delta=0.5, composite_delta=1.0,
    )
    churn_ledger.record(
        tmp_path, iter_num=2, files=["b.py"],
        targeted_criterion="c2", criterion_delta=0.6, composite_delta=1.1,
    )
    entries = churn_ledger.load(tmp_path)
    assert len(entries) == 1
    assert entries[0].files == ["b.py"]
    assert entries[0].targeted_criterion == "c2"


def test_churn_ledger_churning_files_detects_stagnation(tmp_path: Path):
    for i in range(1, 4):
        churn_ledger.record(
            tmp_path, iter_num=i, files=["auth.py"],
            targeted_criterion="brief.thesis", criterion_delta=0.2, composite_delta=0.1,
        )
    # iter 4 now looks back at iters 1-3; file edited 3× with combined |0.6| < 1.0
    assert "auth.py" in churn_ledger.churning_files(tmp_path, current_iter=4, lookback=3)


def test_churn_ledger_churning_skipped_when_window_incomplete(tmp_path: Path):
    # Missing iter 2 breaks the consecutive-window requirement.
    for i in (1, 3):
        churn_ledger.record(
            tmp_path, iter_num=i, files=["auth.py"],
            targeted_criterion="c", criterion_delta=0.1, composite_delta=0.0,
        )
    assert churn_ledger.churning_files(tmp_path, current_iter=4, lookback=3) == []


def test_churn_ledger_not_churning_when_delta_large(tmp_path: Path):
    for i in range(1, 4):
        churn_ledger.record(
            tmp_path, iter_num=i, files=["auth.py"],
            targeted_criterion="c", criterion_delta=2.0, composite_delta=2.0,
        )
    # combined |6.0| >= 1.0 → not churning
    assert churn_ledger.churning_files(tmp_path, current_iter=4, lookback=3) == []


# ── artifacts ────────────────────────────────────────────────────────────────


def test_url_slug_is_deterministic_and_prefix_length():
    a = artifacts.url_slug("https://example.com/foo")
    b = artifacts.url_slug("https://example.com/foo")
    c = artifacts.url_slug("https://example.com/bar")
    assert a == b and a != c and len(a) == 16


def test_write_and_read_json_round_trip(tmp_path: Path):
    target = tmp_path / "nested" / "payload.json"
    artifacts.write_json(target, {"k": [1, 2], "p": Path("/tmp")})
    assert target.exists()
    data = artifacts.read_json(target)
    assert data["k"] == [1, 2]


def test_iter_artifact_paths_contains_required_keys(tmp_path: Path):
    keys = artifacts.iter_artifact_paths(tmp_path).keys()
    required = {
        "input", "summary", "eval", "source_text", "atomic_facts",
        "prompt", "review", "diff", "next_actions", "log",
        "held_out", "aggregate", "new_angle", "regression", "disagreement",
    }
    assert required.issubset(set(keys))


# ── phases._divergence_stamp ─────────────────────────────────────────────────


@pytest.mark.parametrize("diff,expected", [
    (0.0, "AGREEMENT"),
    (5.0, "AGREEMENT"),
    (5.01, "MINOR_DISAGREEMENT"),
    (10.0, "MINOR_DISAGREEMENT"),
    (10.01, "MAJOR_DISAGREEMENT"),
    (50.0, "MAJOR_DISAGREEMENT"),
])
def test_divergence_stamp_bands(diff, expected):
    assert phases._divergence_stamp(diff) == expected


# ── phases composite from fake eval.json ─────────────────────────────────────


def _canned_eval_payload(rubric_total: float = 70.0, faithfulness: float = 0.9) -> dict:
    return {
        "g_eval": {
            "coherence": 4.0, "consistency": 4.0, "fluency": 4.0, "relevance": 4.0,
            "reasoning": "",
        },
        "finesure": {
            "faithfulness": {"score": faithfulness, "items": []},
            "completeness": {"score": 0.85, "items": []},
            "conciseness": {"score": 0.9, "items": []},
        },
        "summac_lite": {"score": 0.95, "contradicted_sentences": [], "neutral_sentences": []},
        "rubric": {
            "components": [
                {"id": "brief.thesis", "score": 15, "max_points": 20,
                 "criteria_fired": [], "criteria_missed": ["T1"]},
                {"id": "body.coverage", "score": 55, "max_points": 80,
                 "criteria_fired": [], "criteria_missed": ["B1"]},
            ],
            "caps_applied": {
                "hallucination_cap": None,
                "omission_cap": None,
                "generic_cap": None,
            },
            "anti_patterns_triggered": [],
        },
        "maps_to_metric_summary": {},
        "editorialization_flags": [],
        "evaluator_metadata": {},
    }


def test_composite_from_eval_file_single(tmp_path: Path):
    path = tmp_path / "eval.json"
    artifacts.write_json(path, _canned_eval_payload())
    score = phases._composite_from_eval_file(path)
    # W3 baseline: g_eval is anchored-ternary {1,2,3}. Bare float 4.0 in the
    # fixture coerces to score=3 for both coh+flu (n > 3.5 → 3), so
    # g_eval_aggregate = (3+3)/6*100 = 100.
    # 0.6*70 + 0.2*90 + 0.1*85 + 0.1*100 = 42 + 18 + 8.5 + 10 = 78.5
    assert score == pytest.approx(78.5, abs=0.05)


def test_composite_from_eval_file_list(tmp_path: Path):
    path = tmp_path / "eval.json"
    payloads = [
        {"url": "https://a", "eval": _canned_eval_payload()},
        {"url": "https://b", "eval": _canned_eval_payload(faithfulness=0.7)},
    ]
    artifacts.write_json(path, payloads)
    score = phases._composite_from_eval_file(path)
    # W3 baseline (g_eval ternary, coh+flu both 3 → g_eval_agg=100):
    # first:  0.6*70 + 0.2*90 + 0.1*85 + 0.1*100 = 78.5
    # second: 0.6*70 + 0.2*70 + 0.1*85 + 0.1*100 = 74.5
    # avg(78.5, 74.5) = 76.5
    assert score == pytest.approx(76.5, abs=0.05)


def test_composite_from_iter_dir_uses_held_out_when_no_top_eval(tmp_path: Path):
    held_out = tmp_path / "held_out"
    for slug, faith in [("aaa", 0.9), ("bbb", 0.7)]:
        d = held_out / slug
        d.mkdir(parents=True)
        artifacts.write_json(d / "eval.json", _canned_eval_payload(faithfulness=faith))
    score = phases._composite_from_iter_dir(tmp_path)
    # W3 baseline (see test_composite_from_eval_file_list): avg(78.5, 74.5) = 76.5
    assert score == pytest.approx(76.5, abs=0.05)


def test_composite_from_iter_dir_empty_returns_zero(tmp_path: Path):
    assert phases._composite_from_iter_dir(tmp_path) == 0.0


def test_run_determinism_check_skips_reval_when_fingerprint_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    iter_dir = tmp_path / "iter-02"
    iter_dir.mkdir()
    (iter_dir / "summary.json").write_text("{}", encoding="utf-8")
    (iter_dir / "source_text.md").write_text("source", encoding="utf-8")
    artifacts.write_json(iter_dir / "atomic_facts.json", [{"facts": []}])

    rubric_yaml = {
        "version": "rubric_youtube.v1",
        "source_type": "youtube",
        "composite_max_points": 100,
        "components": [],
    }
    rubric_path = tmp_path / "rubric.yaml"
    rubric_path.write_text(
        "version: rubric_youtube.v1\nsource_type: youtube\ncomposite_max_points: 100\ncomponents: []\n",
        encoding="utf-8",
    )

    payload = _canned_eval_payload()
    payload["evaluator_metadata"] = {
        "prompt_version": PROMPT_VERSION,
        "implementation_fingerprint": evaluator_implementation_fingerprint(),
        "rubric_sha256": rubric_sha256(rubric_yaml),
    }
    artifacts.write_json(iter_dir / "eval.json", payload)

    monkeypatch.setattr(
        phases,
        "_re_evaluate_from_summary",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not re-evaluate")),
    )

    result = phases.run_determinism_check(
        source="youtube",
        prev_iter_dir=iter_dir,
        rubric_path=rubric_path,
        gemini_client_factory=lambda: None,
    )

    assert result["status"] == "stable_same_fingerprint"


# ── phases._write_diff / _write_next_actions (deterministic) ─────────────────


def test_write_diff_emits_all_fields(tmp_path: Path):
    phases._write_diff(
        iter_dir=tmp_path, prev_dir=None,
        computed_composite=72.5, estimated_composite=70.0, stamp="AGREEMENT",
    )
    text = (tmp_path / "diff.md").read_text(encoding="utf-8")
    assert "computed_composite: 72.50" in text
    assert "estimated_composite: 70.00" in text
    assert "divergence: AGREEMENT" in text
    assert "score_delta_vs_prev: n/a" in text


def test_write_next_actions_lists_lowest_components(tmp_path: Path):
    eval_path = tmp_path / "eval.json"
    artifacts.write_json(eval_path, _canned_eval_payload())
    phases._write_next_actions(
        iter_dir=tmp_path, status="continue",
        computed_composite=76.5, eval_path=eval_path,
    )
    text = (tmp_path / "next_actions.md").read_text(encoding="utf-8")
    assert "status: continue" in text
    assert "brief.thesis: 15/20" in text
    assert "T1" in text


# ── eval_loop._urls_for_iter ─────────────────────────────────────────────────


def test_urls_for_iter_respects_loop_url_counts(monkeypatch: pytest.MonkeyPatch):
    from ops.scripts import eval_loop

    fake_urls = [f"https://x/{i}" for i in range(10)]
    monkeypatch.setattr(eval_loop, "_links_by_source", lambda: {"youtube": fake_urls})

    assert eval_loop._urls_for_iter("youtube", 1, None) == (fake_urls[:1], False)
    assert eval_loop._urls_for_iter("youtube", 4, None) == (fake_urls[:2], False)
    assert eval_loop._urls_for_iter("youtube", 5, None) == (fake_urls[:3], False)
    held, held_out_flag = eval_loop._urls_for_iter("youtube", 6, None)
    assert held == fake_urls[3:]
    assert held_out_flag is True


def test_urls_for_iter_limits_reddit_held_out_to_plan_defined_single_url(
    monkeypatch: pytest.MonkeyPatch,
):
    from ops.scripts import eval_loop

    fake_urls = [f"https://reddit.com/{i}" for i in range(6)]
    monkeypatch.setattr(eval_loop, "_links_by_source", lambda: {"reddit": fake_urls})

    held, held_out_flag = eval_loop._urls_for_iter("reddit", 6, None)

    assert held == [fake_urls[3]]
    assert held_out_flag is True


def test_urls_for_iter_override_wins(monkeypatch: pytest.MonkeyPatch):
    from ops.scripts import eval_loop
    monkeypatch.setattr(eval_loop, "_links_by_source", lambda: {"youtube": ["a", "b"]})
    urls, held_out = eval_loop._urls_for_iter("youtube", 1, ["https://manual"])
    assert urls == ["https://manual"] and held_out is False


def test_apply_eval_key_pool_overrides_sets_failfast_defaults(
    monkeypatch: pytest.MonkeyPatch,
):
    from ops.scripts import eval_loop

    for name in (
        "GEMINI_KEY_ROLE_FILTER",
        "GEMINI_MAX_RETRIES",
        "GEMINI_RATE_LIMIT_COOLDOWN_SECS",
        "GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        eval_loop._apply_eval_key_pool_overrides("github")

        assert os.environ["GEMINI_KEY_ROLE_FILTER"] == "billing"
        assert os.environ["GEMINI_MAX_RETRIES"] == "1"
        assert os.environ["GEMINI_RATE_LIMIT_COOLDOWN_SECS"] == "75"
        assert os.environ["GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS"] == "1"
    finally:
        for name in (
            "GEMINI_KEY_ROLE_FILTER",
            "GEMINI_MAX_RETRIES",
            "GEMINI_RATE_LIMIT_COOLDOWN_SECS",
            "GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS",
        ):
            os.environ.pop(name, None)


# ── git_helper (isolated temp repo) ──────────────────────────────────────────


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_helper_commit_noop_when_nothing_staged(tmp_path: Path):
    _init_git_repo(tmp_path)
    # empty repo → no HEAD; create initial empty commit so branch exists
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=tmp_path, check=True)
    sha = git_helper.commit(tmp_path, "test: noop")
    assert sha == ""


def test_git_helper_commit_creates_new_sha(tmp_path: Path):
    _init_git_repo(tmp_path)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "hello.txt"
    target.write_text("hi", encoding="utf-8")
    git_helper.add_paths(tmp_path, [target])
    sha = git_helper.commit(tmp_path, "test: add hello")
    assert sha and len(sha) >= 7


# ── phases.run_phase_b (integration against stubbed review/eval) ─────────────


_MANUAL_REVIEW_OK = '''# manual review

eval_json_hash_at_review: "NOT_CONSULTED"
estimated_composite: 75.0

## Notes
looks fine.
'''

_MANUAL_REVIEW_NOT_STAMPED = '''# manual review

eval_json_hash_at_review: "abc123"
estimated_composite: 70.0
'''


def test_run_phase_b_missing_review(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"
    iter_dir.mkdir()
    repo = _init_git_repo(tmp_path)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo, check=True)
    result = phases.run_phase_b(
        source="youtube", iter_num=1, iter_dir=iter_dir, prev_dir=None,
        repo_root=repo, allow_commit=False,
    )
    assert result["status"] == "missing_manual_review"


def test_run_phase_b_blind_review_violation(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"
    iter_dir.mkdir()
    (iter_dir / "manual_review.md").write_text(_MANUAL_REVIEW_NOT_STAMPED, encoding="utf-8")
    result = phases.run_phase_b(
        source="youtube", iter_num=1, iter_dir=iter_dir, prev_dir=None,
        repo_root=tmp_path, allow_commit=False,
    )
    assert result["status"] == "blind_review_violation"


def test_run_phase_b_happy_path(tmp_path: Path):
    repo = _init_git_repo(tmp_path)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo, check=True)
    source_dir = tmp_path / "docs" / "summary_eval" / "youtube"
    iter_dir = source_dir / "iter-01"
    iter_dir.mkdir(parents=True)
    artifacts.write_json(iter_dir / "eval.json", _canned_eval_payload())
    (iter_dir / "summary.json").write_text("{}", encoding="utf-8")
    (iter_dir / "manual_review.md").write_text(_MANUAL_REVIEW_OK, encoding="utf-8")

    result = phases.run_phase_b(
        source="youtube", iter_num=1, iter_dir=iter_dir, prev_dir=None,
        repo_root=repo, allow_commit=False,
    )
    assert result["status"] == "continue"
    assert result["divergence"] == "AGREEMENT"
    assert (iter_dir / "diff.md").exists()
    assert (iter_dir / "next_actions.md").exists()
    # churn ledger row recorded for this iter
    entries = churn_ledger.load(source_dir)
    assert any(entry.iter == 1 for entry in entries)


# ── phase_a idempotency (W7) ────────────────────────────────────────────────


def test_phase_a_complete_detects_done_line(tmp_path: Path):
    """The phase_a-done marker emitted at end of run_phase_a is what powers
    the W7 short-circuit. Make sure detection is tight."""
    log = tmp_path / "run.log"
    # Missing file is not "complete".
    assert phases._phase_a_complete(log) is False

    log.write_text("phase_a start source=foo iter=1\n", encoding="utf-8")
    assert phases._phase_a_complete(log) is False

    log.write_text(
        "phase_a start ...\n[ts] phase_a done eval_hash=abc123def456\n",
        encoding="utf-8",
    )
    assert phases._phase_a_complete(log) is True


def test_run_phase_a_force_false_returns_cached_payload(tmp_path: Path):
    """When a prior run has logged 'phase_a done', re-invoking run_phase_a
    must short-circuit and return a cached payload WITHOUT calling the
    Gemini factory."""
    iter_dir = tmp_path / "docs" / "summary_eval" / "youtube" / "iter-01"
    iter_dir.mkdir(parents=True)
    paths = artifacts.iter_artifact_paths(iter_dir)
    paths["log"].write_text(
        "[2026-05-13T00:00:00Z] phase_a start\n"
        "[2026-05-13T00:01:00Z] phase_a done eval_hash=deadbeef0123\n",
        encoding="utf-8",
    )
    artifacts.write_json(paths["input"], {
        "urls": ["https://example.com/a"],
        "held_out": True,
        "records": [{"url": "https://example.com/a", "composite": 72.5}],
    })
    paths["prompt"].write_text("prompt", encoding="utf-8")

    factory_calls: list[int] = []

    def _factory():
        factory_calls.append(1)
        raise RuntimeError("factory should not be called when cached")

    result = phases.run_phase_a(
        source="youtube",
        iter_num=1,
        urls=["https://example.com/a"],
        iter_dir=iter_dir,
        rubric_path=tmp_path / "rubric.yaml",
        cache_root=tmp_path / "_cache",
        gemini_client_factory=_factory,
        held_out=True,
        env="dev",
        force=False,
    )
    assert factory_calls == []
    assert result["status"] == "awaiting_manual_review"
    assert result.get("cached") is True
    assert result["composite_mean"] == pytest.approx(72.5)
    assert result["urls"] == ["https://example.com/a"]


def test_run_phase_a_force_true_bypasses_cache(tmp_path: Path):
    """force=True must always re-run — confirmed by observing the factory
    being invoked (we make it raise to abort before real work)."""
    iter_dir = tmp_path / "docs" / "summary_eval" / "youtube" / "iter-01"
    iter_dir.mkdir(parents=True)
    paths = artifacts.iter_artifact_paths(iter_dir)
    paths["log"].write_text(
        "[2026-05-13T00:01:00Z] phase_a done eval_hash=deadbeef0123\n",
        encoding="utf-8",
    )

    factory_calls: list[int] = []

    class _ForceRun(RuntimeError):
        pass

    def _factory():
        factory_calls.append(1)
        raise _ForceRun("factory invoked — confirms bypass")

    with pytest.raises(_ForceRun):
        phases.run_phase_a(
            source="youtube",
            iter_num=1,
            urls=["https://example.com/a"],
            iter_dir=iter_dir,
            rubric_path=tmp_path / "rubric.yaml",
            cache_root=tmp_path / "_cache",
            gemini_client_factory=_factory,
            held_out=True,
            env="dev",
            force=True,
        )
    assert factory_calls == [1]
