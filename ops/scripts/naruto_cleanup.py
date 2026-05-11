# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_users).
# One-shot cleanup tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Delete every non-canonical Naruto kg_users row + cascades.

Canonical Naruto auth id: f2105544-b73d-4946-8329-096d82f070d3
                         email: naruto@zettelkasten.local

Any other kg_users row whose display_name OR render_user_id contains
"naruto" (case-insensitive) is considered a duplicate and deleted with
its nodes, links, stats, nexus, rag sandbox/chat data via ON DELETE
CASCADE.

Runs against the live Supabase project via the Management API SQL
endpoint (bypasses RLS by using SUPABASE_ACCESS_TOKEN).

One-shot surgery. Not committed. Safe to re-run (idempotent).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(r"C:\Users\LENOVO\Documents\Claude_Code\Projects\Obsidian_Vault")
load_dotenv(ROOT / "supabase" / ".env", override=False)

TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
URL = os.environ["SUPABASE_URL"]
PROJECT_REF = URL.split("//", 1)[1].split(".", 1)[0]

CANONICAL_NARUTO_AUTH_ID = "f2105544-b73d-4946-8329-096d82f070d3"
CANONICAL_NARUTO_EMAIL = "naruto@zettelkasten.local"


def run_sql(sql: str) -> list[dict]:
    resp = httpx.post(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        json={"query": sql},
        timeout=60.0,
    )
    if resp.status_code >= 400:
        print(f"HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit("SQL FAILED")
    return resp.json()


def main() -> int:
    print(f"Project: {PROJECT_REF}\n")

    # 1) Enumerate Naruto-ish rows (canonical + any non-canonical).
    find_sql = f"""
    SELECT id::text,
           render_user_id,
           display_name,
           email,
           created_at
      FROM kg_users
     WHERE display_name ILIKE '%naruto%'
        OR render_user_id ILIKE '%naruto%'
        OR email ILIKE '%naruto%'
     ORDER BY created_at NULLS LAST;
    """
    rows = run_sql(find_sql)
    print("=== All Naruto-ish kg_users rows ===")
    for r in rows:
        print(json.dumps(r, default=str))
    print()

    doomed = [
        r for r in rows
        if r["render_user_id"] != CANONICAL_NARUTO_AUTH_ID
    ]
    canonical = [
        r for r in rows
        if r["render_user_id"] == CANONICAL_NARUTO_AUTH_ID
    ]

    print(f"Canonical rows (keep): {len(canonical)}")
    print(f"Doomed rows (delete): {len(doomed)}")
    if not doomed:
        print("Nothing to delete. Exiting.")
        return 0

    # 2) Snapshot each doomed row's footprint BEFORE deletion.
    print("\n=== Pre-delete snapshot per doomed row ===")
    snapshots = []
    for d in doomed:
        uid = d["id"]
        snap_sql = f"""
        SELECT 'kg_nodes' AS tbl, COUNT(*)::int AS n FROM kg_nodes WHERE user_id = '{uid}'
        UNION ALL SELECT 'kg_links', COUNT(*)::int FROM kg_links WHERE user_id = '{uid}'
        UNION ALL SELECT 'kg_user_stats', COUNT(*)::int FROM information_schema.tables
          WHERE table_schema='public' AND table_name='kg_user_stats'
        ;
        """
        # kg_user_stats may not exist; guard it.
        base_counts_sql = f"""
        SELECT 'kg_nodes' AS tbl, COUNT(*)::int AS n FROM kg_nodes WHERE user_id = '{uid}'
        UNION ALL SELECT 'kg_links', COUNT(*)::int FROM kg_links WHERE user_id = '{uid}';
        """
        counts = run_sql(base_counts_sql)
        # Probe optional tables individually so a missing table doesn't abort.
        optional_tables = [
            "kg_user_stats",
            "rag_sandboxes",
            "rag_chunks",
            "chat_sessions",
            "chat_messages",
            "nexus_messages",
            "nexus_sessions",
            "nexus_events",
            "summarization_runs",
            "summarization_metrics",
        ]
        for t in optional_tables:
            exists = run_sql(
                f"SELECT to_regclass('public.{t}') IS NOT NULL AS e"
            )
            if not exists or not exists[0].get("e"):
                continue
            c = run_sql(
                f"SELECT COUNT(*)::int AS n FROM public.{t} WHERE user_id = '{uid}'"
            )
            counts.append({"tbl": t, "n": c[0]["n"] if c else 0})
        snap = {
            "id": d["id"],
            "render_user_id": d["render_user_id"],
            "display_name": d["display_name"],
            "email": d.get("email"),
            "counts": {row["tbl"]: row["n"] for row in counts},
        }
        snapshots.append(snap)
        print(json.dumps(snap, default=str))

    # 3) Delete via cascade (ON DELETE CASCADE FKs handle children).
    print("\n=== Deleting doomed rows ===")
    ids_list = ",".join(f"'{d['id']}'" for d in doomed)
    del_sql = f"""
    DELETE FROM kg_users WHERE id IN ({ids_list}) RETURNING id::text, render_user_id, display_name;
    """
    deleted = run_sql(del_sql)
    print(f"Deleted kg_users rows: {len(deleted)}")
    for row in deleted:
        print("  " + json.dumps(row, default=str))

    # 4) Verify post-cleanup state.
    print("\n=== Post-cleanup verification ===")
    remaining = run_sql(find_sql)
    print(f"Remaining Naruto-ish rows: {len(remaining)}")
    for r in remaining:
        print("  " + json.dumps(r, default=str))

    # Canonical counts
    final = run_sql(f"""
    SELECT
      (SELECT COUNT(*)::int FROM kg_nodes
         WHERE user_id = (SELECT id FROM kg_users WHERE render_user_id = '{CANONICAL_NARUTO_AUTH_ID}')) AS node_count,
      (SELECT COUNT(*)::int FROM kg_links
         WHERE user_id = (SELECT id FROM kg_users WHERE render_user_id = '{CANONICAL_NARUTO_AUTH_ID}')) AS link_count;
    """)
    print("\nCanonical Naruto final counts:")
    print(json.dumps(final[0] if final else {}, default=str))

    # Emit structured report for the final response.
    print("\n=== REPORT_JSON ===")
    print(json.dumps({
        "canonical": canonical,
        "doomed_snapshots": snapshots,
        "deleted": deleted,
        "remaining_naruto_like": remaining,
        "canonical_final": final[0] if final else {},
    }, default=str, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
