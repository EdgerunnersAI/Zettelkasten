"""Backfill core usage accounting tables from legacy pricing usage data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async


SQL = """
DO $$
DECLARE
    month_start date;
    month_end date;
    cursor_month date;
BEGIN
    SELECT date_trunc('month', min(created_at))::date,
           date_trunc('month', max(created_at))::date
      INTO month_start, month_end
      FROM public.pricing_credit_ledger;

    IF month_start IS NULL THEN
        RETURN;
    END IF;

    cursor_month := month_start;
    WHILE cursor_month <= month_end LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS core.%I PARTITION OF core.usage_events FOR VALUES FROM (%L) TO (%L)',
            'usage_events_p' || to_char(cursor_month, 'YYYY_MM'),
            cursor_month::timestamptz,
            (cursor_month + interval '1 month')::timestamptz
        );
        cursor_month := (cursor_month + interval '1 month')::date;
    END LOOP;
END $$;

INSERT INTO core.usage_aggregates (
    workspace_id, profile_id, feature, unit, period_start,
    quantity_total, events_count, updated_at
)
SELECT w.id,
       p.id,
       c.meter,
       c.period_type,
       c.period_start::timestamptz,
       c.used_count,
       c.used_count,
       c.updated_at
  FROM public.pricing_usage_counters c
  JOIN core.profiles p ON p.id::text = c.render_user_id
  JOIN core.workspaces w ON w.owner_profile_id = p.id AND w.is_personal
ON CONFLICT (workspace_id, feature, unit, period_start) DO UPDATE
SET quantity_total = EXCLUDED.quantity_total,
    events_count = EXCLUDED.events_count,
    updated_at = now();

INSERT INTO billing.pricing_entitlement_consumption (
    profile_id, workspace_id, feature, unit, quantity, consumed_at, metadata
)
SELECT p.id,
       w.id,
       c.meter,
       c.period_type,
       c.used_count,
       c.updated_at,
       jsonb_build_object('legacy_counter_id', c.id, 'period_start', c.period_start)
  FROM public.pricing_usage_counters c
  JOIN core.profiles p ON p.id::text = c.render_user_id
  JOIN core.workspaces w ON w.owner_profile_id = p.id AND w.is_personal
 WHERE c.used_count > 0
   AND NOT EXISTS (
       SELECT 1
         FROM billing.pricing_entitlement_consumption existing
        WHERE existing.metadata ->> 'legacy_counter_id' = c.id::text
   );

INSERT INTO core.usage_events (workspace_id, profile_id, feature, unit, quantity, metadata, occurred_at)
SELECT w.id,
       p.id,
       l.meter,
       'credit_delta',
       l.delta,
       jsonb_build_object('legacy_ledger_id', l.id, 'source', l.source, 'source_id', l.source_id),
       l.created_at
  FROM public.pricing_credit_ledger l
  JOIN core.profiles p ON p.id::text = l.render_user_id
  JOIN core.workspaces w ON w.owner_profile_id = p.id AND w.is_personal
 WHERE NOT EXISTS (
       SELECT 1
         FROM core.usage_events existing
        WHERE existing.metadata ->> 'legacy_ledger_id' = l.id::text
 );
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "usage events backfill")
    if args.dry_run:
        print("usage events backfill ready")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
