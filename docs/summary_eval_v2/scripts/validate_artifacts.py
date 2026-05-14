"""Validate summary_eval_v2 source folders and core artifacts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docs.summary_eval_v2.scripts._common import EVAL_ROOT, SOURCES, iter_dir, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iter", type=int, default=1, dest="iter_num")
    args = parser.parse_args()

    missing: list[str] = []
    for source in SOURCES:
        path = iter_dir(source, args.iter_num)
        if not path.exists():
            missing.append(str(path))
    for script in (
        "baseline_score.py",
        "run_iter.py",
        "post_iter.py",
        "naruto_write.py",
        "validate_artifacts.py",
    ):
        path = EVAL_ROOT / "scripts" / script
        if not path.exists():
            missing.append(str(path))

    payload = {"iter": args.iter_num, "ok": not missing, "missing": missing}
    write_json(EVAL_ROOT / "validation.json", payload)
    if missing:
        print("\n".join(missing))
        return 1
    print(str(EVAL_ROOT / "validation.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
