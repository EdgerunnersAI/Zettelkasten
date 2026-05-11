# LEGACY (broken after 2026-05-11): targets dropped v1 kg_* tables.
# One-shot v1 cutover tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Migrate graph.json data into Supabase.

Usage:
    python ops/scripts/migrate_graph_to_supabase.py

Prerequisites:
    1. Tables must exist in Supabase (run supabase/website/kg_public/schema.sql first)
    2. Credentials in root .env (consolidated from former supabase/.env)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from uuid import UUID

# Add project root to path (ops/scripts/file.py -> ops/ -> project root)
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

# Load Supabase credentials from root .env
load_dotenv(ROOT / ".env")

import httpx
import os

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

GRAPH_JSON = ROOT / "website" / "features" / "knowledge_graph" / "content" / "graph.json"
SCHEMA_SQL = ROOT / "supabase" / "website" / "kg_public" / "schema.sql"

# Map graph.json group names to source_type values
GROUP_TO_SOURCE = {
    "youtube": "youtube",
    "reddit": "reddit",
    "github": "github",
    "substack": "substack",
    "medium": "medium",
    "generic": "web",
    "web": "web",
}


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def check_tables_exist() -> bool:
    """Check if kg_users table exists by attempting a SELECT."""
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_users",
        headers=_headers(),
        params={"select": "id", "limit": "1"},
    )
    return resp.status_code == 200


def apply_schema() -> bool:
    """Attempt to apply schema SQL. Returns True if tables are ready."""
    if check_tables_exist():
        print("[OK] Tables already exist in Supabase")
        return True

    print("[..] Tables not found. Attempting to apply schema...")

    sql = SCHEMA_SQL.read_text(encoding="utf-8")

    # Try Supabase's internal SQL endpoint (used by dashboard)
    for endpoint in ["/pg/query", "/rest/v1/rpc/exec_sql"]:
        try:
            resp = httpx.post(
                f"{SUPABASE_URL}{endpoint}",
                headers=_headers(),
                json={"query": sql},
                timeout=30.0,
            )
            if resp.status_code in (200, 201):
                print(f"[OK] Schema applied via {endpoint}")
                return True
            else:
                print(f"[--] {endpoint} returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[--] {endpoint} failed: {e}")

    # Fallback: print instructions
    print()
    print("=" * 70)
    print("ACTION REQUIRED: Apply schema manually")
    print("=" * 70)
    print(f"1. Open: {SUPABASE_URL.replace('.supabase.co', '.supabase.com/dashboard/project/' + SUPABASE_URL.split('//')[1].split('.')[0])}/sql/new")
    print(f"2. Paste contents of: {SCHEMA_SQL}")
    print("3. Click 'Run'")
    print("4. Re-run this script after tables are created")
    print("=" * 70)
    return False


