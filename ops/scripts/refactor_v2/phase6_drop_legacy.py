"""Phase 6 destructive drop of legacy v1 public.* tables.

Operator-approved per-occurrence:
  A) start Phase 6
  B) bypass the 14-day soak guard (one-shot)
  C) entire 22-table allow-list authorised verbatim

The canonical SQL lives at:
    supabase/website/_v2/15_drop_legacy_tables.sql

This driver:
  1. Runs the pre-flight pg_depend enumeration (Round-2 R2.5). If any object
     outside the allow-list depends on an allow-list table, BLOCK.
  2. Executes the DROP block ONLY (skips the 14-day soak DO-block — operator
     authorisation A.B.C). The soak guard stays intact in the SQL file for
     any future fresh install.
  3. Records `15_drop_legacy_tables.sql` in core._migrations_applied so a
     subsequent `apply_migrations.py --v2` skips it.
  4. Captures post-DROP counts in public.* / kg.* / content.* / rag.* /
     pipelines.* / billing.* and re-runs the Phase-5 login verifier
     (sign_in_with_password against the v2 anon client).

Run:
    python ops/scripts/refactor_v2/phase6_drop_legacy.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env.v2")
load_dotenv(ROOT / ".env")

from website.core.supabase_v2.client import (  # noqa: E402
    get_v2_anon_client,
    get_v2_database_url,
)

MIGRATION_PATH = ROOT / "supabase" / "website" / "_v2" / "15_drop_legacy_tables.sql"
LOG_PATH = ROOT / "docs" / "db-v2" / "backfill-runs" / "2026-05-10.log"

NARUTO = {
    "id": "f2105544-b73d-4946-8329-096d82f070d3",
    "email": "naruto@zettelkasten.local",
    "password": "Naruto2026!",
}
ZORO = {
    "id": "a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e",
    "email": "zoro@zettelkasten.test",
    "password": "Zoro2026!",
}

# 30-entry verbatim allow-list (matches the SQL file):
#   22 originals + 3 derivative views + 5 legacy public.pricing_* tables
#   (operator chat 2026-05-10). The v2 canonical pricing module is billing.*
#   and is NOT in this allow-list — it remains untouched.
ALLOW_LIST = [
    "public.kg_users", "public.kg_nodes", "public.kg_links", "public.kg_node_chunks",
    "public.kg_usage_edges", "public.kg_usage_edges_agg", "public.kg_kasten_node_freq",
    "public.kg_bandit_posteriors", "public.kg_extraction_blocklist", "public.kg_kasten_metrics",
    "public.rag_sandboxes", "public.rag_sandbox_members",
    "public.chat_sessions", "public.chat_messages",
    "public.summary_batch_runs", "public.summary_batch_items",
    "public.nexus_provider_accounts", "public.nexus_oauth_states",
    "public.nexus_ingest_runs", "public.nexus_ingested_artifacts",
    "public.recompute_runs", "public._migrations_applied",
    "public.kg_graph_view", "public.kg_user_stats", "public.rag_sandbox_stats",
    "public.pricing_billing_profiles", "public.pricing_credit_ledger",
    "public.pricing_orders", "public.pricing_subscriptions",
    "public.pricing_usage_counters",
]

# Topologically sorted DROP order (matches 15_drop_legacy_tables.sql exactly).
# Edges enumerated live 2026-05-10 via pg_constraint + pg_rewrite; see the
# big comment block in the SQL file for the verified edge set.
DROP_STATEMENTS: list[tuple[str, str]] = [
    # Tier 0: views and materialized view.
    ("public.kg_graph_view",             "DROP VIEW IF EXISTS public.kg_graph_view RESTRICT"),
    ("public.kg_user_stats",             "DROP VIEW IF EXISTS public.kg_user_stats RESTRICT"),
    ("public.rag_sandbox_stats",         "DROP VIEW IF EXISTS public.rag_sandbox_stats RESTRICT"),
    ("public.kg_usage_edges_agg",        "DROP MATERIALIZED VIEW IF EXISTS public.kg_usage_edges_agg RESTRICT"),
    # Tier 1: FK-holding leaves.
    ("public.pricing_credit_ledger",     "DROP TABLE IF EXISTS public.pricing_credit_ledger RESTRICT"),
    ("public.pricing_usage_counters",    "DROP TABLE IF EXISTS public.pricing_usage_counters RESTRICT"),
    ("public.pricing_orders",            "DROP TABLE IF EXISTS public.pricing_orders RESTRICT"),
    ("public.pricing_subscriptions",     "DROP TABLE IF EXISTS public.pricing_subscriptions RESTRICT"),
    ("public.pricing_billing_profiles",  "DROP TABLE IF EXISTS public.pricing_billing_profiles RESTRICT"),
    ("public.kg_kasten_metrics",         "DROP TABLE IF EXISTS public.kg_kasten_metrics RESTRICT"),
    ("public.kg_kasten_node_freq",       "DROP TABLE IF EXISTS public.kg_kasten_node_freq RESTRICT"),
    ("public.kg_bandit_posteriors",      "DROP TABLE IF EXISTS public.kg_bandit_posteriors RESTRICT"),
    ("public.kg_extraction_blocklist",   "DROP TABLE IF EXISTS public.kg_extraction_blocklist RESTRICT"),
    ("public.kg_node_chunks",            "DROP TABLE IF EXISTS public.kg_node_chunks RESTRICT"),
    ("public.kg_links",                  "DROP TABLE IF EXISTS public.kg_links RESTRICT"),
    ("public.chat_messages",             "DROP TABLE IF EXISTS public.chat_messages RESTRICT"),
    ("public.summary_batch_items",       "DROP TABLE IF EXISTS public.summary_batch_items RESTRICT"),
    ("public.nexus_ingested_artifacts",  "DROP TABLE IF EXISTS public.nexus_ingested_artifacts RESTRICT"),
    ("public.kg_usage_edges",            "DROP TABLE IF EXISTS public.kg_usage_edges RESTRICT"),
    ("public.recompute_runs",            "DROP TABLE IF EXISTS public.recompute_runs RESTRICT"),
    ("public.rag_sandbox_members",       "DROP TABLE IF EXISTS public.rag_sandbox_members RESTRICT"),
    # Tier 2.
    ("public.chat_sessions",             "DROP TABLE IF EXISTS public.chat_sessions RESTRICT"),
    ("public.summary_batch_runs",        "DROP TABLE IF EXISTS public.summary_batch_runs RESTRICT"),
    ("public.nexus_ingest_runs",         "DROP TABLE IF EXISTS public.nexus_ingest_runs RESTRICT"),
    ("public.nexus_provider_accounts",   "DROP TABLE IF EXISTS public.nexus_provider_accounts RESTRICT"),
    ("public.nexus_oauth_states",        "DROP TABLE IF EXISTS public.nexus_oauth_states RESTRICT"),
    ("public.rag_sandboxes",             "DROP TABLE IF EXISTS public.rag_sandboxes RESTRICT"),
    # Tier 3.
    ("public.kg_nodes",                  "DROP TABLE IF EXISTS public.kg_nodes RESTRICT"),
    # Tier 4.
    ("public.kg_users",                  "DROP TABLE IF EXISTS public.kg_users RESTRICT"),
    # Tier 5.
    ("public._migrations_applied",       "DROP TABLE IF EXISTS public._migrations_applied RESTRICT"),
]


def jdefault(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return str(obj)


async def run_preflight(conn: asyncpg.Connection) -> dict[str, list[dict[str, Any]]]:
    """3-branch pg_depend enumeration. Returns per-branch unexpected dependents.

    Branch 1: direct pg_class<->pg_class deps via pg_depend.
    Branch 2: view/mview rewrite-rule deps via pg_rewrite (catches view
              definitions that read from allow-list tables).
    Branch 3: FK deps via pg_constraint (catches tables holding FKs targeting
              allow-list tables — pg_depend stores these via constraint OIDs,
              so the direct join in branch 1 misses them).

    The migration is BLOCKED if any branch reports a dependent outside the
    allow-list.
    """
    branch1 = await conn.fetch(
        """
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind::text                AS dependent_kind
        FROM pg_depend d
        JOIN pg_class c1 ON c1.oid = d.refobjid
        JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
        JOIN pg_class c2 ON c2.oid = d.objid
        JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
        WHERE n1.nspname || '.' || c1.relname = ANY ($1::text[])
          AND c2.oid <> c1.oid
          AND (n2.nspname || '.' || c2.relname) <> ALL ($1::text[])
          AND c2.relkind IN ('r', 'v', 'm', 'f', 'p')
        ORDER BY 1, 2
        """,
        ALLOW_LIST,
    )
    branch2 = await conn.fetch(
        """
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind::text                AS dependent_kind
          FROM pg_depend d
          JOIN pg_rewrite r ON r.oid = d.objid AND d.classid = 'pg_rewrite'::regclass
          JOIN pg_class c2 ON c2.oid = r.ev_class
          JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
          JOIN pg_class c1 ON c1.oid = d.refobjid
          JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
         WHERE n1.nspname || '.' || c1.relname = ANY ($1::text[])
           AND c2.oid <> c1.oid
           AND (n2.nspname || '.' || c2.relname) <> ALL ($1::text[])
           AND c2.relkind IN ('v', 'm')
         ORDER BY 1, 2
        """,
        ALLOW_LIST,
    )
    branch3 = await conn.fetch(
        """
        SELECT DISTINCT
            n2.nspname || '.' || c2.relname AS dependent_object,
            n1.nspname || '.' || c1.relname AS legacy_table,
            c2.relkind::text                AS dependent_kind
          FROM pg_constraint con
          JOIN pg_class c1 ON c1.oid = con.confrelid
          JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
          JOIN pg_class c2 ON c2.oid = con.conrelid
          JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
         WHERE con.contype = 'f'
           AND n1.nspname || '.' || c1.relname = ANY ($1::text[])
           AND c2.oid <> c1.oid
           AND (n2.nspname || '.' || c2.relname) <> ALL ($1::text[])
           AND c2.relkind = 'r'
         ORDER BY 1, 2
        """,
        ALLOW_LIST,
    )
    return {
        "direct": [dict(r) for r in branch1],
        "pg_rewrite": [dict(r) for r in branch2],
        "pg_constraint": [dict(r) for r in branch3],
    }


async def existing_legacy_tables(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT n.nspname || '.' || c.relname AS qname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND (n.nspname || '.' || c.relname) = ANY ($1::text[])
          AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
        ORDER BY qname
        """,
        ALLOW_LIST,
    )
    return [r["qname"] for r in rows]


