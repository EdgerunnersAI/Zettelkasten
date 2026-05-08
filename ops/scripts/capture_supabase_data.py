"""Capture Supabase bootstrap material into docs/supabase_data/.

Reads from local sources (schemas in repo, graph.json on disk, Obsidian markdown
corpus on the user's Syncthing vault, optional iter-12 audit captures) and
copies them into docs/supabase_data/ with sha256 round-trip verification. The
goal is a self-contained migration package for a new Supabase project after
the previous project was banned and its DNS pulled.

CLI:
    python ops/scripts/capture_supabase_data.py \\
        --repo-root . \\
        --corpus-path "C:\\Users\\LENOVO\\Documents\\Syncthing\\Obsidian\\KG" \\
        --output-dir docs/supabase_data \\
        [--dry-run] [--max-corpus-mb 50]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

_CHUNK = 1024 * 1024  # 1 MiB streaming read

_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "sb_sec_": re.compile(r"sb_sec_[A-Za-z0-9_-]{32,}"),
    "AIzaSy": re.compile(r"AIzaSy[A-Za-z0-9_-]{30,}"),
    "ghp_": re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    "xoxb-": re.compile(r"xoxb-[0-9]+-[0-9]+-[A-Za-z0-9]+"),
    "eyJhbGc": re.compile(r"eyJhbGciOiJIUzI1NiI[A-Za-z0-9_./\-]+\.[A-Za-z0-9_./\-]+\.[A-Za-z0-9_./\-]+"),
}


def compute_sha256(path: Path) -> str:
    """Stream-read a file and return its hex sha256."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def scan_for_secrets(text: str) -> list[str]:
    """Return the list of secret-pattern *names* that match anywhere in text."""
    return [name for name, pat in _SECRET_PATTERNS.items() if pat.search(text)]


def copy_with_verify(src: Path, dst: Path) -> dict:
    """Copy src -> dst, verify sha256 round-trips. Returns manifest entry.

    Raises FileNotFoundError if src missing; RuntimeError on hash mismatch.
    """
    if not src.is_file():
        raise FileNotFoundError(f"source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_hash = compute_sha256(src)
    shutil.copy2(src, dst)
    dst_hash = compute_sha256(dst)
    if src_hash != dst_hash:
        raise RuntimeError(f"sha256 mismatch after copy: {src} -> {dst}")
    return {
        "src": str(src),
        "dst": str(dst),
        "size": dst.stat().st_size,
        "sha256": dst_hash,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
