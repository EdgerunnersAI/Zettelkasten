"""WM-15: display_name resolution for Slack payloads.

Source-of-truth is `core.profiles.display_name` (verified at
`supabase/website/_v2/01_core_schema.sql:7` — the spec's `profiles.full_name`
column does not exist post-DB-v2 cutover). The helper takes the value
already resolved by the caller; this test pins the fallback ladder.
"""
from __future__ import annotations

import pytest

from website.features.web_monitor.User_Activity import _resolve_full_name


@pytest.mark.parametrize(
    "display_name,email,expected",
    [
        # Happy path: display_name wins
        ("Alice Anderson", "alice@x.com", "Alice Anderson"),
        # Whitespace stripped
        ("  Bob  ", "bob@x.com", "Bob"),
        # Empty display_name → email local-part fallback
        (None, "carol@x.com", "carol"),
        ("", "dave@x.com", "dave"),
        ("   ", "erin@x.com", "erin"),
        # display_name absent and email malformed → em-dash sentinel
        (None, "no-at-sign", "—"),
        ("", "", "—"),
        (None, None, "—"),
        # Email local-part with dots / underscores preserved verbatim
        (None, "first.last@example.com", "first.last"),
        # Unicode display_name preserved
        ("Лев Толстой", None, "Лев Толстой"),
    ],
)
def test_resolve_full_name_fallback_chain(display_name, email, expected):
    assert _resolve_full_name(display_name=display_name, email=email) == expected
