"""HD-03 — color rule static scan against header.css + header.html.

CLAUDE.md "No purple" rule + Teal/Amber scope:
  * No purple/violet/lavender ANYWHERE (named, #A78BFA, #7C3AED, HSL hue 250-290).
  * No amber #D4A024 outside ``/knowledge-graph`` — the header IS rendered on
    every shell'd page (including /knowledge-graph), so it MUST NOT bake in
    amber-hex tokens directly. The shared header relies on CSS variables
    (``var(--accent)``) which the per-page stylesheet rebinds to amber on
    the /knowledge-graph route and to teal everywhere else.

The Playwright computed-style portion (asserting the actual rendered color
is teal on non-KG routes) lives in ``tests/integration/browser/`` because it
requires a running app. This file is the cheap static gate that catches
violations before they ever hit a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
HEADER_DIR = REPO_ROOT / "website" / "features" / "header"
HEADER_HTML = HEADER_DIR / "header.html"
HEADER_CSS = HEADER_DIR / "css" / "header.css"

# Amber/gold tokens reserved for /knowledge-graph. The shared header file
# must not hard-code these — it relies on CSS variables that the consuming
# page rebinds.
_AMBER_HEX = re.compile(r"#D4A024\b", re.IGNORECASE)
_AMBER_NAMED = re.compile(r"\bamber\b", re.IGNORECASE)


def test_header_css_exists():
    assert HEADER_CSS.exists(), f"header.css missing at {HEADER_CSS}"


def test_header_css_no_purple(static_color_scan):
    """Static scan: header.css contains no purple/violet/lavender values."""
    text = HEADER_CSS.read_text(encoding="utf-8")
    findings = static_color_scan(text, source=str(HEADER_CSS))
    assert not findings, (
        f"header.css contains banned purple values: "
        f"{[(f.line, f.match, f.rule) for f in findings]!r}"
    )


def test_header_html_no_purple(static_color_scan):
    """Static scan: header.html (inline SVG strokes etc.) has no purple."""
    text = HEADER_HTML.read_text(encoding="utf-8")
    findings = static_color_scan(text, source=str(HEADER_HTML))
    assert not findings, (
        f"header.html contains banned purple values: "
        f"{[(f.line, f.match, f.rule) for f in findings]!r}"
    )


def test_header_css_no_hardcoded_amber():
    """header.css must NOT bake the amber hex — /knowledge-graph rebinds the
    CSS variable. A hard-coded ``#D4A024`` here would leak gold onto every
    inner page (e.g. /home, /home/zettels) and violate the teal rule.
    """
    text = HEADER_CSS.read_text(encoding="utf-8")
    bad_hex = list(_AMBER_HEX.finditer(text))
    bad_name = list(_AMBER_NAMED.finditer(text))
    assert not bad_hex and not bad_name, (
        f"header.css hard-codes amber/gold (must use var(--accent) so /knowledge-graph "
        f"rebinds it). hex hits: {[m.group(0) for m in bad_hex]!r}, "
        f"named hits: {[m.group(0) for m in bad_name]!r}"
    )


def test_header_html_no_hardcoded_amber():
    text = HEADER_HTML.read_text(encoding="utf-8")
    bad_hex = list(_AMBER_HEX.finditer(text))
    bad_name = list(_AMBER_NAMED.finditer(text))
    assert not bad_hex and not bad_name, (
        f"header.html hard-codes amber/gold. hex hits: "
        f"{[m.group(0) for m in bad_hex]!r}, named: {[m.group(0) for m in bad_name]!r}"
    )
