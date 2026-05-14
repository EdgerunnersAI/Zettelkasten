# LEGACY (broken after 2026-05-11): references dropped public.kg_nodes / kg_users.
# One-shot remediation script — fully consumed. v2 ownership lives in
# workspaces.owner_user_id (not a kg_users mapping). Kept for historical context.
"""Move specific post-fix YT zettels from legacy 'naruto' render_id user to the
authenticated Naruto UUID f2105544-b73d-4946-8329-096d82f070d3.

One-shot fix for the 3 zettels orphaned when the old Add Zettel route was called without
an auth header in an earlier verification run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / "supabase" / ".env")

URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
AUTH_SUB = "f2105544-b73d-4946-8329-096d82f070d3"

TARGET_IDS = [
    "yt-jobs-2005-stanford-comme",
    "yt-programming-workflow-is",
    "yt-rick-astley-never-gonna",
    "yt-ed-sheeran-shape-of-you",
    "yt-matt-walker-sleep-depriv",
]


def main() -> int:
    client = create_client(URL, KEY)

    user_row = (
        client.table("kg_users")
        .select("id,render_user_id")
        .eq("render_user_id", AUTH_SUB)
        .execute()
    )
    if not user_row.data:
        print(f"No kg_users row for render_user_id={AUTH_SUB}")
        return 1
    target_user = user_row.data[0]["id"]
    print(f"Target kg_users.id={target_user} (render_user_id={AUTH_SUB})")
    global TARGET_USER
    TARGET_USER = target_user

    before = (
        client.table("kg_nodes")
        .select("id,user_id,name")
        .in_("id", TARGET_IDS)
        .execute()
    )
    print(f"Found {len(before.data)} matching rows:")
    for row in before.data:
        print(f"  {row['id']} currently user_id={row['user_id']}")

    if not before.data:
        print("Nothing to move.")
        return 0

    legacy_ids = {row["user_id"] for row in before.data if row["user_id"] != TARGET_USER}
    if not legacy_ids:
        print("All rows already owned by target user; nothing to do.")
        return 0

    # Delete any links under legacy owners that reference these nodes (FK blocks update)
    for legacy in legacy_ids:
        client.table("kg_links").delete().eq("user_id", legacy).in_(
            "source_node_id", TARGET_IDS
        ).execute()
        client.table("kg_links").delete().eq("user_id", legacy).in_(
            "target_node_id", TARGET_IDS
        ).execute()

    # Move nodes
    resp = (
        client.table("kg_nodes")
        .update({"user_id": TARGET_USER})
        .in_("id", TARGET_IDS)
        .execute()
    )
    moved = len(resp.data) if resp.data else 0
    print(f"Moved {moved} nodes to user_id={TARGET_USER}")

    after = (
        client.table("kg_nodes")
        .select("id,user_id")
        .in_("id", TARGET_IDS)
        .execute()
    )
    print("After:")
    for row in after.data:
        print(f"  {row['id']} -> {row['user_id']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
