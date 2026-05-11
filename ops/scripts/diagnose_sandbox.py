# LEGACY (broken after 2026-05-11): references dropped public.kg_* tables (kg_users,
# kg_nodes, kg_node_chunks, rag_sandbox_members). To revive, port to v2 schema
# (workspaces / content_nodes / chunks) via website.core.supabase_v2.client.
"""Diagnose user_id wiring for a kasten sandbox.

Prints:
  - sandbox row (id, user_id, name)
  - kg_users rows referenced by that user_id (kg.id, render_user_id, name)
  - rag_sandbox_members count + distinct user_ids for that sandbox
  - kg_nodes count + distinct user_ids for the member node_ids
  - kg_node_chunks count for those node_ids

Helps identify mismatches between sandbox.user_id, member.user_id, node.user_id,
and the auth-mapped kg_user.id.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
        python ops/scripts/diagnose_sandbox.py --sandbox-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from supabase import create_client


def _client():
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.environ["SUPABASE_ANON_KEY"].strip()
    return create_client(url, key)


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sandbox-id", required=True)
    args = p.parse_args()

    sb = _client()

    _print_section(f"sandbox {args.sandbox_id}")
    sandbox = sb.table("rag_sandboxes").select("id,user_id,description,created_at").eq("id", args.sandbox_id).execute().data
    print(json.dumps(sandbox, indent=2, default=str))
    if not sandbox:
        print("NO sandbox row found")
        return 0
    sandbox_user_id = sandbox[0]["user_id"]

    _print_section(f"kg_users for sandbox.user_id={sandbox_user_id}")
    users = sb.table("kg_users").select("id,render_user_id,created_at").eq("id", sandbox_user_id).execute().data
    print(json.dumps(users, indent=2, default=str))

    _print_section("all kg_users (id, render_user_id)")
    all_users = sb.table("kg_users").select("id,render_user_id").execute().data
    print(json.dumps(all_users, indent=2, default=str))

    _print_section(f"rag_sandbox_members where sandbox_id={args.sandbox_id}")
    members = sb.table("rag_sandbox_members").select("user_id,node_id,added_via,added_at").eq("sandbox_id", args.sandbox_id).execute().data
    print(f"member_count={len(members)}")
    user_ids = Counter(m["user_id"] for m in members)
    print(f"distinct user_ids in members: {dict(user_ids)}")
    print(f"first 10 nodes: {[m['node_id'] for m in members[:10]]}")

    if not members:
        return 0

    node_ids = [m["node_id"] for m in members]

    _print_section(f"kg_nodes for {len(node_ids)} member node_ids")
    nodes = sb.table("kg_nodes").select("id,user_id,source_type").in_("id", node_ids).execute().data
    print(f"node_count={len(nodes)}")
    node_user_ids = Counter(n["user_id"] for n in nodes)
    print(f"distinct user_ids in kg_nodes: {dict(node_user_ids)}")

    _print_section(f"kg_node_chunks for {len(node_ids)} member node_ids")
    chunks = sb.table("kg_node_chunks").select("node_id", count="exact").in_("node_id", node_ids).execute()
    print(f"chunk_count={chunks.count}")

    _print_section("rag_resolve_effective_nodes RPC call (sandbox-only)")
    try:
        rpc_resp = sb.rpc("rag_resolve_effective_nodes", {
            "p_user_id": sandbox_user_id,
            "p_sandbox_id": args.sandbox_id,
            "p_node_ids": None,
            "p_tags": None,
            "p_tag_mode": "all",
            "p_source_types": None,
        }).execute()
        rpc_rows = rpc_resp.data or []
        print(f"resolver returned {len(rpc_rows)} rows")
        print(f"first 5: {[r['node_id'] for r in rpc_rows[:5]]}")
    except Exception as exc:
        print(f"RPC call failed: {exc}")

    _print_section("kg_node_chunks count by user_id (filtered to member node_ids)")
    chunk_rows = sb.table("kg_node_chunks").select("node_id,user_id").in_("node_id", node_ids).execute().data or []
    chunk_user_ids = Counter(c.get("user_id") for c in chunk_rows)
    print(f"distinct user_ids in chunks: {dict(chunk_user_ids)}")

    _print_section("MISMATCH SUMMARY")
    mm = []
    if user_ids and len(user_ids) > 1:
        mm.append(f"members table has {len(user_ids)} distinct user_ids (should be 1)")
    if node_user_ids and len(node_user_ids) > 1:
        mm.append(f"kg_nodes for these node_ids span {len(node_user_ids)} user_ids (should be 1)")
    if user_ids and node_user_ids:
        mu = set(user_ids); nu = set(node_user_ids)
        if mu != nu:
            mm.append(f"members.user_ids {mu} != kg_nodes.user_ids {nu}")
    if user_ids and sandbox_user_id not in user_ids:
        mm.append(f"sandbox.user_id={sandbox_user_id} NOT in members.user_ids {set(user_ids)}")
    if mm:
        for m in mm:
            print(f"  ⚠ {m}")
    else:
        print("  ✓ no mismatches detected at table-row level")

    return 0


if __name__ == "__main__":
    sys.exit(main())
