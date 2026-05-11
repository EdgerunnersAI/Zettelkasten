# LEGACY (broken after 2026-05-11): references dropped v1 rate-limit surface.
# One-shot smoke-probe counter reset — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""iter-08 one-shot: reset the rag-smoke probe user's monthly rag_question
counter so the deploy smoke probe stops returning HTTP 402 quota_exhausted.

The smoke probe in ops/deploy/deploy.sh:233-296 mints a fresh JWT for
`naruto@zettelkasten.local` and fires a canonical RAG query against the new
green container. If Naruto's monthly meter (default 30 questions/month for the
free tier) is exhausted, the deploy aborts even though the iter-08 code is
running correctly.

Per CLAUDE.md the user-pricing gate is NOT bypassable — operator action is
required. This script DELETES the current month's pricing_usage_counters row
for naruto+rag_question, restoring a clean monthly allowance. The gate stays
fully armed for real users.

Usage:
    python ops/scripts/reset_naruto_smoke_meter.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
SMOKE_EMAIL = "naruto@zettelkasten.local"
METERS_TO_RESET = ("rag_question",)  # add more if other meters block smokes


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _project_ref(url: str) -> str:
    return (urlparse(url).hostname or "").split(".", 1)[0]


def main() -> int:
    env = _load_env(ENV_FILE)
    token = env.get("SUPABASE_ACCESS_TOKEN")
    url = env.get("SUPABASE_URL")
    if not token or not url:
        print("ERROR: SUPABASE_ACCESS_TOKEN or SUPABASE_URL missing in root .env")
        return 2
    ref = _project_ref(url)
    api = f"https://api.supabase.com/v1/projects/{ref}/database/query"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Resolve Naruto's auth.users.id (used as render_user_id text in pricing tables).
    print(f"Resolving user id for {SMOKE_EMAIL}...")
    resolve_sql = (
        "SELECT id::text AS render_user_id, email FROM auth.users "
        f"WHERE email = '{SMOKE_EMAIL}' LIMIT 1;"
    )
    resp = httpx.post(api, json={"query": resolve_sql}, headers=headers, timeout=60.0)
    if resp.status_code not in (200, 201):
        print(f"  FAIL ({resp.status_code}): {resp.text[:300]}")
        return 1
    rows = resp.json()
    if not rows:
        print(f"ERROR: no auth.users row for {SMOKE_EMAIL}")
        return 1
    render_user_id = rows[0]["render_user_id"]
    print(f"  found render_user_id={render_user_id}")

    # Show current counter state (for the operator's eyes).
    show_sql = (
        "SELECT meter, period_type, period_start, used_count "
        "FROM pricing_usage_counters "
        f"WHERE render_user_id = '{render_user_id}' "
        "ORDER BY period_start DESC, meter;"
    )
    print("\nCurrent counters (BEFORE):")
    resp = httpx.post(api, json={"query": show_sql}, headers=headers, timeout=60.0)
    print(f"  {resp.text[:500]}")

    # Delete this month's row(s) for the targeted meters.
    for meter in METERS_TO_RESET:
        del_sql = (
            "DELETE FROM pricing_usage_counters "
            f"WHERE render_user_id = '{render_user_id}' "
            f"AND meter = '{meter}' "
            "AND period_type = 'month' "
            "AND period_start = date_trunc('month', current_date)::date;"
        )
        print(f"\nResetting {meter} (monthly)...")
        resp = httpx.post(api, json={"query": del_sql}, headers=headers, timeout=60.0)
        if resp.status_code in (200, 201):
            print(f"  OK ({resp.status_code})")
        else:
            print(f"  FAIL ({resp.status_code}): {resp.text[:300]}")
            return 1

    # Verify.
    print("\nCounters (AFTER):")
    resp = httpx.post(api, json={"query": show_sql}, headers=headers, timeout=60.0)
    print(f"  {resp.text[:500]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
