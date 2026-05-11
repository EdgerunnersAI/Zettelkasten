"""Live integration test — proves ops/scripts/reconcile_kg_users.py runs
green against the production v2 schema (Phase 2D.2 protected knob).

The script was refactored in commit fd41fd8 to read from core.profiles +
content.workspace_zettels JOIN core.workspaces (post-Phase-8 v2 surface).
Existing unit tests (tests/unit/ops/test_reconcile_kg_users.py, 9/9
passing) assert on SQL-string equality with mocked psycopg cursors —
which catches typos in the SQL strings BUT cannot prove the SQL actually
parses + executes against the live v2 schema.

This test invokes the script via subprocess with each of its three CLI
verbs in read-only / dry-run mode:
  - --audit (read-only — runs 2 SELECTs)
  - --dedupe-naruto (no --apply → only the SELECT for duplicates runs)
  - --purge-orphans (no --apply → only the two COUNT(*) JOINs run)

If any v2 column / schema reference is wrong, psycopg raises and the
script exits non-zero. rc=0 across all three invocations = the v2 SQL is
both syntactically valid AND references columns the live DB actually has.

Read-only by construction (no --apply): safe to run against production
data per the standing rule that --live tests must not mutate state
outside their own minted fixture users.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from website.core.supabase_v2.client import get_v2_database_url

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ops" / "scripts" / "reconcile_kg_users.py"


def _env_with_dsn() -> dict[str, str]:
    """os.environ copy with SUPABASE_DB_URL set to the v2 DSN.

    The script reads SUPABASE_DB_URL (L148). In the v2-only world this points
    at the same DSN as SUPABASE_V2_DATABASE_URL. Canonicalising here makes
    the test independent of which env var name the operator has exported.
    """
    env = os.environ.copy()
    env["SUPABASE_DB_URL"] = get_v2_database_url(listen=False)
    return env


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_env_with_dsn(),
        timeout=60,
    )


def _fail_msg(result: subprocess.CompletedProcess[str]) -> str:
    return (
        f"rc={result.returncode}\n"
        f"--- stdout (first 800c) ---\n{result.stdout[:800]}\n"
        f"--- stderr (first 800c) ---\n{result.stderr[:800]}"
    )


def test_reconcile_audit_exits_zero_and_returns_valid_report():
    """--audit must parse v2 schema, exit rc=0, and emit a JSON report.

    Validates the two SELECTs in audit() (reconcile_kg_users.py:48-55):
      - SELECT id::text, email FROM core.profiles
      - SELECT DISTINCT w.owner_profile_id::text
        FROM content.workspace_zettels wz
        JOIN core.workspaces w ON w.id = wz.workspace_id
    """
    result = _run(["--audit"])
    assert result.returncode == 0, _fail_msg(result)

    # The script prints the audit dict as indented JSON to stdout
    # (logger lines go to stderr via basicConfig default).
    report = json.loads(result.stdout)

    # Shape invariants — match the audit() return contract.
    assert set(report.keys()) >= {"users", "duplicate_naruto", "orphan_owners"}
    assert isinstance(report["users"], list)
    assert isinstance(report["duplicate_naruto"], list)
    assert isinstance(report["orphan_owners"], list)

    # Every user entry is [id_str, email_or_null] — list of 2.
    for u in report["users"]:
        assert isinstance(u, list) and len(u) == 2, u


def test_reconcile_dedupe_naruto_dry_run_exits_zero():
    """--dedupe-naruto without --apply must execute the duplicate-finder SELECT
    against core.profiles (LOWER(email) LIKE 'naruto%') and exit rc=0.

    No mutations occur (dry_run=True is the default). rc=0 proves the v2
    SQL parses and executes against the live core.profiles schema.
    """
    result = _run(["--dedupe-naruto"])
    assert result.returncode == 0, _fail_msg(result)


def test_reconcile_purge_orphans_dry_run_exits_zero():
    """--purge-orphans without --apply must execute the two COUNT queries:

      - COUNT(*) FROM content.workspace_zettels wz
        JOIN core.workspaces w ON w.id = wz.workspace_id
        WHERE w.owner_profile_id::text NOT IN <allowlist>
      - COUNT(*) FROM core.workspaces WHERE owner_profile_id::text NOT IN <allowlist>

    rc=0 confirms both cross-schema queries parse against the live v2
    surface. No mutations (dry_run=True default).
    """
    result = _run(["--purge-orphans"])
    assert result.returncode == 0, _fail_msg(result)


def test_reconcile_requires_at_least_one_verb():
    """Sanity check — argparse rejects empty invocation with rc=2.

    This is not a v2-SQL check but it pins the CLI contract so a future
    refactor that drops the verb requirement gets caught.
    """
    result = _run([])
    assert result.returncode == 2, _fail_msg(result)
    assert "at least one of" in result.stderr
