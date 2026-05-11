# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_nodes).
# One-shot backfill tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Backfill embeddings for kg_nodes that have NULL embedding vectors.

Usage:
    python ops/scripts/backfill_embeddings.py [--dry-run] [--batch-size 10] [--user-id UUID]

Prerequisites:
    1. Supabase credentials in supabase/.env
    2. Gemini API key(s) in api_env file at project root (or GEMINI_API_KEY env var)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "supabase" / ".env")
load_dotenv(ROOT / ".env", override=False)

import logging
import os

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_nodes_without_embeddings(user_id: str | None = None) -> list[dict]:
    """Fetch all kg_nodes where embedding IS NULL."""
    url = f"{SUPABASE_URL}/rest/v1/kg_nodes?embedding=is.null&select=id,name,summary,user_id"
    if user_id:
        url += f"&user_id=eq.{user_id}"
    url += "&order=created_at.asc"

    resp = httpx.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_node_embedding(node_id: str, user_id: str, embedding: list[float], retries: int = 3) -> bool:
    """Update the embedding column for a single node with retry on transient errors."""
    url = f"{SUPABASE_URL}/rest/v1/kg_nodes?id=eq.{node_id}&user_id=eq.{user_id}"
    # pgvector expects the embedding as a JSON array string
    embedding_str = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
    for attempt in range(retries):
        try:
            resp = httpx.patch(
                url,
                headers=_headers(),
                json={"embedding": embedding_str},
                timeout=30,
            )
            return resp.status_code in (200, 204)
        except (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.warning("  Retry %d/%d for %s after %s (wait %ds)", attempt + 1, retries, node_id, exc, wait)
                time.sleep(wait)
            else:
                logger.error("  All %d retries failed for %s: %s", retries, node_id, exc)
                return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill embeddings for kg_nodes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without writing")
    parser.add_argument("--batch-size", type=int, default=10, help="Nodes to process per batch (default: 10)")
    parser.add_argument("--user-id", type=str, default=None, help="Filter to a specific user UUID")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between batches (default: 1.0)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in supabase/.env")
        sys.exit(1)

    # Import embedding generator (needs Gemini key)
    from website.features.kg_features.embeddings import generate_embedding

    logger.info("Fetching nodes with NULL embeddings...")
    nodes = fetch_nodes_without_embeddings(args.user_id)
    logger.info("Found %d nodes without embeddings", len(nodes))

    if not nodes:
        logger.info("Nothing to backfill.")
        return

    if args.dry_run:
        for node in nodes:
            logger.info("  [DRY RUN] Would backfill: %s (%s)", node["id"], node["name"])
        return

    success = 0
    skipped = 0
    failed = 0

    for i, node in enumerate(nodes, 1):
        node_id = node["id"]
        name = node.get("name", "")
        summary = node.get("summary", "")
        user_id = node["user_id"]

        # Build embedding input: title + summary (same as live pipeline)
        embed_input = f"{name}\n\n{summary}".strip()[:2000]
        if not embed_input:
            logger.warning("  [%d/%d] SKIP %s — empty name+summary", i, len(nodes), node_id)
            skipped += 1
            continue

        logger.info("  [%d/%d] Generating embedding for %s ...", i, len(nodes), node_id)
        embedding = generate_embedding(embed_input)

        if not embedding:
            logger.warning("  [%d/%d] FAIL %s — embedding generation returned empty", i, len(nodes), node_id)
            failed += 1
            # Delay to respect rate limits even on failure
            time.sleep(args.delay)
            continue

        ok = update_node_embedding(node_id, user_id, embedding)
        if ok:
            logger.info("  [%d/%d] OK   %s (%d dims)", i, len(nodes), node_id, len(embedding))
            success += 1
        else:
            logger.warning("  [%d/%d] FAIL %s — update returned error", i, len(nodes), node_id)
            failed += 1

        # Rate-limit delay between nodes
        if i % args.batch_size == 0:
            logger.info("  Batch pause (%.1fs)...", args.delay)
            time.sleep(args.delay)

    logger.info("Backfill complete: %d success, %d skipped, %d failed (of %d total)",
                success, skipped, failed, len(nodes))


if __name__ == "__main__":
    main()
