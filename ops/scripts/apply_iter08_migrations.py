# LEGACY (broken after 2026-05-11): targets dropped v1 migration surface.
# One-shot iter-08 migration runner — not in active production path.
# Revive: port to v2 _v2/ migration runner. Tracked for follow-up.
"""iter-08 one-shot: apply the 3 Supabase migrations via Management API.

Reads SUPABASE_ACCESS_TOKEN from the root .env and posts each migration to
/v1/projects/{ref}/database/query. Project ref is parsed from SUPABASE_URL.

Usage:
    python ops/scripts/apply_iter08_migrations.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
MIGRATION_DIR = REPO_ROOT / "supabase" / "website" / "kg_public" / "migrations"

MIGRATIONS = [
    "2026-05-03_rag_kasten_chunk_counts.sql",
    "2026-05-03_rag_entity_anchor.sql",
    "2026-05-03_kg_link_relation_enum.sql",
]


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
    host = urlparse(url).hostname or ""
    return host.split(".", 1)[0]


def _is_create_type_already_exists(stderr: str) -> bool:
    return 'type "kg_link_relation" already exists' in stderr.lower()


def _wrap_transactional(sql: str) -> str:
    """Wrap a migration body in BEGIN/COMMIT for atomic apply."""
    return f"BEGIN;\n{sql}\nCOMMIT;\n"


def _is_idempotency_ok(stderr: str) -> bool:
    """Detect benign already-exists errors so re-runs succeed."""
    needles = ('already exists', 'duplicate object', 'duplicate column')
    low = stderr.lower()
    return any(n in low for n in needles)


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

    print(f"Applying {len(MIGRATIONS)} migrations to project {ref}\n")
    failures: list[str] = []
    for name in MIGRATIONS:
        path = MIGRATION_DIR / name
        sql = path.read_text(encoding="utf-8")
        print(f"--- {name} ({len(sql)} bytes) ---")
        try:
            resp = httpx.post(
                api,
                json={"query": _wrap_transactional(sql)},
                headers=headers,
                timeout=60.0,
            )
        except Exception as exc:
            print(f"  EXCEPTION: {exc}")
            failures.append(name)
            continue
        if resp.status_code in (200, 201):
            print(f"  OK ({resp.status_code})")
            try:
                body = resp.json()
                if body:
                    preview = str(body)[:200]
                    print(f"  body: {preview}")
            except Exception:
                pass
        else:
            text = resp.text or ""
            # Idempotency: re-running migrations may surface benign "already exists"
            # / "duplicate object|column" errors. Treat those as SKIP, not failure.
            if resp.status_code in (400, 409) and _is_idempotency_ok(text):
                print(f"  SKIP (already applied: {resp.status_code} idempotency-ok)")
            else:
                print(f"  FAIL ({resp.status_code}): {text[:400]}")
                failures.append(name)
        print()

    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("All migrations applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
