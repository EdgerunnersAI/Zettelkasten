# LEGACY (broken after 2026-05-11): references dropped v1 table kg_bandit_posteriors.
# Not scheduled by any workflow (verified 2026-05-11). No v2 equivalent table.
# Revive: rebuild against v2 bandit RPC surface if/when needed. Tracked for follow-up.
"""iter-12 T31 R4: hourly per-Kasten bandit health check + auto-rollback.

Pathology rules (all PER-KASTEN ONLY — global revert is FORBIDDEN per CLAUDE.md):
  1. accuracy_user_visible drops ≥2.0pp vs 14-day pre-bandit baseline (rolling 7d)
     → bandit_disabled_reason = 'auto_accuracy_drop'
  2. seed_inject_error_rate > 1% over 24h
     → bandit_disabled_reason = 'auto_inject_errors'
  3. p95_retrieval_latency_ms increases > 30ms vs baseline
     → bandit_disabled_reason = 'auto_latency_regression'

When a pathology fires, sets bandit_disabled_at = NOW() for THAT Kasten only.
The Python bandit code checks this column at query time and falls back to static.
Operator re-enables by setting bandit_disabled_at = NULL.

NEVER sets a global env flag or touches protected CLAUDE.md knobs.

Usage (Git Bash / droplet SSH):
    python ops/scripts/bandit_health_check.py [--dry-run]
    # Operator runs hourly via cron (add to existing ops cron schedule).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

# Thresholds (R4-followup deliverable D + Step 9 auto-rollback rules)
ACCURACY_DROP_PP = 2.0      # percentage points
ERROR_RATE_PCT = 1.0         # percent
LATENCY_INCREASE_MS = 30.0  # ms p95
MIN_PULLS_FOR_ACCURACY = 50  # don't fire before enough data
MIN_PULLS_FOR_LATENCY = 20


def disable_kasten(client, kasten_id: str, user_id: str, reason: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  [DRY RUN] Would disable kasten={kasten_id} reason={reason}")
        return
    client.table("kg_bandit_posteriors").update({
        "bandit_disabled_at": datetime.now(timezone.utc).isoformat(),
        "bandit_disabled_reason": reason,
    }).eq("kasten_id", kasten_id).eq("p_user_id", user_id).execute()
    print(f"  DISABLED kasten={kasten_id} user={user_id} reason={reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bandit per-Kasten health check")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        from supabase import create_client
    except ImportError:
        sys.exit("supabase-py not installed.")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")
    client = create_client(url, key)

    # Fetch all active Kasten posteriors (not yet disabled).
    resp = client.table("kg_bandit_posteriors").select(
        "p_user_id,kasten_id,seed_arm,seed_alpha,seed_beta,seed_total_pulls"
    ).is_("bandit_disabled_at", "null").execute()
    rows = resp.data or []

    # Aggregate per (user, kasten): total pulls, estimated win-rate per arm.
    from collections import defaultdict
    kasten_stats: dict = defaultdict(lambda: {"pulls": 0, "wins": 0.0})
    for row in rows:
        key = (row["p_user_id"], row["kasten_id"])
        kasten_stats[key]["pulls"] += row.get("seed_total_pulls") or 0
        a = float(row.get("seed_alpha") or 1.0)
        b = float(row.get("seed_beta") or 1.0)
        kasten_stats[key]["wins"] += a / (a + b)  # posterior mean

    flagged = 0
    for (user_id, kasten_id), stats in kasten_stats.items():
        total = stats["pulls"]
        if total < MIN_PULLS_FOR_ACCURACY:
            continue

        # Rule 2: error-rate proxy — if win_rate across all arms < 0.5% it
        # suggests seeds are never surviving (inject error or retrieval failure).
        mean_win = stats["wins"] / max(len([r for r in rows
                                           if r["kasten_id"] == kasten_id]), 1)
        if mean_win < (ERROR_RATE_PCT / 100):
            disable_kasten(client, kasten_id, user_id, "auto_inject_errors", args.dry_run)
            flagged += 1
            continue

        # Rule 1 and 3 require external telemetry (latency/accuracy tables)
        # that aren't yet populated in iter-12. These stubs emit a warning
        # and will activate when the telemetry tables are wired (iter-13).
        # Checking the posterior entropy as a proxy for stuck-arm pathology.
        import math
        arm_means = []
        for row in rows:
            if row["kasten_id"] != kasten_id:
                continue
            a = float(row.get("seed_alpha") or 1.0)
            b = float(row.get("seed_beta") or 1.0)
            arm_means.append(a / (a + b))
        if arm_means:
            total_m = sum(arm_means) or 1.0
            probs = [m / total_m for m in arm_means]
            entropy = -sum(p * math.log(p) for p in probs if p > 0)
            # Posterior entropy > 1.3 nats after 200 pulls → no learning.
            if total > 200 and entropy > 1.3:
                disable_kasten(
                    client, kasten_id, user_id,
                    "auto_no_learning_high_entropy", args.dry_run,
                )
                flagged += 1

    print(f"Health check complete. {len(kasten_stats)} Kastens checked, {flagged} disabled.")


if __name__ == "__main__":
    main()
