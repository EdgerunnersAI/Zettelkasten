# LEGACY (broken after 2026-05-11): references dropped public.kg_nodes / kg_links.
# One-shot cleanup script — fully consumed; v2 equivalent (content_nodes/edges)
# not needed for new captures. Kept as reference; retire if not revived by 2026-06.
"""Delete malfunctioned zettels from Supabase kg_nodes.

Targets rows explicitly flagged by the user as low-content or placeholder
captures (neural network intro, sindresorhus/awesome, r/changemyview,
Rick Astley MV, ed sheeran MV, test-title, duplicate Jobs row,
r/ExperiencedDevs placeholder). Also deletes incident links so no
orphan edges remain.

Safety: dry-run by default. ``--apply`` performs deletion. No LLM calls.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client


REPO_ROOT = Path(__file__).resolve().parents[2]

TARGET_IDS = [
    "web-test-title",
    "yt-but-what-is-a-neural-net",
    "yt-neural-network-structure",
    "gh-sindresorhus-awesome",
    "rd-r-changemyview-reddit-po",
    "yt-rick-astley-never-gonna",
    "yt-ed-sheeran-shape-of-you",
    "yt-jobs-2005-stanford-comme",
    "rd-r-experienceddevs-reddit",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / "supabase" / ".env")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL + SERVICE_ROLE_KEY missing", file=sys.stderr)
        return 2
    client = create_client(url, key)

    print("=" * 60)
    print(f" DELETE BAD ZETTELS  ({'APPLY' if args.apply else 'dry-run'})")
    print("=" * 60)

    rows = client.table("kg_nodes").select("id,name,url").in_("id", TARGET_IDS).execute().data or []
    present_ids = {r["id"] for r in rows}
    missing = [i for i in TARGET_IDS if i not in present_ids]

    for r in rows:
        print(f"  - {r['id']:55s}  {r.get('name')!r}")
    for m in missing:
        print(f"  ~ {m:55s}  (already absent)")

    if not args.apply:
        print(f"\n  {len(rows)} rows would be deleted. Re-run with --apply.")
        return 0

    # Clean up edges first (both directions) to avoid FK orphans
    for rid in present_ids:
        client.table("kg_links").delete().eq("source_node_id", rid).execute()
        client.table("kg_links").delete().eq("target_node_id", rid).execute()
    # Delete the nodes
    deleted = client.table("kg_nodes").delete().in_("id", list(present_ids)).execute()
    print(f"\n  Deleted {len(deleted.data or [])} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
