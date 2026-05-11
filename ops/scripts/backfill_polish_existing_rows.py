# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_nodes).
# One-shot backfill tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""One-shot backfill: polish + caveat-strip + reddit-tag rewrite for every
existing ``kg_nodes`` row in Supabase.

The write path now polishes new rows at persistence time (see
``website/core/persist.py``, ``website/features/summarization_engine/writers/``).
This script normalizes pre-existing rows so the dataset is consistent at-rest
and read-time polish becomes a defense-in-depth no-op rather than a load-bearing
hack.

Usage:
    python ops/scripts/backfill_polish_existing_rows.py            # dry-run
    python ops/scripts/backfill_polish_existing_rows.py --apply    # write

Idempotent: running with ``--apply`` twice changes zero additional rows.

Prerequisites:
    Supabase credentials in ``supabase/.env`` (``SUPABASE_URL`` and
    ``SUPABASE_SERVICE_ROLE_KEY``). Both are wrapped as <private> values when
    logged.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "supabase" / ".env")
load_dotenv(ROOT / ".env", override=False)

import httpx

from website.core.text_polish import (
    is_caveat_only_line,
    polish,
    polish_envelope,
    rewrite_tags,
    strip_caveats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger("backfill_polish")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_all_nodes() -> list[dict]:
    """Page through every ``kg_nodes`` row, returning id + summary + tags."""
    out: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/kg_nodes"
            f"?select=id,user_id,name,summary,tags"
            f"&order=created_at.asc"
            f"&limit={page_size}"
            f"&offset={offset}"
        )
        resp = httpx.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return out


def _try_parse_envelope(raw: str | None) -> dict | None:
    if not raw:
        return None
    cleaned = str(raw).strip()
    if not cleaned.startswith("{"):
        return None
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _polish_envelope_or_string(raw: str | None) -> str | None:
    """Return the polished serialization of ``raw``, or None if no change."""
    if raw is None:
        return None
    parsed = _try_parse_envelope(raw)
    if parsed is not None:
        # Polish brief / closing as strings; for detailed_summary, handle
        # both list (canonical) and string (markdown / legacy) variants.
        new_envelope = dict(parsed)
        if "brief_summary" in new_envelope:
            new_envelope["brief_summary"] = polish(strip_caveats(str(new_envelope.get("brief_summary") or "")))
        if "closing_remarks" in new_envelope:
            new_envelope["closing_remarks"] = polish(strip_caveats(str(new_envelope.get("closing_remarks") or "")))
        if "mini_title" in new_envelope:
            new_envelope["mini_title"] = polish(str(new_envelope.get("mini_title") or ""))
        detailed = new_envelope.get("detailed_summary")
        if isinstance(detailed, list):
            walked = polish_envelope({"detailed_summary": detailed})
            new_envelope["detailed_summary"] = walked.get("detailed_summary", detailed)
        elif isinstance(detailed, str):
            new_envelope["detailed_summary"] = polish(strip_caveats(detailed))
        new_blob = json.dumps(new_envelope, ensure_ascii=False)
        if new_blob == raw:
            return None
        return new_blob
    # Plain-string row — polish in place.
    polished = polish(strip_caveats(str(raw)))
    if polished == raw:
        return None
    return polished


def _polish_tags(raw_tags: list | None) -> list | None:
    if not isinstance(raw_tags, list):
        return None
    rewritten = list(rewrite_tags(raw_tags))
    if rewritten == raw_tags:
        return None
    return rewritten


def _polish_name(raw_name: str | None) -> str | None:
    if not isinstance(raw_name, str) or not raw_name:
        return None
    polished = polish(raw_name)
    if polished == raw_name:
        return None
    return polished


def update_row(row_id: str, *, summary: str | None, tags: list | None, name: str | None = None) -> None:
    payload: dict = {}
    if summary is not None:
        payload["summary"] = summary
    if tags is not None:
        payload["tags"] = tags
    if name is not None:
        payload["name"] = name
    if not payload:
        return
    url = f"{SUPABASE_URL}/rest/v1/kg_nodes?id=eq.{row_id}"
    resp = httpx.patch(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY (check supabase/.env).")
        return 1

    rows = fetch_all_nodes()
    logger.info("Fetched %d kg_nodes rows", len(rows))

    summary_changes = 0
    tag_changes = 0
    name_changes = 0
    rows_to_update = 0

    for row in rows:
        rid = row["id"]
        new_summary = _polish_envelope_or_string(row.get("summary"))
        new_tags = _polish_tags(row.get("tags"))
        new_name = _polish_name(row.get("name"))
        if new_summary is None and new_tags is None and new_name is None:
            continue
        rows_to_update += 1
        if new_summary is not None:
            summary_changes += 1
        if new_tags is not None:
            tag_changes += 1
        if new_name is not None:
            name_changes += 1
        if args.apply:
            try:
                update_row(rid, summary=new_summary, tags=new_tags, name=new_name)
                logger.info(
                    "Updated row %s (summary=%s, tags=%s, name=%s)",
                    rid, new_summary is not None, new_tags is not None, new_name is not None,
                )
            except Exception as exc:
                logger.warning("Failed to update %s: %s", rid, exc)
        else:
            logger.info(
                "[dry-run] Would update %s (summary_changed=%s, tags_changed=%s, name_changed=%s)",
                rid,
                new_summary is not None,
                new_tags is not None,
                new_name is not None,
            )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info(
        "%s | rows scanned=%d | rows needing update=%d | summary deltas=%d | tag deltas=%d | name deltas=%d",
        mode,
        len(rows),
        rows_to_update,
        summary_changes,
        tag_changes,
        name_changes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
