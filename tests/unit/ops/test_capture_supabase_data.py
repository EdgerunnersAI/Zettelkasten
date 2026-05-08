# tests/unit/ops/test_capture_supabase_data.py
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ops.scripts.capture_supabase_data import compute_sha256, scan_for_secrets


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
