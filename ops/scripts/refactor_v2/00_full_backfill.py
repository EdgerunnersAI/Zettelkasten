"""Run the DB v2 backfill plan with per-step gates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import load_config, parse_args, require_continue  # noqa: E402

STEPS = [
    "01_backfill_profiles.py",
    "02_backfill_canonical_content.py",
    "03_backfill_kg.py",
    "04_backfill_rag.py",
    "05_backfill_pipelines.py",
    "06_backfill_billing.py",
    "07_backfill_usage.py",
    "08_recompute_signal_weights.py",
    "verify_backfill.py",
]


def main() -> int:
    args = parse_args(__doc__ or "")
    load_config(dry_run=args.dry_run)
    if not args.dry_run:
        require_continue(args, "full backfill")

    script_dir = Path(__file__).resolve().parent
    for step in STEPS:
        cmd = [sys.executable, str(script_dir / step)]
        if args.dry_run:
            cmd.append("--dry-run")
        else:
            cmd.append("--continue")
        rc = subprocess.call(cmd, cwd=ROOT)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
