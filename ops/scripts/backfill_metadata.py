# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_node_chunks).
# One-shot backfill tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""One-shot backfill: enrich kg_node_chunks.metadata for legacy chunks.

Plan: docs/superpowers/plans/2026-04-26-rag-improvements-iter-01-02.md (Task 12).

Walks ``kg_node_chunks`` rows where ``metadata_enriched_at IS NULL``, runs the
ingest-side ``MetadataEnricher`` over each batch, and writes back the enriched
``metadata`` jsonb plus a ``metadata_enriched_at = now()`` sentinel so future
runs skip the row. Idempotent by construction.

The script intentionally tolerates per-chunk enrichment failures: if a single
chunk raises during enrichment we log and skip it, preserving forward progress
for the rest of the batch. The enricher itself swallows LLM-level errors, so
the only realistic failure mode is upstream (e.g. Supabase write rejection),
which we surface in the run summary.

Run:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
        python ops/scripts/backfill_metadata.py \
        [--batch-size 100] [--user-id <uuid>] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# Project root for dotenv + package imports
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - optional in test env
    from dotenv import load_dotenv

    load_dotenv(ROOT / "supabase" / ".env")
except Exception:  # pragma: no cover
    pass

from supabase import create_client  # noqa: E402

from website.features.rag_pipeline.ingest.metadata_enricher import (  # noqa: E402
    MetadataEnricher,
)

DEFAULT_BATCH_SIZE = 100
TABLE = "kg_node_chunks"

logger = logging.getLogger("backfill_metadata")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Chunks per enrich/write batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="If set, scope the backfill to a single kg_users.id.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on total chunks processed across all batches.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count pending chunks per batch and exit without writing.",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def _fetch_pending(sb: Any, batch_size: int, user_id: str | None) -> list[dict]:
    """Fetch the next batch of un-enriched chunks ordered by created_at.

    Always re-queries from the head: completed rows drop out of the pending set
    (because ``metadata_enriched_at`` is now set), so a fresh ``range(0, N-1)``
    each iteration walks forward without paging-cursor bookkeeping. This is
    also the property that makes the script safe to interrupt and resume.
    """
    q = (
        sb.table(TABLE)
        .select("id,content,metadata")
        .is_("metadata_enriched_at", None)
        .order("created_at")
        .range(0, batch_size - 1)
    )
    if user_id:
        q = q.eq("user_id", user_id)
    resp = q.execute()
    return list(resp.data or [])


def _build_enricher(key_pool: Any | None) -> MetadataEnricher:
    return MetadataEnricher(key_pool=key_pool)


async def _process_batch(
    sb: Any,
    enricher: MetadataEnricher,
    chunks: list[dict],
) -> tuple[int, int]:
    """Enrich + write back one batch. Returns (written, skipped)."""
    if not chunks:
        return (0, 0)

    # The enricher mutates in place and returns the same list. It is itself
    # exception-safe per chunk (deterministic pass + best-effort entity pass),
    # so a top-level enrich() failure is unexpected — but we still defend
    # against it so a poison batch can't halt the whole job.
    try:
        enriched = await enricher.enrich_chunks(chunks)
    except Exception:
        logger.exception(
            "enricher raised on batch of %d chunks; skipping batch", len(chunks)
        )
        return (0, len(chunks))

    written = 0
    skipped = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for chunk in enriched:
        chunk_id = chunk.get("id")
        if not chunk_id:
            skipped += 1
            continue
        try:
            sb.table(TABLE).update(
                {
                    "metadata": chunk.get("metadata") or {},
                    "metadata_enriched_at": now_iso,
                }
            ).eq("id", chunk_id).execute()
            written += 1
        except Exception:
            logger.exception(
                "write-back failed for chunk %s; continuing", chunk_id
            )
            skipped += 1
    return (written, skipped)


async def _run(args: argparse.Namespace, sb: Any, key_pool: Any | None) -> int:
    enricher = _build_enricher(key_pool)

    total_written = 0
    total_skipped = 0
    total_seen = 0
    batch_no = 0

    while True:
        remaining_cap = None
        if args.limit is not None:
            remaining_cap = args.limit - total_seen
            if remaining_cap <= 0:
                logger.info("hit --limit cap of %d; stopping", args.limit)
                break

        fetch_size = args.batch_size
        if remaining_cap is not None:
            fetch_size = min(fetch_size, remaining_cap)

        chunks = _fetch_pending(sb, fetch_size, args.user_id)
        if not chunks:
            break
        batch_no += 1
        total_seen += len(chunks)

        if args.dry_run:
            logger.info(
                "dry-run batch %d: would enrich %d chunks (running total: %d)",
                batch_no,
                len(chunks),
                total_seen,
            )
            # Dry-run: never writes => pending set never shrinks => infinite
            # loop unless we break. One pass over the head is enough to give
            # operators an actionable count.
            break

        written, skipped = await _process_batch(sb, enricher, chunks)
        total_written += written
        total_skipped += skipped
        logger.info(
            "batch %d complete: written=%d skipped=%d (cumulative written=%d)",
            batch_no,
            written,
            skipped,
            total_written,
        )

    if args.dry_run:
        logger.info("dry-run summary: would enrich >= %d chunks", total_seen)
    else:
        logger.info(
            "backfill complete: written=%d skipped=%d batches=%d",
            total_written,
            total_skipped,
            batch_no,
        )
    return 0


def _build_key_pool() -> Any | None:
    """Best-effort key-pool init. Returns None if pool/keys are unavailable.

    The deterministic pass of MetadataEnricher (domains + dates) runs without a
    key pool, so a missing pool just means we skip the optional LLM-entity
    pass. We never block the backfill on key-pool wiring.
    """
    try:
        from website.features.api_key_switching import get_key_pool
    except Exception:
        logger.warning("api_key_switching import failed; LLM entity pass disabled")
        return None
    try:
        return get_key_pool()
    except Exception:
        logger.warning(
            "get_key_pool() failed; running enricher without LLM entity pass",
            exc_info=True,
        )
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        return 2

    sb = create_client(url, key)
    key_pool = None if args.dry_run else _build_key_pool()

    return asyncio.run(_run(args, sb, key_pool))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
