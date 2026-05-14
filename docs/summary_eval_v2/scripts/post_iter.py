"""Collect summary_eval_v2 post-iteration metrics and next-action hints."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docs.summary_eval_v2.scripts._common import (  # noqa: E402
    extract_composite,
    extract_rubric_total,
    iter_dir,
    read_json,
    write_json,
)


def _load_eval_score(path: Path) -> float | None:
    if not path.exists():
        return None
    payload = read_json(path)
    return extract_composite(payload) or extract_rubric_total(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--iter", required=True, type=int, dest="iter_num")
    args = parser.parse_args()

    out_dir = iter_dir(args.source, args.iter_num)
    baseline_path = out_dir / "baseline_score.json"
    eval_path = out_dir / "eval.json"
    scorecard_path = out_dir / "scorecard.json"

    baseline = read_json(baseline_path) if baseline_path.exists() else {}
    composite = _load_eval_score(scorecard_path) or _load_eval_score(eval_path)
    baseline_score = baseline.get("composite_score")
    delta = None
    if isinstance(composite, (int, float)) and isinstance(baseline_score, (int, float)):
        delta = round(float(composite) - float(baseline_score), 3)

    status = "missing_eval"
    if isinstance(composite, (int, float)):
        if composite >= 90:
            status = "soft_target_met"
        elif composite >= 80:
            status = "hard_target_met"
        else:
            status = "below_hard_target"

    payload = {
        "source": args.source,
        "iter": args.iter_num,
        "status": status,
        "composite_score": composite,
        "baseline_score": baseline_score,
        "delta": delta,
        "checks": {
            "summary_json": (out_dir / "summary.json").exists(),
            "eval_json": eval_path.exists(),
            "baseline_score_json": baseline_path.exists(),
            "manual_review_prompt": (out_dir / "manual_review_prompt.md").exists(),
        },
        "next_actions": [],
    }
    if status == "missing_eval":
        payload["next_actions"].append("Run run_iter.py Phase A or inspect run_result.json for failure.")
    elif status == "below_hard_target":
        payload["next_actions"].append("Apply research loop to the weakest eval subsignal before rerun.")
    elif status == "hard_target_met":
        payload["next_actions"].append("Continue source-specific hardening toward the soft target.")

    write_json(out_dir / "post_iter.json", payload)
    print(str(out_dir / "post_iter.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
