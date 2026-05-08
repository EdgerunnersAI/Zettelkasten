from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_full_backfill_dry_run_is_credential_free() -> None:
    result = subprocess.run(
        [sys.executable, "ops/scripts/refactor_v2/00_full_backfill.py", "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "profiles backfill ready" in result.stdout
    assert "backfill verification ready" in result.stdout


def test_canonical_backfill_casts_existing_embeddings_without_gemini() -> None:
    script = (ROOT / "ops/scripts/refactor_v2/02_backfill_canonical_content.py").read_text(encoding="utf-8")
    assert "embedding::halfvec" in script
    assert "embed_content" not in script
    assert "generate_content" not in script


def test_backfill_scripts_do_not_use_fictional_sql_rpcs() -> None:
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "ops/scripts/refactor_v2").glob("*.py")
    )
    assert "exec_sql_returning" not in scripts
    assert "execute_sql" not in scripts
