"""Verify DB v2 backfill invariants before HNSW build/cutover."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.scripts.refactor_v2.lib import assert_zero, run_statements, load_config, parse_args, require_continue, run_async  # noqa: E402


COUNT_SQL = """
DO $$
BEGIN
    IF (
        SELECT COUNT(DISTINCT (w.id, cz.id))
          FROM public.kg_nodes n
          JOIN public.kg_users ku ON ku.id = n.user_id
          JOIN core.workspaces w ON w.owner_profile_id = ku.render_user_id::uuid AND w.is_personal
          JOIN content.canonical_zettels cz
            ON cz.normalized_url = n.url
           AND cz.content_hash = digest(coalesce(n.url, '') || E'\n' || coalesce(n.summary, ''), 'sha256')
    ) <>
       (SELECT COUNT(*) FROM content.workspace_zettels WHERE added_via = 'migration') THEN
        RAISE EXCEPTION 'workspace_zettels migration count does not match expected legacy workspace/canonical pairs';
    END IF;

    IF (SELECT COUNT(*) FROM public.rag_sandboxes) <>
       (SELECT COUNT(*) FROM rag.kastens) THEN
        RAISE EXCEPTION 'rag.kastens count does not match legacy rag_sandboxes count';
    END IF;

    IF (SELECT COUNT(*) FROM public.pricing_subscriptions) <>
       (SELECT COUNT(*) FROM billing.pricing_subscriptions) THEN
        RAISE EXCEPTION 'pricing_subscriptions count does not match legacy count';
    END IF;
END $$;
"""


CHECKS = [
    (
        """
        SELECT wcm.*
          FROM content.workspace_chunk_membership wcm
          LEFT JOIN content.workspace_zettels wz ON wz.id = wcm.workspace_zettel_id
          LEFT JOIN content.canonical_chunks cc ON cc.id = wcm.canonical_chunk_id
         WHERE wz.id IS NULL OR cc.id IS NULL
        """,
        "workspace_chunk_membership has orphan rows",
    ),
    (
        """
        SELECT kz.*
          FROM rag.kasten_zettels kz
          LEFT JOIN rag.kastens k ON k.id = kz.kasten_id
          LEFT JOIN content.workspace_zettels wz ON wz.id = kz.workspace_zettel_id
         WHERE k.id IS NULL OR wz.id IS NULL
        """,
        "kasten_zettels has orphan rows",
    ),
    (
        """
        SELECT cm.*
          FROM rag.chat_messages cm
          JOIN rag.chat_sessions cs ON cs.id = cm.session_id
         WHERE cm.workspace_id <> cs.workspace_id
        """,
        "chat_messages workspace_id does not match chat_sessions",
    ),
    (
        """
        SELECT cz.*
          FROM content.canonical_zettels cz
         WHERE NOT EXISTS (
               SELECT 1 FROM content.canonical_chunks cc
                WHERE cc.canonical_zettel_id = cz.id
         )
        """,
        "canonical_zettels without canonical_chunks",
    ),
]


async def verify(config) -> None:
    await run_statements(config, COUNT_SQL)
    for sql, message in CHECKS:
        await assert_zero(config, sql, message)


def main() -> int:
    args = parse_args(__doc__ or "")
    config = load_config(dry_run=args.dry_run)
    require_continue(args, "backfill verification")
    if args.dry_run:
        print("backfill verification ready")
        return 0
    return run_async(verify(config))


if __name__ == "__main__":
    raise SystemExit(main())
