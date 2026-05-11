# LEGACY (broken after 2026-05-11): references dropped public.kg_nodes.summary.
# v2 equivalent: content_nodes.summary via website.core.supabase_v2. Tracked for
# follow-up; one-shot historical backfill — likely retire rather than port.
"""Backfill kg_nodes.summary from on-disk eval artifacts.

Recovers the structured `detailed_summary` that was silently dropped by the
old SupabaseWriter (which tried to write a non-existent `summary_v2` column
and persisted only the brief string into `summary`). The per-URL summaries
produced during the eval iterations are saved to
``docs/summary_eval/<source>/iter-*/summary.json`` (or the ``held_out/<hash>/``
variant). This script rebuilds the canonical JSON envelope from those files
and overwrites the ``summary`` column on matching rows.

Selection rules
    * A row is considered "broken" when its ``summary`` does not start with
      ``{`` — i.e. it is not valid JSON in the post-unification envelope
      shape. Those rows are the ones the frontend renders without a Detailed
      section.
    * When multiple artifacts exist for the same URL, the one with the
      highest composite score from ``input.json`` wins; ties break on the
      latest iteration folder (lexical sort).

Safety
    * Dry-run by default. Pass ``--apply`` to perform updates.
    * Never touches rows whose ``summary`` already parses to a dict with a
      ``detailed_summary`` key (already good).
    * Never mutates rows that lack a matching artifact.

Env
    Reads ``supabase/.env`` for ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY``.

Usage (Git Bash)
    python ops/scripts/backfill_summary_from_eval_artifacts.py           # dry
    python ops/scripts/backfill_summary_from_eval_artifacts.py --apply   # live
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client


REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = REPO_ROOT / "docs" / "summary_eval"


def _load_env() -> tuple[str, str]:
    load_dotenv(REPO_ROOT / "supabase" / ".env")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY must be set in supabase/.env", file=sys.stderr)
        sys.exit(2)
    return url, key


def _canonical_envelope(data: dict[str, Any]) -> str:
    payload = {
        "mini_title": data.get("mini_title") or "",
        "brief_summary": data.get("brief_summary") or "",
        "detailed_summary": data.get("detailed_summary") or [],
        "closing_remarks": data.get("closing_remarks") or data.get("closing_takeaway") or "",
    }
    return json.dumps(payload, ensure_ascii=False)


def _collect_artifacts() -> dict[str, dict[str, Any]]:
    """Return ``url -> best summary.json payload``."""
    best: dict[str, tuple[float, str, dict[str, Any]]] = {}
    for summary_path in EVAL_ROOT.glob("*/iter-*/**/summary.json"):
        try:
            raw = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for url, payload in _extract_records(raw):
            if not url or not payload:
                continue
            score = _score_for(summary_path, url)
            sort_key = str(summary_path)
            prev = best.get(url)
            if prev is None or score > prev[0] or (score == prev[0] and sort_key > prev[1]):
                best[url] = (score, sort_key, payload)
    return {url: payload for url, (_, _, payload) in best.items()}


def _extract_records(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    """Normalize dict-form and list-form summary.json into (url, payload) pairs."""
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, dict):
        url = (raw.get("metadata") or {}).get("url") or raw.get("url")
        if url:
            out.append((url, raw))
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or (item.get("metadata") or {}).get("url")
            inner = item.get("summary") if isinstance(item.get("summary"), dict) else item
            if url and isinstance(inner, dict):
                out.append((url, inner))
    return out


def _score_for(summary_path: Path, url: str) -> float:
    input_path = summary_path.parent.parent / "input.json" if summary_path.parent.name.startswith("iter-") is False else summary_path.parent / "input.json"
    if not input_path.exists():
        input_path = summary_path.parent / "input.json"
    if not input_path.exists():
        return 0.0
    try:
        records = (json.loads(input_path.read_text(encoding="utf-8")).get("records") or [])
    except Exception:
        return 0.0
    for rec in records:
        if rec.get("url") == url:
            try:
                return float(rec.get("composite") or 0.0)
            except Exception:
                return 0.0
    return 0.0


def _fetch_broken_rows(client: Client) -> list[dict[str, Any]]:
    resp = client.table("kg_nodes").select("id,user_id,url,summary,metadata").execute()
    rows = resp.data or []
    broken: list[dict[str, Any]] = []
    for row in rows:
        s = row.get("summary") or ""
        if isinstance(s, str) and s.lstrip().startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict) and parsed.get("detailed_summary"):
                    continue  # already good
            except Exception:
                pass
        broken.append(row)
    return broken


def _render_bar(step: int, total: int, label: str) -> str:
    filled = int((step / total) * 11)
    bar = "▓" * filled + "░" * (11 - filled)
    return f" [{bar}] {step}/{total} {label}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Perform DB updates (default: dry-run)")
    args = parser.parse_args()

    print("━" * 40)
    print(" BACKFILL summary FROM EVAL ARTIFACTS")
    print("━" * 40)

    print(_render_bar(1, 5, "load artifacts"))
    artifacts = _collect_artifacts()
    print(f"   found {len(artifacts)} unique URLs in docs/summary_eval/")

    print(_render_bar(2, 5, "connect supabase"))
    url, key = _load_env()
    client = create_client(url, key)

    print(_render_bar(3, 5, "scan kg_nodes"))
    broken = _fetch_broken_rows(client)
    print(f"   {len(broken)} rows have non-JSON summary (frontend blind spots)")

    print(_render_bar(4, 5, "match + plan"))
    plan: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in broken:
        row_url = row.get("url")
        if not row_url or row_url not in artifacts:
            continue
        plan.append((row, artifacts[row_url]))
    print(f"   matched {len(plan)}/{len(broken)} to on-disk artifacts")
    unmatched = [r for r in broken if r.get("url") not in artifacts]
    if unmatched:
        print("   unmatched row URLs:")
        for r in unmatched[:20]:
            print(f"     - {r.get('id')}: {r.get('url')}")

    print(_render_bar(5, 5, "apply" if args.apply else "dry-run"))
    updated = 0
    for row, artifact in plan:
        envelope = _canonical_envelope(artifact)
        metadata = row.get("metadata") or {}
        metadata["summary_v2"] = {
            "mini_title": artifact.get("mini_title") or "",
            "brief_summary": artifact.get("brief_summary") or "",
            "detailed_summary": artifact.get("detailed_summary") or [],
            "closing_remarks": artifact.get("closing_remarks") or artifact.get("closing_takeaway") or "",
        }
        metadata["backfilled_from"] = "eval_artifacts"
        print(f"   {'UPDATE' if args.apply else 'PLAN  '} {row['id']}  ({row['url']})")
        if args.apply:
            (
                client.table("kg_nodes")
                .update({"summary": envelope, "metadata": metadata})
                .eq("user_id", row["user_id"])
                .eq("id", row["id"])
                .execute()
            )
            updated += 1

    print("━" * 40)
    print(f" DONE — {updated if args.apply else len(plan)} rows {'updated' if args.apply else 'planned'}")
    print("━" * 40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
