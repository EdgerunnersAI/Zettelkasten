"""Unit tests for ops.scripts.lib.driver_logging.setup_driver_logging."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ops.scripts.lib.driver_logging import setup_driver_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    """Snapshot/restore root logger config so tests don't leak handlers."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_creates_iter_dir(tmp_path: Path) -> None:
    target = tmp_path / "newly" / "nested" / "iter-x"
    assert not target.exists()
    setup_driver_logging(target)
    assert target.is_dir()


def test_warning_written_to_driver_run_log(tmp_path: Path) -> None:
    setup_driver_logging(tmp_path)
    logging.getLogger("test_driver_logging").warning("hello-from-test")
    # Flush all handlers so content lands on disk before we read it.
    for h in logging.getLogger().handlers:
        h.flush()
    log_path = tmp_path / "driver_run.log"
    assert log_path.exists(), "driver_run.log not created"
    contents = log_path.read_text(encoding="utf-8")
    assert "hello-from-test" in contents
    assert "WARNING" in contents


def test_idempotent_no_duplicate_handlers(tmp_path: Path) -> None:
    setup_driver_logging(tmp_path)
    first = len(logging.getLogger().handlers)
    setup_driver_logging(tmp_path)
    second = len(logging.getLogger().handlers)
    # force=True clears prior handlers, so handler count must stay equal.
    assert second == first, f"handler count changed: {first} -> {second}"
