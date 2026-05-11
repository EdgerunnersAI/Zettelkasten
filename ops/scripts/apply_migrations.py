"""Apply pending Supabase migrations from supabase/website/kg_public/migrations.

D-1 (KAS-11): the iter-01 manual_review.md RCA showed that several committed
migrations (notably ``2026-04-26_fix_rag_bulk_add_to_sandbox.sql``) were never
applied to prod Supabase, leaving the website RAG pipeline broken (Kasten chat
returns "no Zettels in selected scope" even for fully-populated Kastens). This
script makes deploys auto-apply every pending migration in lexical order,
exactly once, atomically per file, with a SHA-256 audit trail.

Usage::

    SUPABASE_DB_URL=postgresql://... python ops/scripts/apply_migrations.py
    # or
    SUPABASE_URL=https://<ref>.supabase.co \\
    SUPABASE_SERVICE_ROLE_KEY=... \\
        python ops/scripts/apply_migrations.py [--dry-run]
        [--migrations-dir DIR] [--rollback NAME]

Behaviour summary
-----------------
* Connects to Postgres via ``SUPABASE_DB_URL`` if set, else assembles the DSN
  from ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` (Supabase pooler /
  ``db.<ref>.supabase.co:5432`` with ``user=postgres``).
* Acquires a session-level Postgres advisory lock so two simultaneous deploys
  cannot race on the same migration.
* Self-bootstraps ``_migrations_applied`` (the table is also committed as a
  regular migration so a fresh DB rebuilt from ``schema.sql`` matches).
* For each ``.sql`` file in lexical order: skip if checksum matches an already-
  applied row; HARD FAIL on checksum mismatch (someone edited an applied
  migration); otherwise run the SQL inside a transaction and INSERT the audit
  row, rolling back on any error.
* ``--dry-run`` parses + lists the plan without writing.
* ``--rollback NAME`` runs ``<name>.down.sql`` (must exist) inside a
  transaction and DELETEs the audit row.

Exit codes
----------
``0`` success, ``1`` migration error / checksum mismatch / SQL failure,
``2`` configuration error (missing env vars, bad args).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - optional in test env
    from dotenv import load_dotenv

    load_dotenv(ROOT / "supabase" / ".env")
    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

DEFAULT_MIGRATIONS_DIR = (
    ROOT / "supabase" / "website" / "kg_public" / "migrations"
)
DEFAULT_V2_MIGRATIONS_DIR = ROOT / "supabase" / "website" / "_v2"
# Phase 8.0 Rev+: Flyway-style repeatable migrations live under
# ``_v2/repeatable/`` and are named ``R__<slug>.sql``. They re-run on every
# deploy whenever their checksum changes, after all versioned migrations.
_V2_REPEATABLE_SUBDIR = "repeatable"

# Bootstrap placeholders that an operator may have inserted into
# ``_migrations_applied.checksum`` to mark a migration as "already
# applied out-of-band; just record it on next run". On match we treat
# the migration as applied and silently overwrite nothing — the row's
# checksum is rewritten only via ``--reconcile-checksum``.
_BOOTSTRAP_PLACEHOLDERS: tuple[str, ...] = ("manual-prebackfill",)

# Stable lock key derived from the literal string 'apply_migrations' so two
# concurrent invocations serialize on Postgres rather than racing.
LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"apply_migrations").digest()[:8],
    "big",
    signed=True,
)

logger = logging.getLogger("apply_migrations")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# ---------------------------------------------------------------------------
# DSN assembly
# ---------------------------------------------------------------------------
def _build_dsn(*, v2: bool = False) -> str:
    """Return the Postgres DSN from an explicit database URL env var.

    Auto-deriving from ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` is
    NOT supported: the service-role JWT is not the postgres direct-connect
    password. The DB password is set per project in Supabase Studio and
    must be supplied via ``SUPABASE_DB_URL`` (preferably the IPv4 pooler
    endpoint, since ``db.<ref>.supabase.co`` is IPv6-only and may not
    resolve from IPv4-only droplet networks).

    Format (IPv4 pooler):
        postgresql://postgres.<ref>:<DB_PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres
    """
    env_name = "SUPABASE_V2_DATABASE_URL" if v2 else "SUPABASE_DB_URL"
    direct = os.environ.get(env_name)
    if direct:
        return direct

    raise RuntimeError(
        f"{env_name} must be set. Get the connection string from Supabase "
        "Studio > Project Settings > Database > Connection string and "
        f"register it as a secret named {env_name}. "
        "Note: SUPABASE_SERVICE_ROLE_KEY is NOT the postgres password."
    )


def _redact_dsn(dsn: str) -> str:
    """Return DSN with password masked, for safe logging."""
    try:
        p = urlparse(dsn)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            return dsn.replace(p.netloc, netloc, 1)
    except Exception:
        pass
    return "<redacted>"


# ---------------------------------------------------------------------------
# Migration discovery + checksum
# ---------------------------------------------------------------------------
def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_MIGRATION_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(_\d{2})?_[a-z0-9_]+\.sql$")
_V2_MIGRATION_NAME_RE = re.compile(r"^\d{2}_[a-z0-9_]+\.sql$")
_V2_REPEATABLE_NAME_RE = re.compile(r"^R__[a-z0-9_]+\.sql$")


def _list_migrations(directory: Path, *, v2: bool = False) -> list[Path]:
    if not directory.is_dir():
        raise RuntimeError(f"Migrations directory not found: {directory}")
    # Phase 8.0 Rev+: top-level *.sql only — repeatable/ subdir is handled
    # separately by _list_repeatable_migrations().
    files = sorted(p for p in directory.glob("*.sql") if not p.name.endswith(".down.sql"))
    name_re = _V2_MIGRATION_NAME_RE if v2 else _MIGRATION_NAME_RE
    expected = "NN_slug.sql" if v2 else "YYYY-MM-DD[_NN]_<slug>.sql"
    invalid = [p.name for p in files if not name_re.match(p.name)]
    if invalid:
        raise RuntimeError(
            f"Invalid migration filenames: {invalid}. "
            f"Expected: {expected}"
        )
    return files


def _list_repeatable_migrations(directory: Path) -> list[Path]:
    """Return sorted R__*.sql files under <directory>/repeatable/.

    Phase 8.0 Rev+ (v2 only): repeatable migrations are tracked by name in
    core._migrations_applied just like versioned migrations, but their
    checksum is allowed to change. When the checksum on disk no longer
    matches the recorded row, the migration runs again and the row is
    updated. Used for idempotent CREATE OR REPLACE definitions whose
    bodies legitimately evolve over time (e.g., diagnostic RPCs).
    """
    repeatable_dir = directory / _V2_REPEATABLE_SUBDIR
    if not repeatable_dir.is_dir():
        return []
    files = sorted(
        p for p in repeatable_dir.glob("*.sql")
        if not p.name.endswith(".down.sql")
    )
    invalid = [p.name for p in files if not _V2_REPEATABLE_NAME_RE.match(p.name)]
    if invalid:
        raise RuntimeError(
            f"Invalid repeatable migration filenames: {invalid}. "
            f"Expected: R__<slug>.sql"
        )
    return files


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _migration_table(v2: bool) -> str:
    return "core._migrations_applied" if v2 else "_migrations_applied"


def _ensure_table(conn, *, v2: bool = False) -> None:
    """Self-bootstrap ``_migrations_applied`` so a fresh DB just works."""
    with conn.cursor() as cur:
        if v2:
            cur.execute("CREATE SCHEMA IF NOT EXISTS core")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_migration_table(v2)} (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checksum TEXT NOT NULL,
                applied_by TEXT
            )
            """
        )
    conn.commit()


def _applied_index(conn, *, v2: bool = False) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT name, checksum FROM {_migration_table(v2)}")
        return {row[0]: row[1] for row in cur.fetchall()}


def _acquire_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    conn.commit()


def _release_lock(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.commit()
    except Exception:  # pragma: no cover - best effort
        logger.exception("failed to release advisory lock")


# ---------------------------------------------------------------------------
# Apply / rollback
# ---------------------------------------------------------------------------
def _apply_one(conn, path: Path, sql: str, checksum: str, hostname: str, *, v2: bool = False) -> float:
    """Run one migration file inside a single transaction. Returns elapsed ms.

    iter-03 §1C.4: also records deploy provenance (git SHA, deploy id,
    actor, runner hostname) into the audit row so we can later trace which
    deploy applied each migration.
    """
    git_sha = os.environ.get("DEPLOY_GIT_SHA")
    deploy_id = os.environ.get("DEPLOY_ID")
    deploy_actor = os.environ.get("DEPLOY_ACTOR")
    t0 = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                f"INSERT INTO {_migration_table(v2)} "
                "(name, checksum, applied_by, deploy_git_sha, deploy_id, "
                "deploy_actor, runner_hostname) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    path.name,
                    checksum,
                    hostname,
                    git_sha,
                    deploy_id,
                    deploy_actor,
                    hostname,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return (time.perf_counter() - t0) * 1000.0


def _apply_repeatable(
    conn,
    path: Path,
    sql: str,
    checksum: str,
    hostname: str,
    prior_checksum: str | None,
) -> tuple[str, float]:
    """Apply a repeatable migration if its checksum changed.

    Phase 8.0 Rev+ (v2 only). Returns (action, elapsed_ms) where action is
    one of: ``applied``, ``updated``, ``skipped``. Keyed by filename in
    core._migrations_applied; on checksum change we UPDATE the existing row
    in the same transaction as the SQL re-application.
    """
    if prior_checksum == checksum:
        return ("skipped", 0.0)

    git_sha = os.environ.get("DEPLOY_GIT_SHA")
    deploy_id = os.environ.get("DEPLOY_ID")
    deploy_actor = os.environ.get("DEPLOY_ACTOR")
    t0 = time.perf_counter()
    table = _migration_table(v2=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if prior_checksum is None:
                cur.execute(
                    f"INSERT INTO {table} "
                    "(name, checksum, applied_by, deploy_git_sha, deploy_id, "
                    "deploy_actor, runner_hostname) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        path.name,
                        checksum,
                        hostname,
                        git_sha,
                        deploy_id,
                        deploy_actor,
                        hostname,
                    ),
                )
                action = "applied"
            else:
                cur.execute(
                    f"UPDATE {table} SET checksum = %s, applied_at = now(), "
                    "applied_by = %s, deploy_git_sha = %s, deploy_id = %s, "
                    "deploy_actor = %s, runner_hostname = %s "
                    "WHERE name = %s",
                    (
                        checksum,
                        hostname,
                        git_sha,
                        deploy_id,
                        deploy_actor,
                        hostname,
                        path.name,
                    ),
                )
                action = "updated"
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return (action, (time.perf_counter() - t0) * 1000.0)


def _run_rollback(conn, directory: Path, name: str, hostname: str, *, v2: bool = False) -> int:
    down = directory / f"{name}.down.sql"
    if not down.exists():
        logger.error(
            "rollback failed: companion file not found: %s. Manual rollback "
            "required — write a <name>.down.sql alongside the original.",
            down,
        )
        return 1
    sql = down.read_text(encoding="utf-8")
    logger.warning(
        "[migration] ROLLBACK %s — running %s and removing audit row "
        "(operator=%s)",
        name,
        down.name,
        hostname,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(f"DELETE FROM {_migration_table(v2)} WHERE name = %s", (name,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("rollback failed: %s", exc)
        return 1
    logger.info("[migration] rolled back %s", name)
    return 0


# ---------------------------------------------------------------------------
# Schema-drift detection (iter-03 §1C.5)
# ---------------------------------------------------------------------------
DEFAULT_MANIFEST_PATH = (
    ROOT / "supabase" / "website" / "kg_public" / "expected_schema.json"
)


def _introspect_schema(conn) -> dict:
    """Build a normalized snapshot of the live ``public`` schema.

    Captures, per table, every column's data_type, nullability, and default
    expression — the four kinds of drift the iter-03 spec calls out
    (added, removed, type change, nullability change, default change).
    Functions are captured by their fully-qualified signature; indexes by
    their definition string for completeness, though only tables and
    functions feed the gate by default.
    """
    snap: dict = {"tables": {}, "functions": {}, "indexes": {}}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_schema = 'public'
             ORDER BY table_name, ordinal_position
            """
        )
        for tbl, col, dtype, is_nullable, default in cur.fetchall():
            t = snap["tables"].setdefault(tbl, {"columns": {}})
            t["columns"][col] = {
                "data_type": dtype,
                "is_nullable": is_nullable,
                "default": default,
            }

        cur.execute(
            """
            SELECT indexname, tablename, indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
             ORDER BY indexname
            """
        )
        for name, tbl, ddef in cur.fetchall():
            snap["indexes"][name] = {"table": tbl, "definition": ddef}

        cur.execute(
            """
            SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS sig,
                   pg_get_function_result(p.oid) AS rettype
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public'
             ORDER BY sig
            """
        )
        for sig, rettype in cur.fetchall():
            snap["functions"][sig] = {"return_type": rettype}

    return snap


def _diff_schemas(expected: dict, live: dict) -> list[str]:
    """Return a list of human-readable drift descriptions; empty == match."""
    drift: list[str] = []
    expected_tables = expected.get("tables", {})
    live_tables = live.get("tables", {})

    for tbl, spec in expected_tables.items():
        if tbl not in live_tables:
            drift.append(f"missing table: {tbl}")
            continue
        live_cols = live_tables[tbl].get("columns", {})
        expected_cols = spec.get("columns", {})
        for col, expected_col in expected_cols.items():
            live_col = live_cols.get(col)
            if live_col is None:
                drift.append(f"missing column: {tbl}.{col}")
                continue
            # Per-attribute comparison so we can name the drift kind.
            for attr in ("data_type", "is_nullable", "default"):
                exp_v = expected_col.get(attr) if isinstance(expected_col, dict) else None
                live_v = live_col.get(attr) if isinstance(live_col, dict) else None
                # Back-compat: legacy manifest may have stored bare type str.
                if isinstance(expected_col, str) and attr == "data_type":
                    exp_v = expected_col
                if exp_v is None and attr in ("is_nullable", "default"):
                    # Manifest didn't pin this attribute — skip.
                    continue
                if exp_v != live_v:
                    drift.append(
                        f"{attr} mismatch: {tbl}.{col} expected={exp_v!r} live={live_v!r}"
                    )
        # Removed-column detection: column present live, absent in manifest.
        for col in live_cols:
            if col not in expected_cols:
                drift.append(f"unexpected column (manifest stale?): {tbl}.{col}")

    for fn in expected.get("functions", {}):
        if fn not in live.get("functions", {}):
            drift.append(f"missing function: {fn}")

    return drift


def _verify_schema(conn, manifest_path: Path) -> int:
    """Return 0 if live schema matches manifest, 1 if drift detected."""
    if not manifest_path.exists():
        logger.error("[migration] expected_schema.json missing: %s", manifest_path)
        return 1
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    live = _introspect_schema(conn)
    drift = _diff_schemas(expected, live)
    if drift:
        logger.error("[migration] SCHEMA DRIFT detected:")
        for d in drift:
            logger.error("  - %s", d)
        return 1
    logger.info("[migration] schema matches expected_schema.json")
    return 0


def _write_manifest(conn, manifest_path: Path) -> int:
    """Write the live schema to ``manifest_path`` (bootstrap or update)."""
    snap = _introspect_schema(conn)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(snap, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.warning(
        "[migration] wrote schema manifest -> %s (%d tables, %d functions)",
        manifest_path,
        len(snap["tables"]),
        len(snap["functions"]),
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--v2",
        action="store_true",
        help="Apply DB v2 migrations from supabase/website/_v2 using SUPABASE_V2_DATABASE_URL.",
    )
    p.add_argument(
        "--target",
        default=None,
        help="Human-readable target label for logs only (for example v2-dev).",
    )
    p.add_argument("--dry-run", action="store_true", help="Plan only; never write.")
    p.add_argument(
        "--migrations-dir",
        default=None,
        help=(
            f"Directory of *.sql migrations (default: {DEFAULT_MIGRATIONS_DIR}; "
            f"with --v2: {DEFAULT_V2_MIGRATIONS_DIR})."
        ),
    )
    p.add_argument(
        "--rollback",
        default=None,
        help="Name of an applied migration to roll back (requires <name>.down.sql).",
    )
    p.add_argument(
        "--reconcile-checksum",
        metavar="NAME",
        default=None,
        help=(
            "Rewrite the recorded checksum for an already-applied migration "
            "to the current file's SHA-256 (operator review required)."
        ),
    )
    p.add_argument(
        "--manifest-path",
        default=None,
        help=f"Path to expected_schema.json (default: {DEFAULT_MANIFEST_PATH}).",
    )
    p.add_argument(
        "--bootstrap-manifest",
        action="store_true",
        help="Write the live schema to the manifest path and exit.",
    )
    p.add_argument(
        "--update-manifest",
        action="store_true",
        help=(
            "Apply pending migrations, then overwrite the manifest with the "
            "post-apply schema (use after a deliberate schema change)."
        ),
    )
    p.add_argument(
        "--check-manifest-fresh",
        action="store_true",
        help=(
            "Compare the live schema to the manifest and exit; do NOT apply "
            "any migrations. Used by the CI freshness gate."
        ),
    )
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.migrations_dir is None:
        args.migrations_dir = str(DEFAULT_V2_MIGRATIONS_DIR if args.v2 else DEFAULT_MIGRATIONS_DIR)
    if args.manifest_path is None:
        args.manifest_path = str(
            ROOT / "supabase" / "website" / "_v2" / "expected_schema.json"
            if args.v2
            else DEFAULT_MANIFEST_PATH
        )

    try:
        dsn = _build_dsn(v2=args.v2)
    except RuntimeError as exc:
        logger.error(str(exc))
        return 2

    try:
        import psycopg  # type: ignore
    except ImportError:
        logger.error(
            "psycopg (v3) is required: pip install 'psycopg[binary]'"
        )
        return 2

    directory = Path(args.migrations_dir).resolve()
    hostname = socket.gethostname()

    logger.info("[migration] connecting to %s", _redact_dsn(dsn))
    conn = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            conn = psycopg.connect(dsn, autocommit=False, connect_timeout=15)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "[migration] connect attempt %d/3 failed: %s",
                attempt + 1,
                exc,
            )
            if attempt < 2:
                time.sleep(5)
    if conn is None:
        logger.error(
            "could not connect to Postgres at %s after 3 attempts: %s. "
            "If the host did not resolve, the assembled DSN is IPv6-only — "
            "set SUPABASE_DB_URL to the IPv4 pooler endpoint from "
            "Supabase Studio > Project Settings > Database > Connection string.",
            _redact_dsn(dsn),
            last_exc,
        )
        return 2

    applied_count = 0
    skipped_count = 0
    total_count = 0
    rc = 0
    manifest_path = Path(args.manifest_path).resolve()
    try:
        _acquire_lock(conn)
        _ensure_table(conn, v2=args.v2)

        if args.bootstrap_manifest:
            return _write_manifest(conn, manifest_path)

        if args.check_manifest_fresh:
            return _verify_schema(conn, manifest_path)

        if args.reconcile_checksum:
            name = args.reconcile_checksum
            sql_path = directory / name
            if not sql_path.exists():
                logger.error("[migration] reconcile failed: file not found: %s", sql_path)
                return 1
            new_checksum = _checksum(sql_path.read_text(encoding="utf-8"))
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {_migration_table(args.v2)} SET checksum = %s WHERE name = %s",
                    (new_checksum, name),
                )
            conn.commit()
            logger.warning(
                "[migration] reconciled checksum for %s -> %s",
                name,
                new_checksum[:12],
            )
            return 0

        if args.rollback:
            return _run_rollback(conn, directory, args.rollback, hostname, v2=args.v2)

        applied = _applied_index(conn, v2=args.v2)
        migrations = _list_migrations(directory, v2=args.v2)
        total_count = len(migrations)

        for path in migrations:
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            prior = applied.get(path.name)
            if prior is not None:
                # Allow the bootstrap placeholder so operators can flip to a
                # real checksum on first successful re-application.
                if prior == checksum or prior in _BOOTSTRAP_PLACEHOLDERS:
                    logger.info("[migration] skip %s (already applied)", path.name)
                    skipped_count += 1
                    continue
                logger.error(
                    "[migration] CHECKSUM MISMATCH for %s — applied=%s "
                    "current=%s. Refusing to run. An already-applied migration "
                    "was edited; investigate and reconcile manually.",
                    path.name,
                    prior,
                    checksum,
                )
                rc = 1
                break

            if args.dry_run:
                logger.info("[migration] PLAN apply %s (sha256=%s)", path.name, checksum[:12])
                applied_count += 1
                continue

            try:
                elapsed = _apply_one(conn, path, sql, checksum, hostname, v2=args.v2)
            except Exception as exc:
                logger.error(
                    "[migration] FAILED %s — rolled back. Error: %s",
                    path.name,
                    exc,
                )
                rc = 1
                break
            logger.info(
                "[migration] applied %s in %.0fms", path.name, elapsed
            )
            applied_count += 1

        # Phase 8.0 Rev+: repeatable migrations run AFTER versioned ones so any
        # versioned file (e.g., 41_migrate_39_to_repeatable.sql) that cleans up
        # a prior manifest row lands before the R__ runner re-inserts it under
        # the new name. v2-only — the legacy migrations layout has none.
        if rc == 0 and args.v2 and not args.dry_run:
            repeatable_files = _list_repeatable_migrations(directory)
            for path in repeatable_files:
                sql = path.read_text(encoding="utf-8")
                checksum = _checksum(sql)
                prior = applied.get(path.name)
                try:
                    action, elapsed = _apply_repeatable(
                        conn, path, sql, checksum, hostname, prior
                    )
                except Exception as exc:
                    logger.error(
                        "[migration] FAILED repeatable %s — rolled back. Error: %s",
                        path.name,
                        exc,
                    )
                    rc = 1
                    break
                if action == "skipped":
                    logger.info("[migration] skip %s (repeatable, checksum match)", path.name)
                    skipped_count += 1
                else:
                    logger.info(
                        "[migration] %s %s (repeatable) in %.0fms",
                        action,
                        path.name,
                        elapsed,
                    )
                    applied_count += 1
                total_count += 1

        # iter-03 §1C.5 (hardened): post-apply manifest gate.
        #
        # Default behavior is HARD-FAIL on drift or missing manifest. Two
        # operator escapes:
        #   * ``--update-manifest`` rewrites the manifest from the live schema
        #     (deliberate schema change — committed back to Git).
        #   * ``MIGRATION_MANIFEST_AUTOBOOTSTRAP=1`` writes the manifest if
        #     absent, returns 0, and logs a loud reminder to commit it. Used
        #     for the first deploy after iter-03; not for steady state.
        #
        # The legacy ``MIGRATION_MANIFEST_REQUIRED=0`` env reverts to warn-only
        # behavior — kept for emergency rollback only.
        if rc == 0 and not args.dry_run:
            required = os.environ.get("MIGRATION_MANIFEST_REQUIRED", "1") == "1"
            autobootstrap = os.environ.get("MIGRATION_MANIFEST_AUTOBOOTSTRAP", "0") == "1"

            if args.update_manifest:
                _write_manifest(conn, manifest_path)
            elif manifest_path.exists():
                drift_rc = _verify_schema(conn, manifest_path)
                if drift_rc != 0:
                    rc = 1
            elif autobootstrap:
                logger.warning(
                    "[migration] manifest missing — AUTOBOOTSTRAP writing %s. "
                    "OPERATOR MUST COMMIT THIS FILE TO GIT before the next deploy.",
                    manifest_path,
                )
                _write_manifest(conn, manifest_path)
            elif required:
                logger.error(
                    "[migration] FATAL: schema-drift manifest not found at %s. "
                    "Either set MIGRATION_MANIFEST_AUTOBOOTSTRAP=1 for the first "
                    "deploy, or run --bootstrap-manifest against staging and commit.",
                    manifest_path,
                )
                rc = 1
            else:
                logger.warning(
                    "[migration] schema-drift gate skipped (MIGRATION_MANIFEST_REQUIRED=0).",
                )
    finally:
        _release_lock(conn)
        conn.close()

    logger.info(
        "[migration] summary applied=%d skipped=%d total=%d%s",
        applied_count,
        skipped_count,
        total_count,
        " (dry-run)" if args.dry_run else "",
    )
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