async def post_drop_counts(conn: asyncpg.Connection) -> dict[str, Any]:
    """Counts to surface that legacy public.* matchers are gone and v2 schemas intact."""
    out: dict[str, Any] = {}
    out["public_legacy_tables"] = await conn.fetchval(
        """
        SELECT count(*)
          FROM information_schema.tables
         WHERE table_schema = 'public'
           AND (
                table_name LIKE 'kg_%'
             OR table_name LIKE 'rag_%'
             OR table_name LIKE 'nexus_%'
             OR table_name LIKE 'chat_%'
             OR table_name LIKE 'summary_batch_%'
             OR table_name LIKE 'recompute_%'
             OR table_name = '_migrations_applied'
             -- 5 legacy pricing_* names dropped this round (other public.pricing_*
             -- tables are NOT in the allow-list and remain untouched).
             OR table_name IN ('pricing_billing_profiles', 'pricing_credit_ledger',
                               'pricing_orders', 'pricing_subscriptions',
                               'pricing_usage_counters')
           )
        """
    )
    # information_schema.views excludes materialized views — check the 3
    # explicitly named legacy views.
    out["public_legacy_views"] = await conn.fetchval(
        """
        SELECT count(*) FROM information_schema.views
         WHERE table_schema = 'public'
           AND table_name IN ('kg_graph_view', 'kg_user_stats', 'rag_sandbox_stats')
        """
    )
    for sch in ("core", "content", "kg", "rag", "pipelines", "billing"):
        out[f"{sch}_tables"] = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = $1",
            sch,
        )
    return out


