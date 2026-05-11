"""Unit tests for ops/scripts/apply_migrations.py.

The Postgres driver (psycopg v3) is fully mocked — no DB connection is made.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Fake psycopg
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.conn.executed.append((sql.strip(), params))
        sql_low = sql.strip().lower()
        if self.conn.fail_on and self.conn.fail_on in sql_low:
            raise RuntimeError(f"forced failure on: {self.conn.fail_on}")
        if sql_low.startswith("select name, checksum from _migrations_applied"):
            self._rows = list(self.conn.applied_rows)
        elif sql_low.startswith("insert into _migrations_applied"):
            assert params is not None
            # Older 3-tuple form supported for back-compat in tests, but the
            # canonical iter-03 §1C.4 form is 7-tuple with audit columns.
            name = params[0]
            checksum = params[1]
            self.conn.applied_rows.append((name, checksum))
        elif sql_low.startswith("update _migrations_applied"):
            assert params is not None
            new_checksum, name = params
            self.conn.applied_rows = [
                (r[0], new_checksum) if r[0] == name else r
                for r in self.conn.applied_rows
            ]
        elif sql_low.startswith("delete from _migrations_applied"):
            assert params is not None
            (name,) = params
            self.conn.applied_rows = [
                r for r in self.conn.applied_rows if r[0] != name
            ]
        elif sql_low.startswith("select pg_advisory_lock"):
            self.conn.lock_acquired = True
        elif sql_low.startswith("select pg_advisory_unlock"):
            self.conn.lock_released = True

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []
        self.applied_rows: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.lock_acquired = False
        self.lock_released = False
        self.fail_on: str | None = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_psycopg(monkeypatch):
    fake_conn = FakeConn()
    fake_module = ModuleType("psycopg")

    def _connect(dsn: str, autocommit: bool = False, **kwargs) -> FakeConn:
        fake_conn.dsn = dsn  # type: ignore[attr-defined]
        return fake_conn

    fake_module.connect = _connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)
    return fake_conn


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    # iter-03 §1C.5: redirect the schema-drift manifest path to a tmp
    # location and disable the gate for the migration-application tests
    # (the dedicated drift-detection tests in test_schema_drift.py exercise
    # the gate directly).
    am = _load()
    monkeypatch.setattr(am, "DEFAULT_MANIFEST_PATH", tmp_path / "no-manifest.json")
    monkeypatch.setenv("MIGRATION_MANIFEST_REQUIRED", "0")
    monkeypatch.setenv("MIGRATION_MANIFEST_AUTOBOOTSTRAP", "0")


@pytest.fixture
def mig_dir(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "2026-01-01_first.sql").write_text("CREATE TABLE t1 ();\n", encoding="utf-8")
    (d / "2026-01-02_second.sql").write_text("CREATE TABLE t2 ();\n", encoding="utf-8")
    return d


# Lazily import the script so the test-time monkeypatching of psycopg works.
def _load():
    from ops.scripts import apply_migrations as am  # noqa: WPS433

    return am


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_detects_pending_migrations_and_applies_in_order(fake_psycopg, env, mig_dir):
    am = _load()
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    names = [r[0] for r in fake_psycopg.applied_rows]
    assert names == ["2026-01-01_first.sql", "2026-01-02_second.sql"]
    assert fake_psycopg.lock_acquired and fake_psycopg.lock_released


def test_skip_already_applied_matching_checksum(fake_psycopg, env, mig_dir):
    am = _load()
    sql = (mig_dir / "2026-01-01_first.sql").read_text(encoding="utf-8")
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", am._checksum(sql)))
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    # Only the pending one should have been added; first remains untouched.
    names = [r[0] for r in fake_psycopg.applied_rows]
    assert names.count("2026-01-01_first.sql") == 1
    assert "2026-01-02_second.sql" in names


def test_checksum_mismatch_is_hard_fail(fake_psycopg, env, mig_dir):
    am = _load()
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", "deadbeef-not-matching"))
    rc = am.main(["--migrations-dir", str(mig_dir)])
    # Rev++ (Phase 8.0): drift uses a dedicated exit code distinct from the
    # generic SQL-failure path so deploy.sh and humans can branch on cause.
    assert rc == am.EXIT_DRIFT_DETECTED
    assert rc != 1
    # Second migration must NOT have been applied after the failure.
    names = [r[0] for r in fake_psycopg.applied_rows]
    assert "2026-01-02_second.sql" not in names


def test_writes_audit_row_on_success(fake_psycopg, env, mig_dir):
    am = _load()
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    inserts = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("insert into _migrations_applied")
    ]
    assert len(inserts) == 2
    # iter-03 §1C.4: insert tuple is now
    # (name, checksum, applied_by, deploy_git_sha, deploy_id, deploy_actor, runner_hostname)
    for _sql, params in inserts:
        assert params is not None and len(params) == 7


def test_rolls_back_on_sql_error(fake_psycopg, env, mig_dir):
    am = _load()
    fake_psycopg.fail_on = "create table t2"
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 1
    # First succeeded; second triggered rollback.
    assert ("2026-01-01_first.sql", am._checksum("CREATE TABLE t1 ();\n")) in fake_psycopg.applied_rows
    assert all(r[0] != "2026-01-02_second.sql" for r in fake_psycopg.applied_rows)
    assert fake_psycopg.rollbacks >= 1


def test_dry_run_does_not_write(fake_psycopg, env, mig_dir):
    am = _load()
    rc = am.main(["--dry-run", "--migrations-dir", str(mig_dir)])
    assert rc == 0
    assert fake_psycopg.applied_rows == []
    inserts = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("insert into _migrations_applied")
    ]
    assert inserts == []


def test_advisory_lock_acquired_and_released(fake_psycopg, env, mig_dir):
    am = _load()
    am.main(["--migrations-dir", str(mig_dir)])
    assert fake_psycopg.lock_acquired
    assert fake_psycopg.lock_released


def test_rollback_requires_down_companion(fake_psycopg, env, mig_dir):
    am = _load()
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", "anything"))
    rc = am.main([
        "--migrations-dir", str(mig_dir),
        "--rollback", "2026-01-01_first.sql",
    ])
    assert rc == 1  # no .down.sql file -> hard fail


def test_rollback_runs_down_companion(fake_psycopg, env, mig_dir):
    am = _load()
    sql = (mig_dir / "2026-01-01_first.sql").read_text(encoding="utf-8")
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", am._checksum(sql)))
    (mig_dir / "2026-01-01_first.sql.down.sql").write_text(
        "DROP TABLE t1;\n", encoding="utf-8"
    )
    rc = am.main([
        "--migrations-dir", str(mig_dir),
        "--rollback", "2026-01-01_first.sql",
    ])
    assert rc == 0
    assert all(r[0] != "2026-01-01_first.sql" for r in fake_psycopg.applied_rows)


def test_missing_env_returns_config_error(monkeypatch, fake_psycopg):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    am = _load()
    rc = am.main([])
    assert rc == 2


def test_build_dsn_requires_supabase_db_url(monkeypatch, fake_psycopg):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://abcxyz.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret/with+chars")
    am = _load()
    with pytest.raises(RuntimeError, match="SUPABASE_DB_URL must be set"):
        am._build_dsn()


def test_build_dsn_returns_supabase_db_url_when_set(monkeypatch, fake_psycopg):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@host:5432/db")
    am = _load()
    assert am._build_dsn() == "postgresql://u:p@host:5432/db"


def test_bootstrap_placeholders_constant():
    am = _load()
    assert "manual-prebackfill" in am._BOOTSTRAP_PLACEHOLDERS


def test_bootstrap_placeholder_skips_apply(fake_psycopg, env, mig_dir):
    am = _load()
    # Pre-mark first migration with the placeholder — it must be skipped
    # without an INSERT and without a checksum mismatch.
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", "manual-prebackfill"))
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    # Second migration must have been applied normally; first untouched.
    inserts = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("insert into _migrations_applied")
    ]
    inserted_names = [params[0] for _sql, params in inserts]
    assert "2026-01-01_first.sql" not in inserted_names
    assert "2026-01-02_second.sql" in inserted_names


def test_reconcile_checksum_rewrites_existing_row(fake_psycopg, env, mig_dir):
    am = _load()
    fake_psycopg.applied_rows.append(("2026-01-01_first.sql", "stale-deadbeef"))
    rc = am.main([
        "--migrations-dir", str(mig_dir),
        "--reconcile-checksum", "2026-01-01_first.sql",
    ])
    assert rc == 0
    updates = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("update _migrations_applied")
    ]
    assert len(updates) == 1
    _sql, params = updates[0]
    expected_checksum = am._checksum(
        (mig_dir / "2026-01-01_first.sql").read_text(encoding="utf-8")
    )
    assert params == (expected_checksum, "2026-01-01_first.sql")


def test_audit_trail_columns_populated_from_env(monkeypatch, fake_psycopg, env, mig_dir):
    monkeypatch.setenv("DEPLOY_GIT_SHA", "abc123")
    monkeypatch.setenv("DEPLOY_ID", "run-7-1")
    monkeypatch.setenv("DEPLOY_ACTOR", "chintanmehta21")
    am = _load()
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    inserts = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("insert into _migrations_applied")
    ]
    assert inserts, "no audit insert observed"
    for _sql, params in inserts:
        name, checksum, applied_by, git_sha, deploy_id, actor, runner = params
        assert git_sha == "abc123"
        assert deploy_id == "run-7-1"
        assert actor == "chintanmehta21"
        assert runner == applied_by  # hostname mirrored


def test_audit_trail_columns_null_when_env_missing(
    monkeypatch, fake_psycopg, env, mig_dir
):
    monkeypatch.delenv("DEPLOY_GIT_SHA", raising=False)
    monkeypatch.delenv("DEPLOY_ID", raising=False)
    monkeypatch.delenv("DEPLOY_ACTOR", raising=False)
    am = _load()
    am.main(["--migrations-dir", str(mig_dir)])
    inserts = [
        ex for ex in fake_psycopg.executed
        if ex[0].lower().startswith("insert into _migrations_applied")
    ]
    for _sql, params in inserts:
        _name, _checksum, _ab, git_sha, deploy_id, actor, _runner = params
        assert git_sha is None and deploy_id is None and actor is None


def test_invalid_filename_rejected(tmp_path):
    am = _load()
    (tmp_path / "BAD.sql").write_text("--", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Invalid migration filenames"):
        am._list_migrations(tmp_path)


def test_valid_filenames_accepted(tmp_path):
    am = _load()
    (tmp_path / "2026-04-28_x.sql").write_text("--", encoding="utf-8")
    (tmp_path / "2026-04-28_01_y.sql").write_text("--", encoding="utf-8")
    files = am._list_migrations(tmp_path)
    assert [p.name for p in files] == ["2026-04-28_01_y.sql", "2026-04-28_x.sql"]


def test_connect_retries_three_times_then_returns_two(monkeypatch, env, mig_dir):
    am = _load()
    fake_module = ModuleType("psycopg")
    attempts: list[int] = []

    def _connect(dsn, autocommit=False, **kwargs):
        attempts.append(1)
        raise RuntimeError("connection refused")

    fake_module.connect = _connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)
    monkeypatch.setattr(am.time, "sleep", lambda *_a, **_kw: None)
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 2
    assert len(attempts) == 3


def test_connect_succeeds_after_two_failures(monkeypatch, env, mig_dir):
    am = _load()
    fake_module = ModuleType("psycopg")
    fake_conn = FakeConn()
    state = {"calls": 0}

    def _connect(dsn, autocommit=False, **kwargs):
        state["calls"] += 1
        if state["calls"] < 3:
            raise RuntimeError("transient")
        return fake_conn

    fake_module.connect = _connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)
    monkeypatch.setattr(am.time, "sleep", lambda *_a, **_kw: None)
    rc = am.main(["--migrations-dir", str(mig_dir)])
    assert rc == 0
    assert state["calls"] == 3


def test_reconcile_checksum_missing_file_returns_error(fake_psycopg, env, mig_dir):
    am = _load()
    rc = am.main([
        "--migrations-dir", str(mig_dir),
        "--reconcile-checksum", "999_does_not_exist.sql",
    ])
    assert rc == 1