def migrate_data() -> dict:
    """Insert graph.json nodes and links into Supabase. Returns stats."""
    graph = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    headers = _headers()

    stats = {"users": 0, "nodes": 0, "links": 0, "skipped_nodes": 0, "skipped_links": 0}

    # Step 1: Create default user
    print("[..] Creating default user...")
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_users",
        headers=headers,
        params={"select": "*", "render_user_id": "eq.default-web-user", "limit": "1"},
    )

    if resp.status_code == 200 and resp.json():
        user = resp.json()[0]
        user_id = user["id"]
        print(f"[OK] Default user exists: {user_id}")
    else:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/kg_users",
            headers=headers,
            json={
                "render_user_id": "default-web-user",
                "display_name": "Web User",
            },
        )
        if resp.status_code not in (200, 201):
            print(f"[FAIL] Could not create user: {resp.status_code} {resp.text}")
            return stats
        user = resp.json()[0]
        user_id = user["id"]
        stats["users"] = 1
        print(f"[OK] Created default user: {user_id}")

    # Step 2: Insert nodes (skip auto-link — we have curated links)
    print(f"[..] Inserting {len(graph['nodes'])} nodes...")

    for node in graph["nodes"]:
        source_type = GROUP_TO_SOURCE.get(node["group"], "web")

        payload = {
            "id": node["id"],
            "user_id": user_id,
            "name": node["name"],
            "source_type": source_type,
            "summary": node.get("summary", ""),
            "tags": node.get("tags", []),
            "url": node["url"],
            "node_date": node.get("date"),
            "metadata": {},
        }

        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/kg_nodes",
            headers=headers,
            json=payload,
        )

        if resp.status_code in (200, 201):
            stats["nodes"] += 1
        elif "duplicate key" in resp.text.lower() or "unique" in resp.text.lower():
            stats["skipped_nodes"] += 1
        else:
            print(f"  [WARN] Node {node['id']}: {resp.status_code} {resp.text[:100]}")
            stats["skipped_nodes"] += 1

    print(f"[OK] Inserted {stats['nodes']} nodes ({stats['skipped_nodes']} skipped/existing)")

    # Step 3: Insert links
    print(f"[..] Inserting {len(graph['links'])} links...")

    for link in graph["links"]:
        payload = {
            "user_id": user_id,
            "source_node_id": link["source"],
            "target_node_id": link["target"],
            "relation": link["relation"],
        }

        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/kg_links",
            headers=headers,
            json=payload,
        )

        if resp.status_code in (200, 201):
            stats["links"] += 1
        elif "duplicate key" in resp.text.lower() or "unique" in resp.text.lower():
            stats["skipped_links"] += 1
        else:
            print(f"  [WARN] Link {link['source']}->{link['target']}: {resp.status_code} {resp.text[:100]}")
            stats["skipped_links"] += 1

    print(f"[OK] Inserted {stats['links']} links ({stats['skipped_links']} skipped/existing)")

    return stats


def verify() -> bool:
    """Verify data was migrated correctly."""
    headers = _headers()

    # Count nodes
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_nodes",
        headers={**headers, "Prefer": "count=exact"},
        params={"select": "id"},
    )
    node_count = int(resp.headers.get("content-range", "0/0").split("/")[-1])

    # Count links
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_links",
        headers={**headers, "Prefer": "count=exact"},
        params={"select": "id"},
    )
    link_count = int(resp.headers.get("content-range", "0/0").split("/")[-1])

    graph = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    expected_nodes = len(graph["nodes"])
    expected_links = len(graph["links"])

    print(f"\n{'='*40}")
    print(f"Nodes: {node_count}/{expected_nodes}", "OK" if node_count >= expected_nodes else "MISMATCH")
    print(f"Links: {link_count}/{expected_links}", "OK" if link_count >= expected_links else "MISMATCH")
    print(f"{'='*40}")

    return node_count >= expected_nodes and link_count >= expected_links


def benchmark_get_graph() -> float:
    """Time a full graph fetch (matches GET /api/graph)."""
    headers = _headers()
    start = time.perf_counter()

    # Fetch nodes
    httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_nodes",
        headers=headers,
        params={"select": "*", "order": "node_date.desc"},
    )

    # Fetch links
    httpx.get(
        f"{SUPABASE_URL}/rest/v1/kg_links",
        headers=headers,
        params={"select": "*"},
    )

    elapsed = time.perf_counter() - start
    return elapsed


def main():
    print("Supabase KG Migration")
    print(f"URL: {SUPABASE_URL}")
    print(f"Graph: {GRAPH_JSON}")
    print()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[FAIL] Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in root .env")
        sys.exit(1)

    # Step 1: Check/apply schema
    if not apply_schema():
        sys.exit(1)

    # Step 2: Migrate data
    stats = migrate_data()

    # Step 3: Verify
    ok = verify()

    # Step 4: Benchmark
    print("\nBenchmarking full graph load (3 runs)...")
    times = []
    for i in range(3):
        t = benchmark_get_graph()
        times.append(t)
        print(f"  Run {i+1}: {t*1000:.0f}ms")

    avg = sum(times) / len(times)
    print(f"  Average: {avg*1000:.0f}ms {'(< 2s OK)' if avg < 2.0 else '(> 2s SLOW)'}")

    if ok and avg < 2.0:
        print("\n[SUCCESS] Migration complete. All checks passed.")
    elif ok:
        print(f"\n[WARN] Data OK but avg load time {avg*1000:.0f}ms > 2000ms target.")
    else:
        print("\n[FAIL] Data verification failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
