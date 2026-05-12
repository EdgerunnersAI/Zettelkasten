"""WM-04: PII redaction — _mask_email boundary cases.

Pure-function unit tests; no Slack, no env vars required.
"""
from __future__ import annotations

import pytest

from website.features.web_monitor.User_Activity import _mask_email


@pytest.mark.parametrize(
    "email,expected",
    [
        # Happy path: typical OAuth email
        ("alice@example.com", "a***@example.com"),
        ("BOB@example.com", "B***@example.com"),
        # Boundary: single-char local part — must NOT leak the only char
        ("a@b.c", "*@b.c"),
        # Boundary: empty local part (RFC-invalid but possible from bad input)
        ("@b.c", "*@b.c"),
        # Boundary: empty domain — `_mask_email` accepts and renders empty domain
        ("a@", "*@"),
        # No @ symbol — fall through unchanged (treated as non-email)
        ("not-an-email", "not-an-email"),
        # None / empty
        (None, "—"),
        ("", "—"),
        # Unicode local part — preserve first char correctness
        ("ünicode@example.com", "ü***@example.com"),
        # Multiple @ signs — partition picks the first; local="a" hits the
        # len<=1 branch so result is "*@b@c.com" (NOT "a***@b@c.com")
        ("a@b@c.com", "*@b@c.com"),
        # Longer local with multiple @ — first char visible, rest masked
        ("alice@b@c.com", "a***@b@c.com"),
    ],
)
def test_mask_email_boundary_cases(email, expected):
    assert _mask_email(email) == expected


def test_mask_email_never_leaks_full_local_part():
    """Regression guard: longer local parts must NOT have more than 1 char
    visible. Catches a class of off-by-one bugs where someone "improves" the
    mask to show 2 chars and silently widens PII exposure."""
    masked = _mask_email("john.doe.long.name@corp.example.org")
    assert masked == "j***@corp.example.org"
    # Visible local chars (everything before the first '*')
    assert masked.split("*", 1)[0] == "j"
