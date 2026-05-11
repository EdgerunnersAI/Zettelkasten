# LEGACY (broken after 2026-05-11): targets dropped v1 migration audit surface.
# One-shot iter-08 audit backfill — not in active production path.
# Revive: port to v2 _v2/ migration runner. Tracked for follow-up.
"""iter-08 one-shot: backfill _migrations_applied with the 3 iter-08 migrations.

Context: apply_iter08_migrations.py applied the SQL via the Supabase Management
API, which executes raw SQL but does NOT touch the _migrations_applied audit
table that ops/scripts/apply_migrations.py reads on every droplet deploy. The
next deploy then thinks the migrations are un-applied, tries to CREATE TYPE
again, hits "already exists", and rolls back.

Fix: insert audit rows with the documented bootstrap placeholder
``checksum='manual-prebackfill'`` (apply_migrations.py:77 _BOOTSTRAP_PLACEHOLDERS).
The next deploy then logs "[migration] skip <name> (already applied)" and
proceeds. After the first successful deploy run, the operator can flip the
checksum to the real SHA256 via ``--reconcile-checksum`` if desired.

Usage:
    python ops/scripts/backfill_iter08_migrations_audit.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

MIGRATIONS = (
    "2026-05-03_rag_kasten_chunk_counts.sql",
    "2026-05-03_rag_entity_anchor.sql",
    "2026-05-03_kg_link_relation_enum.sql",
)
PLACEHOLDER = "manual-prebackfill"  # apply_migrations.py:77
APPLIED_BY = "manual-mgmt-api-backfill"


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

    # Self-bootstrap the table (the deploy script's _ensure_table normally does
    # this; we do it too in case this is a brand-new project).
    bootstrap = """
    CREATE TABLE IF NOT EXISTS _migrations_applied (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        checksum TEXT NOT NULL,
        applied_by TEXT
    );
    """
    print("Ensuring _migrations_applied exists...")
    resp = httpx.post(api, json={"query": bootstrap}, headers=headers, timeout=60.0)
    print(f"  bootstrap: {resp.status_code} {resp.text[:120] if resp.status_code >= 400 else 'OK'}")

    # Upsert audit rows for each iter-08 migration. ON CONFLICT keeps any prior
    # row intact (don't clobber a real checksum that may have landed via a race).
    inserts = []
    for name in MIGRATIONS:
        inserts.append(
            f"INSERT INTO _migrations_applied (name, checksum, applied_by) "
            f"VALUES ('{name}', '{PLACEHOLDER}', '{APPLIED_BY}') "
            f"ON CONFLICT (name) DO NOTHING;"
        )
    sql = "\n".join(inserts)
    print(f"\nBackfilling {len(MIGRATIONS)} audit rows with placeholder='{PLACEHOLDER}'...")
    resp = httpx.post(api, json={"query": sql}, headers=headers, timeout=60.0)
    if resp.status_code in (200, 201):
        print(f"  OK ({resp.status_code})")
    else:
        print(f"  FAIL ({resp.status_code}): {resp.text[:400]}")
        return 1

    # Verify the rows landed.
    verify = (
        "SELECT name, checksum, applied_by FROM _migrations_applied "
        "WHERE name IN ('2026-05-03_rag_kasten_chunk_counts.sql', "
        "'2026-05-03_rag_entity_anchor.sql', "
        "'2026-05-03_kg_link_relation_enum.sql') "
        "ORDER BY name;"
    )
    print("\nVerifying audit rows...")
    resp = httpx.post(api, json={"query": verify}, headers=headers, timeout=60.0)
    if resp.status_code in (200, 201):
        print(f"  OK ({resp.status_code}): {resp.text[:600]}")
    else:
        print(f"  VERIFY FAIL ({resp.status_code}): {resp.text[:400]}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
