"""UH-03 (static portion) — purple/violet/lavender ban on /home assets.

CLAUDE.md hard rule: never use purple/violet/lavender (HSL 250-290,
``#A78BFA``, ``#7C3AED``, named tokens) anywhere in the UI; the only
allow-listed surface is ``/knowledge-graph``. /home is in scope.

This is the static-file pass — pure regex against the on-disk CSS + HTML.
The companion computed-style scan against a live browser is UH-03's
``@pytest.mark.live`` portion and lives next to the Playwright tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest


HOME_DIR = (
    Path(__file__).resolve().parents[3]
    / "website"
    / "features"
    / "user_home"
)


@pytest.mark.parametrize(
    "rel_path",
    [
        "css/home.css",
        "index.html",
        "js/home.js",
    ],
)
def test_user_home_static_assets_have_no_purple(rel_path, static_color_scan):
    """Every shipped /home asset must be purple-free (UH-03 static)."""
    target = HOME_DIR / rel_path
    assert target.exists(), f"expected file missing: {target}"
    text = target.read_text(encoding="utf-8")

    findings = static_color_scan(text, source=str(target), allow_paths=())
    assert not findings, (
        "purple/violet/lavender token detected in /home asset:\n"
        + "\n".join(
            f"  {f.file}:{f.line} [{f.rule}] {f.match!r}" for f in findings
        )
    )
