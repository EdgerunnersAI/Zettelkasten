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


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(body: str) -> dict:
    """Tiny YAML subset parser — handles `key: value` and `key: [a, b]` only.

    Sufficient for Obsidian frontmatter shape produced by this app's writer.
    Returns {} when no frontmatter block is present.
    """
    match = _FRONTMATTER_RE.match(body)
    if not match:
        return {}
    data: dict = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            data[key] = [item.strip() for item in inner.split(",") if item.strip()]
        else:
            data[key] = value
    return data


def build_obsidian_index(corpus_dir: Path) -> list[dict]:
    """Walk corpus, return one entry per .md file with title/url/tags/mtime/sha256.

    Skips `.obsidian/`, `.trash/`, attachments, and any non-.md file.
    """
    if not corpus_dir.is_dir():
        return []
    entries: list[dict] = []
    for path in corpus_dir.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(corpus_dir).parts):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        fm = parse_frontmatter(body)
        title = (
            str(fm.get("title") or "").strip()
            or _first_h1(body)
            or path.stem
        )
        entries.append({
            "path": str(path),
            "title": title,
            "url": str(fm.get("url") or "").strip(),
            "tags": list(fm.get("tags") or []),
            "mtime": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "sha256": compute_sha256(path),
            "size": path.stat().st_size,
        })
    return entries


def _first_h1(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def build_manifest(entries: list[dict], *, generator: str) -> dict:
    """Aggregate per-file capture entries into the top-level manifest dict."""
    return {
        "generator": generator,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(entries),
        "total_bytes": sum(int(e.get("size", 0)) for e in entries),
        "files": entries,
    }


def sweep_corpus_for_secrets(corpus_dir: Path) -> list[dict]:
    """Walk corpus, flag any *.md file whose contents match a known secret pattern."""
    hits: list[dict] = []
    if not corpus_dir.is_dir():
        return hits
    for path in corpus_dir.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(corpus_dir).parts):
            continue
        try:
            patterns = scan_for_secrets(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        if patterns:
            hits.append({"path": str(path), "patterns": patterns})
    return hits