async def record_migration(conn: asyncpg.Connection, sql_text: str) -> str:
    """Record 15_drop_legacy_tables.sql as applied in core._migrations_applied.

    Use the same checksum convention as ops/scripts/apply_migrations.py:
    sha256 of the file body, hex-encoded.
    """
    checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
    hostname = socket.gethostname()
    await conn.execute(
        """
        INSERT INTO core._migrations_applied
            (name, checksum, applied_by, runner_hostname)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (name) DO NOTHING
        """,
        MIGRATION_PATH.name, checksum, "phase6_drop_legacy.py", hostname,
    )
    return checksum


async def verify_logins() -> list[str]:
    print("[Step 4] Re-verifying email/password login via v2 anon client...")
    lines: list[str] = []
    anon = get_v2_anon_client()
    for u in (NARUTO, ZORO):
        try:
            resp = anon.auth.sign_in_with_password({"email": u["email"], "password": u["password"]})
            session = resp.session
            user = resp.user
            if not session or not getattr(session, "access_token", None):
                lines.append(f"FAIL {u['email']}: no session")
                continue
            jwt = session.access_token
            uid = getattr(user, "id", "?") if user else "?"
            lines.append(f"OK   {u['email']} uid={uid} jwt_len={len(jwt)}")
            anon.auth.sign_out()
        except Exception as e:
            lines.append(f"FAIL {u['email']}: {type(e).__name__}: {e}")
    for ln in lines:
        print(f"  {ln}")
    return lines


