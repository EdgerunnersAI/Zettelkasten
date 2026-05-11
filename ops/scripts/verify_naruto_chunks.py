# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_node_chunks).
# One-shot verification helper — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Phase A A4 verification helper.

Counts Naruto's ``kg_node_chunks`` rows before/after a fresh capture on
``https://www.zettelkasten.in``. Reads the service-role key from
``supabase/.env`` internally — never echoes secrets to stdout.

Usage:
    python ops/scripts/verify_naruto_chunks.py baseline   # snapshot count
    python ops/scripts/verify_naruto_chunks.py check      # recount + delta
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request

_NARUTO_AUTH_ID = "f2105544-b73d-4946-8329-096d82f070d3"
_SNAPSHOT_PATH = pathlib.Path(__file__).parent / ".naruto_chunks_baseline.json"


def _load_env() -> tuple[str, str]:
    env_file = pathlib.Path(__file__).parents[2] / "supabase" / ".env"
    url = key = None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name == "SUPABASE_URL":
            url = value
        elif name == "SUPABASE_SERVICE_ROLE_KEY":
            key = value
    if not url or not key:
        raise SystemExit("supabase/.env missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    return url, key


def _rest_get(path: str, url: str, key: str) -> list[dict]:
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Prefer": "count=exact",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else []


def _count(path: str, url: str, key: str) -> int:
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Prefer": "count=exact",
            "Range": "0-0",
        },
        method="HEAD",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_range = resp.headers.get("content-range", "*/0")
        try:
            return int(content_range.rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            return 0


def _get_naruto_kg_user_id(url: str, key: str) -> str:
    rows = _rest_get(
        f"kg_users?render_user_id=eq.{_NARUTO_AUTH_ID}&select=id,render_user_id",
        url,
        key,
    )
    if not rows:
        raise SystemExit(f"No kg_users row for Naruto auth id {_NARUTO_AUTH_ID}")
    return rows[0]["id"]


def _latest_chunks(user_id: str, url: str, key: str, limit: int = 5) -> list[dict]:
    return _rest_get(
        f"kg_node_chunks?user_id=eq.{user_id}&select=node_id,chunk_idx,chunk_type,token_count,created_at&order=created_at.desc&limit={limit}",
        url,
        key,
    )


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "check").lower()
    url, key = _load_env()
    kg_user_id = _get_naruto_kg_user_id(url, key)
    total = _count(f"kg_node_chunks?user_id=eq.{kg_user_id}", url, key)
    latest = _latest_chunks(kg_user_id, url, key, limit=5)

    if mode == "baseline":
        _SNAPSHOT_PATH.write_text(
            json.dumps({"ts": time.time(), "kg_user_id": kg_user_id, "total": total}, indent=2),
            encoding="utf-8",
        )
        print(f"BASELINE kg_user_id={kg_user_id} total_chunks={total}")
        for row in latest:
            print(f"  node_id={row['node_id']} chunk_idx={row['chunk_idx']} kind={row['chunk_type']} created_at={row['created_at']}")
        return 0

    if mode == "check":
        if not _SNAPSHOT_PATH.exists():
            print(f"NO_BASELINE total_chunks={total}")
            return 1
        base = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        delta = total - base["total"]
        status = "PASS" if delta >= 1 else "FAIL"
        print(f"{status} kg_user_id={kg_user_id} total_chunks={total} baseline={base['total']} delta={delta}")
        for row in latest:
            print(f"  node_id={row['node_id']} chunk_idx={row['chunk_idx']} kind={row['chunk_type']} created_at={row['created_at']}")
        return 0 if delta >= 1 else 2

    raise SystemExit(f"unknown mode {mode!r}; use baseline|check")


if __name__ == "__main__":
    sys.exit(main())
