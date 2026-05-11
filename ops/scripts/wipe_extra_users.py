# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_users).
# One-shot user-cleanup tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Keep only Naruto and Zoro; delete all other users (kg_users + auth.users).

Cascades remove their nodes/links/sandboxes/chat data automatically.
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

KEEP = (
    "f2105544-b73d-4946-8329-096d82f070d3",  # Naruto
    "a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e",  # Zoro
)

SQL = f"""
-- Before
SELECT 'before_kg_users' AS label, COUNT(*) AS n FROM kg_users
UNION ALL SELECT 'before_auth_users', COUNT(*) FROM auth.users;

-- Delete non-kept kg_users (cascades to nodes/links/sandboxes/chat via FKs)
DELETE FROM kg_users
 WHERE render_user_id NOT IN ('{KEEP[0]}', '{KEEP[1]}');

-- Delete non-kept auth.users
DELETE FROM auth.users
 WHERE id NOT IN ('{KEEP[0]}', '{KEEP[1]}');

-- After
SELECT 'after_kg_users' AS label, COUNT(*) AS n FROM kg_users
UNION ALL SELECT 'after_auth_users', COUNT(*) FROM auth.users
UNION ALL SELECT 'kg_nodes', COUNT(*) FROM kg_nodes
UNION ALL SELECT 'kg_links', COUNT(*) FROM kg_links
UNION ALL SELECT 'rag_sandboxes', COUNT(*) FROM rag_sandboxes
UNION ALL SELECT 'chat_sessions', COUNT(*) FROM chat_sessions;

SELECT id, render_user_id, email, display_name FROM kg_users ORDER BY email;
"""


def main() -> None:
    print(f"Project: {PROJECT_REF}")
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
        print(resp.json())
    except Exception:
        print(resp.text)
    if resp.status_code >= 400:
        sys.exit("FAILED")


if __name__ == "__main__":
    main()
