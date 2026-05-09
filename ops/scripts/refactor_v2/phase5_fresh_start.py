"""Phase 5 fresh-start: archive + wipe + recreate 2 designated users + backfill.

Operator-approved plan: docs/superpowers/plans/2026-05-10-phase5-fresh-start.md
Run:
    python ops/scripts/refactor_v2/phase5_fresh_start.py

Reads SUPABASE_V2_* from .env / .env.v2 via website.core.supabase_v2.client.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
    get_v2_client,
    get_v2_database_url,
)

ARCHIVE_PATH = ROOT / "docs" / "db-v2" / "legacy-archive-2026-05-10.json"
LOG_PATH = ROOT / "docs" / "db-v2" / "backfill-runs" / "2026-05-10.log"

NARUTO = {
    "id": "f2105544-b73d-4946-8329-096d82f070d3",
    "email": "naruto@zettelkasten.local",
    "password": "Naruto2026!",
    "display_name": "Naruto",
}
ZORO = {
    "id": "a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e",
    "email": "zoro@zettelkasten.test",
    "password": "Zoro2026!",
    "display_name": "Zoro",
}

# Tables we touch — for BEFORE/AFTER counts.
COUNT_TABLES: list[tuple[str, str]] = [
    ("auth", "users"),
    ("core", "profiles"),
    ("core", "workspaces"),
    ("core", "workspace_members"),
    ("core", "usage_events"),
    ("core", "usage_aggregates"),
    ("core", "quotas"),
    ("content", "canonical_zettels"),
    ("content", "canonical_chunks"),
    ("content", "workspace_zettels"),
    ("content", "workspace_chunk_membership"),
    ("kg", "kg_nodes"),
    ("kg", "kg_edges"),
    ("kg", "kg_node_aliases"),
    ("kg", "chunk_node_mentions"),
    ("rag", "kastens"),
    ("rag", "kasten_zettels"),
    ("rag", "kasten_members"),
    ("rag", "chat_sessions"),
    ("rag", "chat_messages"),
    ("rag", "retrieval_signal_weights"),
    ("pipelines", "pipeline_runs"),
    ("pipelines", "pipeline_run_items"),
    ("pipelines", "nexus_provider_tokens"),
    ("billing", "pricing_plan_entitlements"),
    ("billing", "pricing_subscriptions"),
    ("public", "kg_users"),
    ("public", "kg_nodes"),
    ("public", "kg_links"),
    ("public", "kg_node_chunks"),
    ("public", "rag_sandboxes"),
    ("public", "rag_sandbox_members"),
    ("public", "chat_sessions"),
    ("public", "chat_messages"),
]


def jdefault(obj: Any) -> Any:
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    return str(obj)


async def gather_counts(conn: asyncpg.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for sch, tbl in COUNT_TABLES:
        try:
            out[f"{sch}.{tbl}"] = await conn.fetchval(f"SELECT count(*) FROM {sch}.{tbl}")
        except Exception as e:  # missing table = -1 sentinel
            out[f"{sch}.{tbl}"] = -1
            print(f"  count {sch}.{tbl}: missing ({type(e).__name__})")
    return out


async def archive_legacy(conn: asyncpg.Connection) -> dict[str, Any]:
    print("[Step 0] Archiving legacy state to JSON...")
    archive: dict[str, Any] = {
        "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        "tables": {},
    }

    async def grab(label: str, sql: str) -> None:
        try:
            rows = await conn.fetch(sql)
            archive["tables"][label] = [dict(r) for r in rows]
            print(f"  archived {label}: {len(rows)} rows")
        except Exception as e:
            archive["tables"][label] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  archive {label}: ERROR {e}")

    await grab("public.kg_users", "SELECT * FROM public.kg_users")
    await grab("public.kg_nodes", "SELECT * FROM public.kg_nodes")
    await grab("public.kg_node_chunks", "SELECT * FROM public.kg_node_chunks")
    await grab("public.kg_links", "SELECT * FROM public.kg_links")
    await grab("public.rag_sandboxes", "SELECT * FROM public.rag_sandboxes")
    await grab("public.rag_sandbox_members", "SELECT * FROM public.rag_sandbox_members")
    await grab("public.chat_sessions", "SELECT * FROM public.chat_sessions")
    await grab("public.chat_messages", "SELECT * FROM public.chat_messages")
    await grab(
        "auth.users",
        "SELECT id, email, email_confirmed_at, raw_user_meta_data, raw_app_meta_data, created_at FROM auth.users",
    )

    archive["counts_before"] = await gather_counts(conn)

    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(archive, default=jdefault, indent=2), encoding="utf-8")
    print(f"  wrote {ARCHIVE_PATH} ({ARCHIVE_PATH.stat().st_size} bytes)")
    return archive


async def wipe_data(conn: asyncpg.Connection) -> None:
    print("[Step 1] Wiping data (preserves billing seeds + scorer registry + embedding versions)...")
    # Order: deepest-FK leaves first. Use TRUNCATE ... CASCADE to be safe vs hidden FKs.
    statements = [
        # rag children
        "DELETE FROM rag.chat_messages",
        "DELETE FROM rag.chat_sessions",
        "DELETE FROM rag.kasten_zettels",
        "DELETE FROM rag.kasten_members",
        "DELETE FROM rag.kastens",
        "DELETE FROM rag.retrieval_signal_weights",
        # content children
        "DELETE FROM content.workspace_chunk_membership",
        "DELETE FROM content.workspace_zettels",
        "DELETE FROM content.canonical_chunks",
        "DELETE FROM content.canonical_zettels",
        # kg
        "DELETE FROM kg.kg_node_aliases",
        "DELETE FROM kg.chunk_node_mentions",
        "DELETE FROM kg.kg_edges",
        "DELETE FROM kg.kg_nodes",
        # core (workspaces/profiles will also CASCADE from auth.users wipe; explicit for safety)
        "DELETE FROM core.usage_events",
        "DELETE FROM core.usage_aggregates",
        "DELETE FROM core.quotas",
        "DELETE FROM core.soft_delete_queue",
        "DELETE FROM core.workspace_members",
        "DELETE FROM core.workspaces",
        "DELETE FROM core.profiles",
        # pipelines
        "DELETE FROM pipelines.pipeline_run_items",
        "DELETE FROM pipelines.pipeline_runs",
        "DELETE FROM pipelines.nexus_provider_tokens",
        # legacy public schema
        "DELETE FROM public.chat_messages",
        "DELETE FROM public.chat_sessions",
        "DELETE FROM public.rag_sandbox_members",
        "DELETE FROM public.rag_sandboxes",
        "DELETE FROM public.kg_node_chunks",
        "DELETE FROM public.kg_links",
        "DELETE FROM public.kg_nodes",
        "DELETE FROM public.kg_users",
        # auth.users last — CASCADE will catch anything we missed
        "DELETE FROM auth.users",
    ]
    for sql in statements:
        try:
            res = await conn.execute(sql)
            print(f"  {sql:64s} -> {res}")
        except Exception as e:
            print(f"  {sql}: ERROR {type(e).__name__}: {e}")
            raise


async def create_users_direct(conn: asyncpg.Connection) -> None:
    """Direct INSERT into auth.users with stable IDs + bcrypt-hashed passwords.

    Mirrors what supabase admin.create_user(email_confirm=True) writes:
    - encrypted_password = crypt(password, gen_salt('bf'))
    - email_confirmed_at = now()
    - aud='authenticated', role='authenticated', instance_id=00000000-...
    """
    print("[Step 2] Creating users directly in auth.users with stable IDs...")
    for u in (NARUTO, ZORO):
        await conn.execute(
            """
            INSERT INTO auth.users (
                instance_id, id, aud, role, email,
                encrypted_password, email_confirmed_at,
                raw_app_meta_data, raw_user_meta_data,
                created_at, updated_at,
                confirmation_token, recovery_token,
                email_change, email_change_token_new,
                is_super_admin, is_sso_user, is_anonymous
            ) VALUES (
                '00000000-0000-0000-0000-000000000000', $1::uuid, 'authenticated', 'authenticated', $2::text,
                crypt($3::text, gen_salt('bf')), now(),
                jsonb_build_object('provider','email','providers',jsonb_build_array('email')),
                jsonb_build_object('name', $4::text),
                now(), now(),
                '', '', '', '',
                false, false, false
            )
            """,
            u["id"], u["email"], u["password"], u["display_name"],
        )
        # Also seed the email-identity row that supabase auth expects for password sign-in.
        await conn.execute(
            """
            INSERT INTO auth.identities (
                provider_id, user_id, identity_data, provider,
                last_sign_in_at, created_at, updated_at
            ) VALUES (
                $1::text, $2::uuid,
                jsonb_build_object('sub', $2::text, 'email', $1::text, 'email_verified', true, 'phone_verified', false),
                'email',
                now(), now(), now()
            )
            """,
            u["email"], u["id"],
        )
        print(f"  created auth.users + auth.identities for {u['email']} ({u['id']})")


async def ensure_profiles_workspaces(conn: asyncpg.Connection) -> dict[str, str]:
    """Make sure each user has profile + personal workspace + member row.

    The on_auth_user_created trigger should populate core.profiles, which then
    fires create_personal_workspace. Verify and patch if any link is missing.
    """
    print("[Step 3] Verifying trigger-driven profile/workspace rows...")
    workspaces: dict[str, str] = {}
    for u in (NARUTO, ZORO):
        prof = await conn.fetchval("SELECT id FROM core.profiles WHERE id=$1::uuid", u["id"])
        if prof is None:
            await conn.execute(
                """INSERT INTO core.profiles (id, email, display_name)
                   VALUES ($1::uuid, $2::text, $3::text)""",
                u["id"], u["email"], u["display_name"],
            )
            print(f"  patched core.profiles for {u['email']}")
        else:
            # Ensure display_name set.
            await conn.execute(
                "UPDATE core.profiles SET display_name=$2::text, email=$3::text WHERE id=$1::uuid",
                u["id"], u["display_name"], u["email"],
            )

        ws_id = await conn.fetchval(
            """SELECT id FROM core.workspaces
                WHERE owner_profile_id=$1::uuid AND is_personal LIMIT 1""",
            u["id"],
        )
        if ws_id is None:
            ws_id = await conn.fetchval(
                """INSERT INTO core.workspaces (owner_profile_id, name, is_personal)
                   VALUES ($1::uuid, $2::text, true) RETURNING id""",
                u["id"], f"{u['display_name']} Personal",
            )
            print(f"  patched core.workspaces for {u['email']} -> {ws_id}")
        # Ensure member row.
        await conn.execute(
            """INSERT INTO core.workspace_members (workspace_id, profile_id, role)
               VALUES ($1::uuid, $2::uuid, 'owner') ON CONFLICT DO NOTHING""",
            ws_id, u["id"],
        )
        workspaces[u["email"]] = str(ws_id)
        print(f"  {u['email']}: profile + workspace {ws_id} confirmed")
    return workspaces


async def backfill_kg_nodes(conn: asyncpg.Connection, archive: dict[str, Any], naruto_ws: str) -> dict[str, int]:
    """Backfill the 7 legacy kg_nodes -> content.canonical_zettels + workspace_zettels in Naruto's ws."""
    print("[Step 4] Backfilling legacy kg_nodes -> Naruto's workspace...")
    legacy_nodes = archive["tables"].get("public.kg_nodes", [])
    if not isinstance(legacy_nodes, list):
        print("  no legacy kg_nodes archived — skipping")
        return {"canonical_inserted": 0, "workspace_zettels_inserted": 0, "skipped_no_url": 0}

    # Deduplicate by url (legacy data has 3 copies of test fixtures across users).
    seen_urls: set[str] = set()
    unique_nodes: list[dict[str, Any]] = []
    for n in legacy_nodes:
        url = (n.get("url") or "").strip()
        if not url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        unique_nodes.append(n)

    valid_source_types = {"youtube", "reddit", "github", "twitter", "substack",
                          "newsletter", "medium", "web", "generic"}

    canonical_inserted = 0
    workspace_inserted = 0
    skipped = 0
    for n in unique_nodes:
        url = n.get("url") or ""
        if not url:
            skipped += 1
            continue
        title = (n.get("name") or "").strip() or "(untitled)"
        src = (n.get("source_type") or "web").lower()
        if src not in valid_source_types:
            src = "web"
        # Synthesised content_hash (sha256 of title+url) — not real chunk hash but consistent.
        content_hash = hashlib.sha256(f"{title}\x00{url}".encode("utf-8")).digest()
        # body_md from summary jsonb if available
        summary = n.get("summary") or {}
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except Exception:
                summary = {"raw": summary}
        if not isinstance(summary, dict):
            summary = {}
        body_md = summary.get("long_summary") or summary.get("summary") or summary.get("short_summary") or ""

        node_date = n.get("node_date")
        if isinstance(node_date, str):
            try:
                node_date = datetime.fromisoformat(node_date).date()
            except Exception:
                node_date = None
        elif isinstance(node_date, datetime):
            node_date = node_date.date()

        metadata = n.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {"raw": metadata}

        # Use the upsert RPC.
        row = await conn.fetchrow(
            """SELECT id, was_new
                 FROM content.upsert_canonical_zettel(
                     $1::text, $2::bytea, $3::text, $4::text, $5::text, $6::date, $7::jsonb)""",
            url, content_hash, src, title, body_md, node_date, json.dumps(metadata),
        )
        canonical_id = row["id"]
        if row["was_new"]:
            canonical_inserted += 1

        tags_raw = n.get("tags") or []
        if isinstance(tags_raw, str):
            try:
                tags_raw = json.loads(tags_raw)
            except Exception:
                tags_raw = [tags_raw]
        if not isinstance(tags_raw, list):
            tags_raw = []
        tags_clean: list[str] = [str(t) for t in tags_raw if t]

        ws_zettel_id = await conn.fetchval(
            """INSERT INTO content.workspace_zettels (
                   workspace_id, canonical_zettel_id, ai_summary, ai_summary_engine_version,
                   user_tags, added_via)
               VALUES ($1::uuid, $2::uuid, $3::text, 'legacy-v1-backfill', $4::text[], 'migration')
               ON CONFLICT (workspace_id, canonical_zettel_id) DO NOTHING
               RETURNING id""",
            naruto_ws, canonical_id, body_md or None, tags_clean,
        )
        if ws_zettel_id is not None:
            workspace_inserted += 1
        print(f"  {url[:60]:60s} -> canonical={canonical_id} new={row['was_new']}")

    return {
        "legacy_nodes_total": len(legacy_nodes),
        "unique_by_url": len(unique_nodes),
        "canonical_inserted": canonical_inserted,
        "workspace_zettels_inserted": workspace_inserted,
        "skipped_no_url": skipped,
    }


