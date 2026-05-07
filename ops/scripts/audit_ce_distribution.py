"""iter-12 Task 35: audit CE-score distributions and produce iter-13 empirical gate decision.

Usage:
    python ops/scripts/audit_ce_distribution.py --results-dir ops/eval_results/iter-12

Reads verification_results.json (JSONL), groups ce_score_distribution log lines
per kasten, and emits one of three decisions for iter-13 plan authors:
  ACTIVATE_A1_PER_KASTEN_FLOOR    — spread too large / min-kasten p70 too low
  CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT — all kastens cluster near global floor
  DEFER_TO_ITER_14_INSUFFICIENT_DATA    — fewer than 50 samples in any kasten
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Gate thresholds (advisory, not runtime-enforced).
_MIN_SAMPLES_PER_KASTEN = 50
_SPREAD_THRESHOLD = 0.15   # max(p70) - min(p70) triggers ACTIVATE when exceeded
_LOW_FLOOR_THRESHOLD = 0.60  # any kasten with p70 < this triggers ACTIVATE


def _p70(scores: list[float]) -> float:
    """70th-percentile of a sorted list."""
    if not scores:
        return 0.0
    asc = sorted(scores)
    return asc[int(0.7 * len(asc))] if len(asc) >= 4 else asc[-1]


def iter_13_a1_gate(samples: dict[str, list[float]]) -> str:
    """Evaluate per-kasten CE-score samples and return a gate decision string.

    Args:
        samples: {kasten_id: [ce_score, ...]} with at least 1 score each.
    """
    if any(len(v) < _MIN_SAMPLES_PER_KASTEN for v in samples.values()):
        return "DEFER_TO_ITER_14_INSUFFICIENT_DATA"

    p70s = {k: _p70(v) for k, v in samples.items()}
    spread = max(p70s.values()) - min(p70s.values())
    min_p70 = min(p70s.values())

    if spread > _SPREAD_THRESHOLD or min_p70 < _LOW_FLOOR_THRESHOLD:
        return "ACTIVATE_A1_PER_KASTEN_FLOOR"
    return "CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT"


def _load_samples_from_jsonl(path: Path) -> dict[str, list[float]]:
    """Parse verification_results.json (one JSON object per line) for CE fields."""
    samples: dict[str, list[float]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            kasten = row.get("kasten_id") or row.get("sandbox_id") or "unknown"
            score = row.get("top1_ce_score") or row.get("rerank_score")
            if score is not None:
                samples.setdefault(kasten, []).append(float(score))
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default="ops/eval_results/iter-12",
                        help="Directory containing verification_results.json")
    args = parser.parse_args(argv)

    results_path = Path(args.results_dir) / "verification_results.json"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found", file=sys.stderr)
        return 1

    samples = _load_samples_from_jsonl(results_path)
    if not samples:
        print("DEFER_TO_ITER_14_INSUFFICIENT_DATA  (no CE score rows found)")
        return 0

    decision = iter_13_a1_gate(samples)
    p70s = {k: _p70(v) for k, v in samples.items()}
    print(f"Decision: {decision}")
    print(f"Kastens: {len(samples)}  samples/kasten: { {k: len(v) for k, v in samples.items()} }")
    print(f"p70 per kasten: { {k: round(v, 4) for k, v in p70s.items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
