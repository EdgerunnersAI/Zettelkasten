"""Reconcile profiles: dedupe duplicates of Naruto, purge orphans (Phase 2D.2 v2 port).

Single-tenant allowlist enforcement: only the canonical Naruto + Zoro auth IDs
own data in production. Any other core.profiles row, or any workspace_zettels
row owned (via workspace -> owner_profile_id) by a non-canonical profile is a
leftover from prior auth migrations and must be reassigned (Naruto dupes) or
purged (orphans).

v2 surface mapping (post-Phase-8 v1 drop):
- v1 kg_users          -> v2 core.profiles (id + email denormalized on profile)
- v1 kg_nodes owner    -> v2 content.workspace_zettels via core.workspaces.owner_profile_id
- v1 kg_links          -> v2 no separate links table (KG/links concept removed)
- v1 kg_node_chunks    -> v2 content.workspace_chunk_membership, scoped by workspace_id;
                          cascades automatically when workspace is deleted.

Usage:
  python ops/scripts/reconcile_kg_users.py --audit
  python ops/scripts/reconcile_kg_users.py --dedupe-naruto         # dry-run
  python ops/scripts/reconcile_kg_users.py --dedupe-naruto --apply # writes
  python ops/scripts/reconcile_kg_users.py --purge-orphans --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / "ops" / "deploy" / "expected_users.json"

logger = logging.getLogger("reconcile_kg_users")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def load_allowlist(path: Path = ALLOWLIST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(conn, allowlist: dict | None = None) -> dict:
    aw = allowlist or load_allowlist()
    canonical = {aw["_canonical_naruto"], aw["_canonical_zoro"]}
    with conn.cursor() as cur:
        # v2: profile rows live in core.profiles; email is denormalized there.
        cur.execute("SELECT id::text, email FROM core.profiles")
        users = list(cur.fetchall())
        # v2: zettel ownership flows through workspace owner; collect distinct owner profile ids.
        cur.execute(
            "SELECT DISTINCT w.owner_profile_id::text "
            "FROM content.workspace_zettels wz "
            "JOIN core.workspaces w ON w.id = wz.workspace_id"
        )
        node_owners = {r[0] for r in cur.fetchall()}
    duplicate_naruto = [
        list(u) for u in users
        if u[1] and "naruto" in u[1].lower() and u[0] != aw["_canonical_naruto"]
    ]
    orphan_owners = sorted(node_owners - canonical)
    report = {
        "users": [list(u) for u in users],
        "duplicate_naruto": duplicate_naruto,
        "orphan_owners": orphan_owners,
    }
    logger.info(
        "audit: %d users, %d duplicate Naruto, %d orphan owners",
        len(users), len(duplicate_naruto), len(orphan_owners),
    )
    return report


def dedupe_naruto(conn, *, dry_run: bool = True, allowlist: dict | None = None) -> int:
    aw = allowlist or load_allowlist()
    canonical = aw["_canonical_naruto"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text FROM core.profiles WHERE LOWER(email) LIKE 'naruto%%' AND id != %s",
            (canonical,),
        )
        dupes = [r[0] for r in cur.fetchall()]
    if not dupes:
        logger.info("no duplicate Naruto users")
        return 0
    logger.warning("found %d duplicate Naruto users: %s", len(dupes), dupes)
    if dry_run:
        return len(dupes)
    with conn.cursor() as cur:
        for dupe_id in dupes:
            # v2: reassign every workspace owned by the dupe to canonical Naruto.
            # workspace_zettels / workspace_chunk_membership follow the workspace
            # automatically (no per-row user_id column in v2).
            cur.execute(
                "UPDATE core.workspaces SET owner_profile_id = %s WHERE owner_profile_id = %s",
                (canonical, dupe_id),
            )
            cur.execute("DELETE FROM core.profiles WHERE id = %s", (dupe_id,))
    conn.commit()
    return len(dupes)


def purge_orphans(conn, *, dry_run: bool = True, allowlist: dict | None = None) -> dict[str, int]:
    aw = allowlist or load_allowlist()
    # psycopg3 binds Python lists -> PostgreSQL ARRAY; use `!= ALL(%s)` instead
    # of `NOT IN %s` (the latter emits a single $1 placeholder, which Postgres
    # rejects as a syntax error since IN expects a parenthesised list).
    allowed = list(aw["allowed_auth_ids"])
    with conn.cursor() as cur:
        # v2: count zettels whose workspace owner is outside the allowlist.
        cur.execute(
            "SELECT COUNT(*) FROM content.workspace_zettels wz "
            "JOIN core.workspaces w ON w.id = wz.workspace_id "
            "WHERE w.owner_profile_id::text != ALL(%s)",
            (allowed,),
        )
        n_nodes = cur.fetchone()[0]
        # v2: count workspaces themselves owned outside the allowlist (replaces
        # the v1 kg_links sweep — links no longer exist as a standalone table).
        cur.execute(
            "SELECT COUNT(*) FROM core.workspaces WHERE owner_profile_id::text != ALL(%s)",
            (allowed,),
        )
        n_links = cur.fetchone()[0]
    counts = {"nodes": n_nodes, "links": n_links}
    if dry_run:
        logger.info("would purge: %s", counts)
        return counts
    with conn.cursor() as cur:
        # v2: deleting the workspace cascades to workspace_zettels +
        # workspace_chunk_membership + workspace_members via FK ON DELETE CASCADE.
        cur.execute(
            "DELETE FROM content.workspace_zettels "
            "WHERE workspace_id IN ("
            " SELECT id FROM core.workspaces WHERE owner_profile_id::text != ALL(%s)"
            ")",
            (allowed,),
        )
        cur.execute(
            "DELETE FROM core.workspaces WHERE owner_profile_id::text != ALL(%s)",
            (allowed,),
        )
    conn.commit()
    logger.info("purged: %s", counts)
    return counts


def _connect():
    import psycopg

    dsn = os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("SUPABASE_DB_URL is required")
    return psycopg.connect(dsn, autocommit=False)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit", action="store_true")
    p.add_argument("--dedupe-naruto", action="store_true")
    p.add_argument("--purge-orphans", action="store_true")
    p.add_argument("--apply", action="store_true", help="commit changes (default is dry-run)")
    args = p.parse_args(argv)

    if not (args.audit or args.dedupe_naruto or args.purge_orphans):
        p.error("specify at least one of --audit / --dedupe-naruto / --purge-orphans")

    conn = _connect()
    try:
        dry = not args.apply
        if args.audit:
            print(json.dumps(audit(conn), indent=2, default=str))
        if args.dedupe_naruto:
            dedupe_naruto(conn, dry_run=dry)
        if args.purge_orphans:
            purge_orphans(conn, dry_run=dry)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
