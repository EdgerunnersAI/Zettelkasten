# tests/unit/ops/test_capture_supabase_data.py
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ops.scripts.capture_supabase_data import (
    compute_sha256,
    scan_for_secrets,
    copy_with_verify,
    build_obsidian_index,
    parse_frontmatter,
    build_manifest,
    sweep_corpus_for_secrets,
)


def test_compute_sha256_matches_hashlib(tmp_path: Path) -> None:
    payload = b"hello supabase capture\n"
    target = tmp_path / "f.txt"
    target.write_bytes(payload)

    expected = hashlib.sha256(payload).hexdigest()
    assert compute_sha256(target) == expected


def test_compute_sha256_streams_large_files(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"
    chunk = b"x" * 1024
    with target.open("wb") as fh:
        for _ in range(2048):  # 2 MiB
            fh.write(chunk)
    expected = hashlib.sha256(b"x" * 1024 * 2048).hexdigest()
    assert compute_sha256(target) == expected


def test_scan_for_secrets_detects_known_prefixes() -> None:
    body = (
        "Random note content. "
        "token=sb_sec_" + "A" * 40 + ", "
        "key=AIzaSyB" + "x" * 35 + ", "
        "github=ghp_" + "a" * 36 + ", "
        "slack=xoxb-1234567890-1234567890-aaaaaaaaaaaaaaaa, "
        "supabase=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGVzdHRlc3R0ZXN0"
    )
    hits = scan_for_secrets(body)
    assert "sb_sec_" in hits
    assert "AIzaSy" in hits
    assert "ghp_" in hits
    assert "xoxb-" in hits
    assert "eyJhbGc" in hits


def test_scan_for_secrets_clean_text_returns_empty() -> None:
    body = "An ordinary obsidian note about productivity. No secrets here."
    assert scan_for_secrets(body) == []


def test_scan_for_secrets_does_not_match_short_prefixes_in_prose() -> None:
    """Common prose containing secret-prefix-like substrings must not flag."""
    body = (
        "The AIzaSynaptic approach to ML. The ghp_protocol overview. "
        "My organization is sb_sec_oriented. JWT-style content."
    )
    assert scan_for_secrets(body) == []


def test_copy_with_verify_copies_and_verifies(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "out" / "dst.txt"
    src.write_bytes(b"hello\n")

    entry = copy_with_verify(src, dst)
    assert dst.read_bytes() == b"hello\n"
    assert entry["src"] == str(src)
    assert entry["dst"] == str(dst)
    assert entry["size"] == 6
    assert entry["sha256"] == hashlib.sha256(b"hello\n").hexdigest()


def test_copy_with_verify_raises_on_missing_source(tmp_path: Path) -> None:
    src = tmp_path / "nope.txt"
    dst = tmp_path / "dst.txt"
    with pytest.raises(FileNotFoundError):
        copy_with_verify(src, dst)


def test_copy_with_verify_creates_parent_dirs(tmp_path: Path) -> None:
    src = tmp_path / "s.txt"
    dst = tmp_path / "a" / "b" / "c" / "d.txt"
    src.write_bytes(b"deep")
    copy_with_verify(src, dst)
    assert dst.exists()


def test_parse_frontmatter_extracts_yaml_block() -> None:
    body = "---\ntitle: Hello\nurl: https://example.com\ntags: [a, b]\n---\n# Hello\n\nbody"
    fm = parse_frontmatter(body)
    assert fm["title"] == "Hello"
    assert fm["url"] == "https://example.com"
    assert fm["tags"] == ["a", "b"]


def test_parse_frontmatter_strips_surrounding_quotes() -> None:
    body = (
        "---\n"
        'title: "jina-ai/reader"\n'
        "url: 'https://example.com'\n"
        'tags: ["a", "b"]\n'
        "---\nbody"
    )
    fm = parse_frontmatter(body)
    assert fm["title"] == "jina-ai/reader"
    assert fm["url"] == "https://example.com"
    assert fm["tags"] == ["a", "b"]


def test_parse_frontmatter_returns_empty_when_absent() -> None:
    assert parse_frontmatter("# Just a heading\n\nNo frontmatter") == {}


def test_build_obsidian_index_falls_back_to_source_url(tmp_path: Path) -> None:
    """The bot's writer emits `source_url:` (not `url:`) — the index must read either."""
    (tmp_path / "n.md").write_text(
        "---\n"
        'title: "x"\n'
        'source_url: "https://example.com/a"\n'
        "---\n# x"
    )
    entries = build_obsidian_index(tmp_path)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://example.com/a"


def test_build_obsidian_index_walks_recursively(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntitle: A\nurl: https://a.com\n---\n# A")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("---\ntitle: B\nurl: https://b.com\ntags: [x]\n---\n# B")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "skip.md").write_text("hidden")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    entries = build_obsidian_index(tmp_path)
    titles = sorted(e["title"] for e in entries)
    assert titles == ["A", "B"]
    # `.obsidian/` and binaries excluded
    assert all(not e["path"].endswith("skip.md") for e in entries)
    assert all(e["path"].endswith(".md") for e in entries)


def test_build_manifest_aggregates_entries() -> None:
    entries = [
        {"src": "a", "dst": "b", "size": 10, "sha256": "h1", "captured_at": "t"},
        {"src": "c", "dst": "d", "size": 20, "sha256": "h2", "captured_at": "t"},
    ]
    manifest = build_manifest(entries, generator="capture_supabase_data.py")
    assert manifest["file_count"] == 2
    assert manifest["total_bytes"] == 30
    assert manifest["files"] == entries
    assert manifest["generator"] == "capture_supabase_data.py"
    assert "generated_at" in manifest


def test_sweep_corpus_for_secrets_returns_offending_files(tmp_path: Path) -> None:
    clean = tmp_path / "clean.md"
    dirty = tmp_path / "dirty.md"
    clean.write_text("nothing to see")
    dirty.write_text("here is a key: AIzaSy" + "A" * 35)

    hits = sweep_corpus_for_secrets(tmp_path)
    assert len(hits) == 1
    assert hits[0]["path"] == str(dirty)
    assert "AIzaSy" in hits[0]["patterns"]
