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


def test_console_handler_silenced_to_error_by_default(tmp_path: Path) -> None:
    """StreamHandler is ERROR-only by default so INFO/WARNING noise stays
    in driver_run.log and doesn't clutter operator's console."""
    setup_driver_logging(tmp_path)
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers, "no StreamHandler attached"
    for sh in stream_handlers:
        assert sh.level == logging.ERROR, (
            f"console handler level={sh.level} (expected ERROR={logging.ERROR})"
        )
    # FileHandler still gets INFO+
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers, "no FileHandler attached"
    for fh in file_handlers:
        assert fh.level == logging.INFO, (
            f"file handler level={fh.level} (expected INFO={logging.INFO})"
        )


def test_console_level_can_be_overridden(tmp_path: Path) -> None:
    """Operator can opt into INFO/DEBUG on console via console_level kwarg."""
    setup_driver_logging(tmp_path, console_level=logging.INFO)
    root = logging.getLogger()
    stream_handlers = [
        h for h in root.handlers if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers
    for sh in stream_handlers:
        assert sh.level == logging.INFO
