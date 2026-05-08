from __future__ import annotations

from pathlib import Path

import pytest

from ops.scripts import apply_migrations as am


def test_v2_build_dsn_uses_v2_database_url(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_V2_DATABASE_URL", "postgresql://u:p@host:5432/postgres")
    assert am._build_dsn(v2=True) == "postgresql://u:p@host:5432/postgres"


def test_v2_build_dsn_does_not_fall_back_to_prod(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_V2_DATABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://prod")
    with pytest.raises(RuntimeError, match="SUPABASE_V2_DATABASE_URL"):
        am._build_dsn(v2=True)


def test_v2_migration_names_use_numbered_prefix(tmp_path: Path) -> None:
    (tmp_path / "00_extensions.sql").write_text("select 1;", encoding="utf-8")
    assert [p.name for p in am._list_migrations(tmp_path, v2=True)] == ["00_extensions.sql"]

    (tmp_path / "bad.sql").write_text("select 1;", encoding="utf-8")
    with pytest.raises(RuntimeError, match="NN_slug.sql"):
        am._list_migrations(tmp_path, v2=True)