async def verify_logins() -> list[str]:
    """Sign in via the v2 ANON client and return verification lines."""
    print("[Step 7] Verifying email/password login via supabase anon client...")
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


def write_log(payload: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(payload, default=jdefault, indent=2), encoding="utf-8")
    print(f"[Step 8] wrote {LOG_PATH}")


async def main() -> int:
    dsn = get_v2_database_url()
    print(f"DB v2 DSN: {dsn.split('@')[-1]}")
    conn = await asyncpg.connect(dsn, ssl="require")
    payload: dict[str, Any] = {"started_at": datetime.now(tz=timezone.utc).isoformat()}
    try:
        archive = await archive_legacy(conn)
        payload["counts_before"] = archive["counts_before"]

        async with conn.transaction():
            await wipe_data(conn)

        # Verify zero counts on touched tables (excl. seed-only ones)
        zero_check = await gather_counts(conn)
        payload["counts_after_wipe"] = zero_check
        for key, n in zero_check.items():
            if key.startswith("billing.") or n in (-1, 0):
                continue
            print(f"  WARN non-zero after wipe: {key}={n}")

        await create_users_direct(conn)
        workspaces = await ensure_profiles_workspaces(conn)
        payload["workspaces"] = workspaces

        backfill = await backfill_kg_nodes(conn, archive, workspaces[NARUTO["email"]])
        payload["backfill"] = backfill

        # Skip rag_sandboxes backfill — archive shows 0 rows.
        # Skip kg_links / kg_node_chunks backfill — archive shows 0 rows.

        payload["counts_after"] = await gather_counts(conn)
    finally:
        await conn.close()

    payload["login_verification"] = await verify_logins()
    payload["finished_at"] = datetime.now(tz=timezone.utc).isoformat()

    write_log(payload)
    if not all(ln.startswith("OK") for ln in payload["login_verification"]):
        print("BLOCKED: at least one login failed.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
