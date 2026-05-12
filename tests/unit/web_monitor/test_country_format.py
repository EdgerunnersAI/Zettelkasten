"""WM-16: country code → display-name formatting."""
from __future__ import annotations

import pytest

from website.features.web_monitor._country import format_country


@pytest.mark.parametrize(
    "code,expected",
    [
        # Happy path — top markets
        ("IN", "India (IN)"),
        ("US", "United States (US)"),
        ("GB", "United Kingdom (GB)"),
        ("DE", "Germany (DE)"),
        # Cloudflare-specific: XX = anonymous proxy / unknown
        ("XX", "Unknown (XX)"),
        # Unknown 2-letter code falls through with raw code preserved
        ("ZZ", "Unknown (ZZ)"),
        # Lowercase normalized to upper before lookup
        ("in", "India (IN)"),
        # Whitespace tolerated
        ("  IN  ", "India (IN)"),
        # None / empty / placeholder em-dash → em-dash sentinel
        (None, "—"),
        ("", "—"),
        ("—", "—"),
        ("-", "—"),
    ],
)
def test_format_country(code, expected):
    assert format_country(code) == expected


def test_format_country_top_market_india_invariant():
    """WM-16 spec line: "India (IN)" — guard the canonical happy-case string."""
    assert format_country("IN") == "India (IN)"
