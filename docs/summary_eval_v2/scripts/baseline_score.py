"""Create the baseline_score.json artifact for a summary_eval_v2 iteration."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docs.summary_eval_v2.scripts._common import (  # noqa: E402
    extract_composite,
    extract_final_scorecard_composite,
    extract_rubric_total,
    iter_dir,
    latest_baseline,
    read_json,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--iter", required=True, type=int, dest="iter_num")
    args = parser.parse_args()

    baseline_dir = latest_baseline(args.source, args.iter_num)
    out_dir = iter_dir(args.source, args.iter_num)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict = {
        "source": args.source,
        "iter": args.iter_num,
        "baseline_dir": str(baseline_dir) if baseline_dir else None,
        "baseline_kind": "legacy_summary_eval" if args.iter_num <= 1 else "summary_eval_v2",
        "composite_score": None,
        "notes": [],
    }
    if baseline_dir is None:
        payload["notes"].append("No prior baseline found; iter becomes first measured baseline.")
    else:
        if args.iter_num <= 1:
            final_scorecard = baseline_dir.parent / "final_scorecard.md"
            final_score = extract_final_scorecard_composite(final_scorecard)
            if final_score is not None:
                payload["composite_score"] = final_score
                payload["score_file"] = str(final_scorecard)
        score_files = [
            baseline_dir / "scorecard.json",
            baseline_dir / "eval.json",
        ]
        if payload["composite_score"] is None:
            for score_file in score_files:
                if score_file.exists():
                    try:
                        parsed = read_json(score_file)
                        payload["composite_score"] = (
                            extract_composite(parsed) or extract_rubric_total(parsed)
                        )
                        payload["score_file"] = str(score_file)
                        break
                    except Exception as exc:
                        payload["notes"].append(f"Could not parse {score_file.name}: {exc}")
        if payload["composite_score"] is None:
            payload["notes"].append("Baseline exists but no parseable composite_score was found.")

    write_json(out_dir / "baseline_score.json", payload)
    print(str(out_dir / "baseline_score.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
