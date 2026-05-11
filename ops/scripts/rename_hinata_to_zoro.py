# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_users).
# One-shot rename tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Rename kg_users row for Auth ID a57e1f2f-... from Hinata to Zoro."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "supabase" / ".env")

TOKEN = os.environ["SUPABASE_ACCESS_TOKEN"]
URL = os.environ["SUPABASE_URL"]
PROJECT_REF = URL.split("//", 1)[1].split(".", 1)[0]

AUTH_ID = "a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e"

SQL = f"""
UPDATE kg_users
   SET display_name = 'Zoro',
       email        = 'zoro@zettelkasten.test',
       updated_at   = now()
 WHERE render_user_id = '{AUTH_ID}';

UPDATE auth.users
   SET email        = 'zoro@zettelkasten.test',
       raw_user_meta_data =
         COALESCE(raw_user_meta_data, '{{}}'::jsonb)
         || jsonb_build_object('display_name', 'Zoro', 'name', 'Zoro')
 WHERE id = '{AUTH_ID}';

SELECT id, render_user_id, email, display_name FROM kg_users ORDER BY email;
"""

resp = httpx.post(
    f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    json={"query": SQL},
    timeout=60.0,
)
print(f"HTTP {resp.status_code}")
print(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text)
