"""Backfill auth users and core.profiles/workspaces for DB v2."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import run_statements, load_config, parse_args, require_continue, run_async  # noqa: E402


SQL = """
DO $$
BEGIN
    IF to_regclass('public.kg_users') IS NULL THEN
        RAISE NOTICE 'legacy public.kg_users table is absent; skipping profile backfill';
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.kg_users
         WHERE render_user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ) THEN
        RAISE EXCEPTION 'DB v2 profile backfill requires UUID render_user_id values before auth.users seeding';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.kg_users ku
          LEFT JOIN auth.users au ON au.id = ku.render_user_id::uuid
         WHERE au.id IS NULL
    ) THEN
        RAISE EXCEPTION 'DB v2 profile backfill requires auth.users rows to be seeded before core.profiles';
    END IF;
END $$;

INSERT INTO core.profiles (id, display_name, email, avatar_url, allowlist_status, created_at, updated_at)
SELECT ku.render_user_id::uuid,
       ku.display_name,
       ku.email,
       ku.avatar_url,
       'allowed',
       ku.created_at,
       ku.updated_at
  FROM public.kg_users ku
ON CONFLICT (id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    avatar_url = EXCLUDED.avatar_url,
    updated_at = now();
"""


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "profiles backfill")
    if args.dry_run:
        print("profiles backfill ready; execution requires seeded auth.users rows")
        return 0
    return run_async(run_statements(config, SQL))


if __name__ == "__main__":
    raise SystemExit(main())
