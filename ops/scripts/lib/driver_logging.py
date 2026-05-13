"""Shared logging setup for eval drivers.

Ensures driver_run.log captures logs from the driver AND any libraries
(key_pool warnings, summarization_engine info, etc.) instead of being
0 bytes due to stdout buffering on Windows redirect.
"""
from __future__ import annotations

import atexit
import logging
import sys
from pathlib import Path


def setup_driver_logging(iter_dir: Path, level: int = logging.INFO) -> None:
    iter_dir = Path(iter_dir)
    iter_dir.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(iter_dir / "driver_run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,  # override any pre-existing config from imports
    )
    atexit.register(logging.shutdown)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
