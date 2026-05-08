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


def _strip_surrounding_quotes(value: str) -> str:
    """Remove a single matched pair of surrounding ASCII quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


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
            data[key] = [_strip_surrounding_quotes(item.strip()) for item in inner.split(",") if item.strip()]
        else:
            data[key] = _strip_surrounding_quotes(value)
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
            "url": str(fm.get("url") or fm.get("source_url") or "").strip(),
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


def _capture_tree(src_root: Path, dst_root: Path) -> list[dict]:
    """Recursively copy every regular file from src_root to dst_root with verify."""
    entries: list[dict] = []
    if not src_root.exists():
        return entries
    for src in sorted(src_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        entries.append(copy_with_verify(src, dst))
    return entries


def _capture_obsidian_corpus(
    corpus_dir: Path,
    dst_root: Path,
    *,
    max_bytes: int,
) -> tuple[list[dict], list[dict]]:
    """Returns (manifest_entries, index_entries). Aborts via RuntimeError on:
       - corpus dir missing/empty
       - aggregate corpus size > max_bytes
       - any file matches a known secret pattern (operator must redact first).
    """
    if not corpus_dir.is_dir():
        raise RuntimeError(f"obsidian corpus path not found: {corpus_dir}")

    md_files = [p for p in corpus_dir.rglob("*.md")
                if not any(part.startswith(".") for part in p.relative_to(corpus_dir).parts)]
    if not md_files:
        raise RuntimeError(f"obsidian corpus has zero .md files: {corpus_dir}")

    total = sum(p.stat().st_size for p in md_files)
    if total > max_bytes:
        raise RuntimeError(
            f"obsidian corpus too large: {total} bytes > {max_bytes} budget. "
            "Set --max-corpus-mb higher or relocate corpus outside the repo."
        )

    secret_hits = sweep_corpus_for_secrets(corpus_dir)
    if secret_hits:
        raise RuntimeError(
            f"corpus contains apparent secrets in {len(secret_hits)} file(s): "
            f"{[h['path'] for h in secret_hits[:5]]} (showing up to 5). "
            "Redact and re-run."
        )

    entries: list[dict] = []
    for src in sorted(md_files):
        rel = src.relative_to(corpus_dir)
        dst = dst_root / "corpus" / rel
        entries.append(copy_with_verify(src, dst))

    index = build_obsidian_index(corpus_dir)
    (dst_root / "INDEX.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_corpus_stats(dst_root / "STATS.md", index, total)
    return entries, index


def _write_corpus_stats(path: Path, index: list[dict], total_bytes: int) -> None:
    by_url_prefix: dict[str, int] = {}
    for entry in index:
        url = entry.get("url", "")
        if "youtube" in url:
            by_url_prefix["youtube"] = by_url_prefix.get("youtube", 0) + 1
        elif "github.com" in url:
            by_url_prefix["github"] = by_url_prefix.get("github", 0) + 1
        elif "reddit" in url:
            by_url_prefix["reddit"] = by_url_prefix.get("reddit", 0) + 1
        elif "substack" in url or "buttondown" in url or "beehiiv" in url:
            by_url_prefix["newsletter"] = by_url_prefix.get("newsletter", 0) + 1
        elif url:
            by_url_prefix["web"] = by_url_prefix.get("web", 0) + 1
        else:
            by_url_prefix["no-url"] = by_url_prefix.get("no-url", 0) + 1
    lines = [
        "# Obsidian Corpus Stats",
        "",
        f"- file count: {len(index)}",
        f"- total bytes: {total_bytes}",
        "",
        "## URL-host histogram",
        "",
    ]
    for k, v in sorted(by_url_prefix.items()):
        lines.append(f"- {k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def post_capture_audit(manifest_path: Path) -> None:
    """Re-walk every dst path, recompute sha256, diff against MANIFEST.json.

    Raises RuntimeError on any drift.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    drift: list[str] = []
    for entry in manifest["files"]:
        dst = Path(entry["dst"])
        if not dst.is_file():
            drift.append(f"missing: {dst}")
            continue
        actual = compute_sha256(dst)
        if actual != entry["sha256"]:
            drift.append(f"sha256 mismatch: {dst} expected={entry['sha256']} actual={actual}")
    if drift:
        raise RuntimeError("post-capture audit found drift:\n  " + "\n  ".join(drift))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--corpus-path", type=Path, required=True,
                        help="Obsidian markdown vault root, e.g. C:\\\\Users\\\\LENOVO\\\\Documents\\\\Syncthing\\\\Obsidian\\\\KG")
    parser.add_argument("--output-dir", type=Path, default=Path("docs/supabase_data"))
    parser.add_argument("--max-corpus-mb", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    if args.dry_run:
        print(f"[dry-run] would capture into {output_dir}")
        return 0

    print(f"[capture] writing into {output_dir}")
    all_entries: list[dict] = []

    # 1. schemas
    src = repo_root / "supabase" / "website"
    dst = output_dir / "schemas"
    print(f"[capture] schemas: {src} -> {dst}")
    all_entries.extend(_capture_tree(src, dst))

    # 2. graph.json
    src = repo_root / "website" / "features" / "knowledge_graph" / "content" / "graph.json"
    dst = output_dir / "file_store_fallback" / "graph.json"
    if src.is_file():
        print(f"[capture] graph.json: {src} -> {dst}")
        all_entries.append(copy_with_verify(src, dst))
    else:
        print(f"[capture] WARN graph.json missing at {src}")

    # 3. obsidian corpus
    corpus_dst = output_dir / "obsidian_export"
    print(f"[capture] obsidian corpus: {args.corpus_path} -> {corpus_dst}")
    corpus_entries, _ = _capture_obsidian_corpus(
        args.corpus_path, corpus_dst, max_bytes=args.max_corpus_mb * 1024 * 1024,
    )
    all_entries.extend(corpus_entries)

    # 4. iter-12 audit (conditional)
    audit_src = repo_root / "_audit"
    audit_dst = output_dir / "audit"
    if audit_src.is_dir():
        print(f"[capture] audit: {audit_src} -> {audit_dst}")
        all_entries.extend(_capture_tree(audit_src, audit_dst))
    else:
        print(f"[capture] no _audit/ dir at {audit_src}; skipped")

    # 5. manifest + post-audit
    manifest = build_manifest(all_entries, generator="capture_supabase_data.py")
    manifest_path = output_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[capture] manifest: {manifest_path} ({manifest['file_count']} files / {manifest['total_bytes']} bytes)")

    print("[audit] re-walking destination tree...")
    post_capture_audit(manifest_path)
    print(f"OK: {manifest['file_count']} files / {manifest['total_bytes']} bytes captured, all sha256 verified, manifest at {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
