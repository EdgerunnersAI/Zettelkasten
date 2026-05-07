"""iter-12 Task 28 R5: backfill LLM-generated aliases on existing kg_nodes.

Iterates all kg_nodes WHERE aliases = '{}' OR summary_hash IS NULL in batches,
calls canonicalize_node per row via the GeminiKeyPool, then batch-updates
kg_nodes.aliases and kg_nodes.summary_hash.

Idempotent: re-running only touches rows that still have empty aliases or a
missing summary_hash.  Resumable: Ctrl-C mid-run is safe — rows processed so
far keep their aliases.

Usage (droplet SSH, after applying the migration):
    cd /opt/zettelkasten
    python ops/scripts/backfill_aliases.py [--batch-size 50] [--max-rows 500] [--dry-run]

DO NOT execute this script automatically — operator runs it post-deploy.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow running from repo root without editable install
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
_log = logging.getLogger("backfill_aliases")

_DEFAULT_BATCH = 50
_DEFAULT_MAX = 0  # 0 = unlimited


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH, help="Rows per Supabase page (default 50)")
    p.add_argument("--max-rows", type=int, default=_DEFAULT_MAX, help="Stop after N rows (0 = unlimited)")
    p.add_argument("--dry-run", action="store_true", help="Compute aliases but do not write to Supabase")
    return p


async def _run(batch_size: int, max_rows: int, dry_run: bool) -> None:
    from website.core.supabase_kg.client import get_supabase_client
    from website.features.api_key_switching.key_pool import GeminiKeyPool
    from website.features.rag_pipeline.ingest.entity_canonicalizer import (
        canonicalize_node,
        summary_hash as compute_hash,
    )

    supabase = get_supabase_client()
    key_pool = GeminiKeyPool()

    total_processed = 0
    total_updated = 0
    offset = 0

    _log.info("Starting alias backfill dry_run=%s batch_size=%d max_rows=%d", dry_run, batch_size, max_rows or 999999)

    while True:
        # Fetch next batch of nodes needing aliases
        resp = (
            supabase.table("kg_nodes")
            .select("id, user_id, name, summary, summary_hash")
            .or_("aliases.eq.{},summary_hash.is.null")
            .limit(batch_size)
            .offset(offset)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break

        for row in rows:
            if max_rows and total_processed >= max_rows:
                _log.info("Reached --max-rows=%d, stopping.", max_rows)
                return

            node_id: str = row["id"]
            user_id: str = row["user_id"]
            name: str = row.get("name") or ""
            summary: str = row.get("summary") or ""

            # Skip if summary_hash unchanged (idempotent guard)
            new_hash = compute_hash(summary)
            if row.get("summary_hash") == new_hash:
                total_processed += 1
                continue

            try:
                canon = await canonicalize_node(title=name, summary=summary, key_pool=key_pool)
            except Exception as exc:  # noqa: BLE001
                _log.warning("canonicalize_node failed node=%s: %s — skipping", node_id, exc)
                total_processed += 1
                continue

            aliases = canon.get("aliases") or []
            _log.info("node=%s aliases=%r dry_run=%s", node_id, aliases, dry_run)

            if not dry_run:
                try:
                    supabase.table("kg_nodes").update(
                        {"aliases": aliases, "summary_hash": new_hash}
                    ).eq("id", node_id).eq("user_id", user_id).execute()
                    total_updated += 1
                except Exception as exc:  # noqa: BLE001
                    _log.warning("Supabase update failed node=%s: %s", node_id, exc)

            total_processed += 1

        offset += batch_size
        if len(rows) < batch_size:
            break  # last page

    _log.info(
        "Backfill complete: processed=%d updated=%d dry_run=%s",
        total_processed,
        total_updated,
        dry_run,
    )


def main() -> None:
    args = _build_arg_parser().parse_args()
    # Require Supabase env vars
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_ANON_KEY"):
        _log.error("SUPABASE_URL and SUPABASE_ANON_KEY must be set.")
        sys.exit(1)
    asyncio.run(_run(batch_size=args.batch_size, max_rows=args.max_rows, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
