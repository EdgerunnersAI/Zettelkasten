# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_*/rag_*/etc.).
# One-shot wipe tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""One-shot wipe: delete all Zettels (kg_nodes + links + chunks) and
Kastens (rag_sandboxes + members + chat sessions/messages) across all users.

Preserves kg_users. Uses Supabase Management API with SUPABASE_ACCESS_TOKEN.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "supabase" / ".env")

TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
URL = os.environ["SUPABASE_URL"]
PROJECT_REF = URL.split("//", 1)[1].split(".", 1)[0]

SQL = """
TRUNCATE TABLE
    chat_messages,
    chat_sessions,
    rag_sandbox_members,
    rag_sandboxes,
    kg_node_chunks,
    kg_links,
    kg_nodes
RESTART IDENTITY CASCADE;

SELECT
    (SELECT COUNT(*) FROM kg_nodes)            AS kg_nodes,
    (SELECT COUNT(*) FROM kg_links)            AS kg_links,
    (SELECT COUNT(*) FROM kg_node_chunks)      AS kg_node_chunks,
    (SELECT COUNT(*) FROM rag_sandboxes)       AS rag_sandboxes,
    (SELECT COUNT(*) FROM rag_sandbox_members) AS rag_sandbox_members,
    (SELECT COUNT(*) FROM chat_sessions)       AS chat_sessions,
    (SELECT COUNT(*) FROM chat_messages)       AS chat_messages,
    (SELECT COUNT(*) FROM kg_users)            AS kg_users_preserved;
"""


def main() -> None:
    print(f"Project: {PROJECT_REF}")
    print("Executing wipe...")
    resp = httpx.post(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        json={"query": SQL},
        timeout=60.0,
    )
    print(f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    print(body)
    if resp.status_code >= 400:
        sys.exit("FAILED")


if __name__ == "__main__":
    main()
