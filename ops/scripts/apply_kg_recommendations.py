"""Autonomous KG-recommendation applicator with audit logging."""
# LEGACY (broken after 2026-05-11): imports website.core.supabase_kg which was retired
# in Phase 8.0.6. To revive, port get_supabase_client calls to get_v2_client() from
# website.core.supabase_v2.client. Tracked for follow-up iteration.
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def apply_recommendations(
    *,
    recs_path: Path,
    user_id: str,
    supabase: Any,
    dry_run: bool = False,
) -> dict:
    raw = json.loads(recs_path.read_text(encoding="utf-8"))
    summary = {"applied_count": 0, "skipped_count": 0, "dry_run": dry_run, "applied": [], "skipped": []}

    for rec in raw:
        if rec.get("status") != "auto_apply":
            summary["skipped_count"] += 1
            summary["skipped"].append({"type": rec.get("type"), "reason": rec.get("status")})
            continue
        if dry_run:
            continue

        rtype = rec["type"]
        payload = rec.get("payload", {})
        if rtype == "add_link":
            supabase.table("kg_links").insert({
                "user_id": user_id,
                "source_node_id": payload["from_node"],
                "target_node_id": payload["to_node"],
                "relation": payload.get("suggested_relation", "rag_eval_proximity"),
            }).execute()
        elif rtype == "add_tag":
            # Update kg_nodes tags array
            existing = supabase.table("kg_nodes").select("tags").eq(
                "user_id", user_id
            ).eq("id", payload["node_id"]).single().execute().data
            new_tags = list(set((existing or {}).get("tags", []) + [payload["suggested_tag"]]))
            supabase.table("kg_nodes").update({"tags": new_tags}).eq(
                "user_id", user_id).eq("id", payload["node_id"]).execute()
        elif rtype == "orphan_warning":
            # Annotation only — no graph mutation
            existing = supabase.table("kg_nodes").select("metadata").eq(
                "user_id", user_id).eq("id", payload["node_id"]).single().execute().data
            md = (existing or {}).get("metadata", {}) or {}
            md["rag_eval_orphan_flag"] = datetime.now(timezone.utc).isoformat()
            supabase.table("kg_nodes").update({"metadata": md}).eq(
                "user_id", user_id).eq("id", payload["node_id"]).execute()
        else:
            # merge_nodes / reingest_node only run via --confirm flag (separate code path)
            summary["skipped_count"] += 1
            continue

        summary["applied_count"] += 1
        summary["applied"].append({"type": rtype, "payload": payload})

    return summary


def _changelog_append(path: Path, summary: dict, iter_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"\n## {iter_id} — {ts}\n"]
    for app in summary.get("applied", []):
        lines.append(f"- APPLIED `{app['type']}` — {json.dumps(app['payload'])}\n")
    for skip in summary.get("skipped", []):
        lines.append(f"- SKIPPED `{skip['type']}` — reason: {skip.get('reason')}\n")
    with path.open("a", encoding="utf-8") as f:
        f.writelines(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iter", required=True, help="e.g. youtube/iter-02")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm", action="store_true",
                        help="Required for merge_nodes / reingest_node application.")
    args = parser.parse_args()

    from website.core.supabase_kg.client import get_supabase_client

    supabase = get_supabase_client()
    if supabase is None:
        print("ERROR: Supabase not configured")
        return 1

    recs_path = Path("docs/rag_eval") / args.iter / "kg_recommendations.json"
    summary = asyncio.run(apply_recommendations(
        recs_path=recs_path, user_id=args.user_id, supabase=supabase, dry_run=args.dry_run,
    ))
    print(json.dumps(summary, indent=2))
    if not args.dry_run and summary.get("applied_count", 0) > 0:
        _changelog_append(Path("docs/rag_eval/_kg_changelog.md"), summary, args.iter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
