"""iter-12 T31 R4: daily decay job for Thompson-sampling bandit posteriors.

Applies γ=0.98/day decay to α and β in kg_bandit_posteriors.
GREATEST clamp keeps α≥1, β≥1 so the prior never collapses to a point mass.

Operator runs daily via existing cron infra. DO NOT execute against live
Supabase without operator approval and confirmed migration applied.

Usage (Git Bash / droplet SSH):
    python ops/scripts/bandit_decay_job.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

DECAY_SQL = """
UPDATE kg_bandit_posteriors
   SET seed_alpha        = GREATEST(1.0, seed_alpha * 0.98),
       seed_beta         = GREATEST(1.0, seed_beta  * 0.98),
       seed_last_decay_at = now()
 WHERE seed_arm IS NOT NULL
   AND (seed_last_decay_at IS NULL
        OR seed_last_decay_at < now() - interval '23 hours');
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Bandit posterior decay (γ=0.98/day)")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN — SQL that would execute:")
        print(DECAY_SQL)
        return

    try:
        from supabase import create_client
    except ImportError:
        sys.exit("supabase-py not installed. Run: pip install supabase")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")

    client = create_client(url, key)
    result = client.rpc("execute_sql", {"query": DECAY_SQL}).execute()
    print(f"Decay complete. Result: {result.data}")


if __name__ == "__main__":
    main()