def append_log(payload: dict[str, Any]) -> None:
    """Append a phase6 entry to the existing 2026-05-10.log file.

    The file was a JSON object from phase5; we keep it parseable by writing
    a JSON object and a separator line above. To stay robust, we read the
    existing content as text, then append a separator + JSON block.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
    block = "\n\n--- phase6_drop_legacy.py ---\n" + json.dumps(payload, default=jdefault, indent=2) + "\n"
    LOG_PATH.write_text(existing + block, encoding="utf-8")
    print(f"[log] appended phase6 block to {LOG_PATH}")


async def main() -> int:
    sql_text = MIGRATION_PATH.read_text(encoding="utf-8")
    dsn = get_v2_database_url()
    print(f"DB v2 DSN: {dsn.split('@')[-1]}")
    print(f"migration: {MIGRATION_PATH.relative_to(ROOT)}")
    print()

    payload: dict[str, Any] = {
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "migration_file": str(MIGRATION_PATH.relative_to(ROOT)),
        "operator_overrides": {
            "soak_guard_bypass": True,
            "authorisation": "operator chat 2026-05-10 A.B.C",
        },
        "allow_list": list(ALLOW_LIST),
    }

    conn = await asyncpg.connect(dsn, ssl="require")
    try:
        # Step 1: 3-branch pre-flight
        print("[Step 1] Pre-flight pg_depend enumeration (R2.5, 3-branch)...")
        preflight = await run_preflight(conn)
        payload["preflight"] = preflight
        total_unexpected = (
            len(preflight["direct"])
            + len(preflight["pg_rewrite"])
            + len(preflight["pg_constraint"])
        )
        for label in ("direct", "pg_rewrite", "pg_constraint"):
            print(f"  branch={label:14s} unexpected={len(preflight[label])}")
            for r in preflight[label]:
                print(f"    - {r['dependent_object']} (kind={r['dependent_kind']}) -> {r['legacy_table']}")
        if total_unexpected:
            print("  BLOCKED: unexpected dependents detected.")
            payload["result"] = "BLOCKED_PREFLIGHT"
            payload["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
            append_log(payload)
            return 3
        print("  OK — all 3 branches clean.")

        existing_before = await existing_legacy_tables(conn)
        payload["legacy_tables_present_before"] = existing_before
        print(f"[Step 2] Legacy public.* objects present BEFORE drop: {len(existing_before)}")
        for q in existing_before:
            print(f"    {q}")

        # Step 3: per-statement DROP (single transaction). Soak guard skipped
        # by extracting only the DROP block — operator-approved one-shot.
        print(f"[Step 3] Executing {len(DROP_STATEMENTS)} DROP statements (RESTRICT, single transaction)...")
        drop_results: list[dict[str, Any]] = []
        async with conn.transaction():
            for label, stmt in DROP_STATEMENTS:
                try:
                    cmd_tag = await conn.execute(stmt)
                    drop_results.append({"target": label, "status": "OK", "tag": cmd_tag})
                    print(f"  {label:42s} -> {cmd_tag}")
                except Exception as e:
                    drop_results.append({"target": label, "status": "ERROR", "error": f"{type(e).__name__}: {e}"})
                    print(f"  {label:42s} -> ERROR {type(e).__name__}: {e}")
                    raise

            # Record migration as applied INSIDE the same transaction.
            checksum = await record_migration(conn, sql_text)
            payload["migration_checksum_sha256"] = checksum

        payload["drop_results"] = drop_results

        # Step 4: post-drop verification
        existing_after = await existing_legacy_tables(conn)
        payload["legacy_tables_present_after"] = existing_after
        payload["post_drop_counts"] = await post_drop_counts(conn)
        print(f"[Step 4] Legacy public.* tables present AFTER drop: {len(existing_after)}")
        for q in existing_after:
            print(f"    LEAKED {q}")
    finally:
        await conn.close()

    # Step 5: login verifier
    payload["login_verification"] = await verify_logins()
    payload["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    payload["result"] = (
        "OK"
        if not payload["legacy_tables_present_after"]
        and all(ln.startswith("OK") for ln in payload["login_verification"])
        else "FAIL"
    )

    append_log(payload)
    if payload["result"] != "OK":
        print("BLOCKED: post-DROP verification failed.")
        return 2
    print("DONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
