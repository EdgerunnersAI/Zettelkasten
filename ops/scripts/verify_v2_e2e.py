"""End-to-end exerciser for the v2 write path.

Mints a real auth user via Supabase admin API, signs them in, and POSTs to
/api/summarize. After the request, queries both schemas and reports which
landed data:

- public.kg_nodes (v1 / legacy) — should be empty in pure v2 mode
- content.workspace_zettels (v2) — should get a row
- content.canonical_zettels (v2) — should get a row (or hit existing dedup)
- content.canonical_chunks (v2) — should get N rows for the chunked body
- content.workspace_chunk_membership (v2) — N rows linking workspace to chunks

Usage:
    PYTHONIOENCODING=utf-8 python ops/scripts/verify_v2_e2e.py

Reports row counts before/after and per-table delta.
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure project root on sys.path for `from website.app import ...`
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncpg


def load_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


async def snapshot_counts(conn: asyncpg.Connection) -> dict[str, int]:
    # v1 public.kg_* tables dropped in Phase 6 — counts now come from v2 schemas only.
    return {
        "core.profiles": await conn.fetchval("SELECT COUNT(*) FROM core.profiles"),
        "core.workspaces": await conn.fetchval("SELECT COUNT(*) FROM core.workspaces"),
        "content.canonical_zettels": await conn.fetchval("SELECT COUNT(*) FROM content.canonical_zettels"),
        "content.canonical_chunks": await conn.fetchval("SELECT COUNT(*) FROM content.canonical_chunks"),
        "content.workspace_zettels": await conn.fetchval("SELECT COUNT(*) FROM content.workspace_zettels"),
        "content.workspace_chunk_membership": await conn.fetchval(
            "SELECT COUNT(*) FROM content.workspace_chunk_membership"
        ),
    }


async def main() -> int:
    load_env()
    os.environ["DB_SCHEMA_VERSION"] = "v2"
    os.environ.setdefault("ENV", "dev")

    db_url = os.environ.get("SUPABASE_DATABASE_URL")
    if not db_url:
        print("ERROR: SUPABASE_DATABASE_URL not set", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(db_url)
    try:
        print("=" * 70)
        print("BEFORE")
        print("=" * 70)
        before = await snapshot_counts(conn)
        for k, v in before.items():
            print(f"  {k:50} {v}")

        # Boot FastAPI + post /api/summarize against a real test user
        # Create a real auth.users row + profile/workspace via the supabase admin API
        from supabase import create_client
        supa_url = os.environ["SUPABASE_URL"]
        service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        admin = create_client(supa_url, service_key)

        # Create a fresh test user
        from uuid import uuid4
        email = f"e2e-{uuid4().hex[:8]}@test.com"
        password = "x" * 16
        try:
            user_resp = admin.auth.admin.create_user({
                "email": email, "password": password, "email_confirm": True,
            })
            new_user_id = user_resp.user.id
            print(f"\nCreated test user: {email} (id={new_user_id})")
        except Exception as e:
            print(f"\nERROR creating test user: {e}", file=sys.stderr)
            return 2

        # Sign in to get JWT
        anon_key = os.environ["SUPABASE_ANON_KEY"]
        client = create_client(supa_url, anon_key)
        sess = client.auth.sign_in_with_password({"email": email, "password": password})
        jwt = sess.session.access_token
        print(f"Signed in. JWT length={len(jwt)}")

        # Boot FastAPI inline + post
        from website.app import create_app
        from fastapi.testclient import TestClient
        app = create_app()
        with TestClient(app) as tc:
            # Use a real URL the summariser can actually fetch.
            # Reddit is reliable + has a known shape the engine handles.
            test_url = "https://docs.python.org/3/whatsnew/3.12.html"
            r = tc.post(
                "/api/summarize",
                json={"url": test_url},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            print(f"\nPOST /api/summarize → {r.status_code}: {r.text[:200]}")

            r2 = tc.get("/api/graph", headers={"Authorization": f"Bearer {jwt}"})
            graph_data = r2.json() if r2.status_code == 200 else {}
            print(f"GET  /api/graph     → {r2.status_code}, {len(graph_data.get('nodes', []))} nodes returned")

        # Snapshot after
        print("\n" + "=" * 70)
        print("AFTER")
        print("=" * 70)
        after = await snapshot_counts(conn)
        for k in before:
            delta = after[k] - before[k]
            marker = " <-- DELTA" if delta != 0 else ""
            print(f"  {k:50} {after[k]:>5} ({delta:+d}){marker}")

        # Cleanup
        try:
            admin.auth.admin.delete_user(new_user_id)
            print(f"\nDeleted test user {email}")
        except Exception as e:
            print(f"\nWarning: failed to delete test user: {e}")

        # Verdict — public.kg_* dropped in Phase 6, so only v2 growth is meaningful.
        print("\n" + "=" * 70)
        v2_growth = (after["content.workspace_zettels"] - before["content.workspace_zettels"]) > 0
        if v2_growth:
            print("VERDICT: PURE v2 (writes landed in content.* only) ✓")
        else:
            print("VERDICT: NO WRITES landed (content.workspace_zettels unchanged) — likely auth/route failure")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
