# LEGACY (broken after 2026-05-11): references dropped v1 table kg_bandit_posteriors.
# One-shot warm-start backfill — not scheduled by any workflow (verified 2026-05-11).
# Revive: rebuild against v2 bandit RPC surface if/when needed. Tracked for follow-up.
"""iter-12 T31 R4: one-shot warm-start backfill for bandit posteriors.

Reads last 30 days of static-0.30 anchor-seed outcomes from the droplet logs
(lines matching "anchor_seed_reward") and seeds kg_bandit_posteriors with
an informative Beta prior per (user, kasten, pool_bucket, arm).

Prior formula (R4-followup mod 3):
  total_mass = 4  (concentrates prior without over-committing)
  prior_alpha = 1 + 4 * s / (s + f)
  prior_beta  = 1 + 4 * f / (s + f)
All four arms receive the same warm-start prior because we only have aggregate
performance for static 0.30 — not per-arm history.

Operator runs ONCE before the first bandit arm sample. DO NOT re-run after
live bandit data starts accumulating (it would overwrite learned posteriors).

Usage (Git Bash / droplet SSH):
    python ops/scripts/bandit_warm_start.py --log-file /path/to/app.log [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Regex for structured log line emitted by hybrid.py when a seed is injected
# and the reward is recorded: "anchor_seed_reward ... survived=True|False"
_REWARD_RE = re.compile(
    r"anchor_seed_reward.*?kasten_id=(?P<kasten>[^\s]+).*?"
    r"user=(?P<user>[^\s]+).*?"
    r"bucket=(?P<bucket>[SM|L]+).*?"
    r"survived=(?P<survived>True|False)"
)
# fallback: plain survived=True/False with kasten_id
_SIMPLE_RE = re.compile(
    r"kasten_id=(?P<kasten>[^\s]+).*?bucket=(?P<bucket>[SML]).*?survived=(?P<survived>True|False)"
)

ARMS = [0.25, 0.30, 0.35, 0.40]
TOTAL_MASS = 4.0
CUTOFF_DAYS = 30


def parse_log(path: str) -> dict:
    """Return {(user, kasten, bucket): (survivors, total)} from log lines."""
    counts: dict = defaultdict(lambda: [0, 0])  # [survivors, total]
    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _REWARD_RE.search(line) or _SIMPLE_RE.search(line)
            if not m:
                continue
            kasten = m.group("kasten")
            bucket = m.group("bucket")
            survived = m.group("survived") == "True"
            user = m.groupdict().get("user", "unknown")
            key = (user, kasten, bucket)
            counts[key][1] += 1
            if survived:
                counts[key][0] += 1
    return dict(counts)


def compute_prior(survivors: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 1.0, 1.0
    win_rate = survivors / total
    alpha = 1.0 + TOTAL_MASS * win_rate
    beta = 1.0 + TOTAL_MASS * (1.0 - win_rate)
    return round(alpha, 4), round(beta, 4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bandit warm-start backfill")
    parser.add_argument("--log-file", required=True, help="Path to app log file")
    parser.add_argument("--dry-run", action="store_true", help="Print upserts without executing")
    args = parser.parse_args()

    counts = parse_log(args.log_file)
    print(f"Parsed {len(counts)} (user, kasten, bucket) groups from {args.log_file}")

    rows = []
    for (user, kasten, bucket), (s, t) in counts.items():
        alpha, beta = compute_prior(s, t)
        for arm in ARMS:
            rows.append({
                "p_user_id": user,
                "kasten_id": kasten,
                "seed_arm": arm,
                "seed_pool_bucket": bucket,
                "seed_alpha": alpha,
                "seed_beta": beta,
                "seed_total_pulls": t,
            })

    if args.dry_run:
        print(f"DRY RUN — {len(rows)} rows would be upserted:")
        for r in rows[:10]:
            print(json.dumps(r))
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        return

    try:
        from supabase import create_client
    except ImportError:
        sys.exit("supabase-py not installed.")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")
    client = create_client(url, key)
    # Batch upsert in chunks of 100.
    for i in range(0, len(rows), 100):
        batch = rows[i : i + 100]
        client.table("kg_bandit_posteriors").upsert(
            batch,
            on_conflict="p_user_id,kasten_id,seed_arm,seed_pool_bucket",
        ).execute()
    print(f"Warm-start complete: {len(rows)} rows upserted.")


if __name__ == "__main__":
    main()
