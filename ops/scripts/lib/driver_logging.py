"""Shared logging setup for eval drivers.

Ensures driver_run.log captures logs from the driver AND any libraries
(key_pool warnings, summarization_engine info, etc.) instead of being
0 bytes due to stdout buffering on Windows redirect.

Console policy (2026-05-13): FileHandler keeps INFO+WARNING+ERROR for full
forensic capture; StreamHandler is set to ERROR only so the operator sees
just the final scorecard JSON instead of a wall of rate-limit / AFC /
langfuse noise. All INFO/WARNING traffic still lands in driver_run.log.
"""
from __future__ import annotations

import atexit
import logging
import sys
from pathlib import Path

DEFAULT_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_driver_logging(
    iter_dir: Path,
    level: int = logging.INFO,
    *,
    console_level: int = logging.ERROR,
) -> None:
    """Wire dual-sink logging for an eval driver.

    Args:
        iter_dir:      directory to write driver_run.log into.
        level:         FileHandler level (root logger) — default INFO captures
                       everything in driver_run.log.
        console_level: StreamHandler level — default ERROR silences INFO and
                       WARNING noise from the operator's console; library
                       warnings still go to driver_run.log.
    """
    iter_dir = Path(iter_dir)
    iter_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(iter_dir / "driver_run.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(DEFAULT_FILE_FORMAT))

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(console_level)
    stream_handler.setFormatter(logging.Formatter(DEFAULT_FILE_FORMAT))

    logging.basicConfig(
        level=level,
        handlers=[file_handler, stream_handler],
        force=True,  # override any pre-existing config from imports
    )

    atexit.register(logging.shutdown)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
