"""WM-04 / WM-15 / WM-16: end-to-end payload assertions for the 3 notifiers.

Drives ``notify_new_signup``, ``notify_pricing_visit``, and ``notify_payment``
through the ``slack_webhook_mock`` fixture, captures the JSON Slack would
have received, and asserts:

* WM-04: no raw email / no bare full email in any Slack body
* WM-15: resolved display_name appears in body AND fields block, with the
  documented fallback ladder (display_name → email-localpart)
* WM-16: country code rendered as ``"India (IN)"`` not bare ``"IN"``
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from website.features.web_monitor import User_Activity as ua_mod
from website.features.web_monitor.User_Activity import (
    notify_new_signup,
    notify_payment,
    notify_pricing_visit,
)


@pytest.fixture(autouse=True)
def _reset_pricing_throttle():
    """The module-level `_pricing_seen_at` dict survives across tests; clear
    it so each test sees a fresh throttle window.  Without this the second
    pricing-visit test in CI ordering may short-circuit on a hit from a
    prior test and produce zero Slack calls (→ IndexError on calls[0])."""
    ua_mod._pricing_seen_at.clear()
    yield
    ua_mod._pricing_seen_at.clear()


def _flatten(payload: dict[str, Any]) -> str:
    """Stringify a Slack payload so regex assertions don't have to walk blocks."""
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Stub Request for notify_pricing_visit
# ---------------------------------------------------------------------------


class _StubRequest:
    """Minimal FastAPI-Request-shaped object for unit-testing notify_*."""

    def __init__(self, *, headers: dict[str, str] | None = None, client_host: str = "203.0.113.7"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})()


# ---------------------------------------------------------------------------
# WM-15 — notify_new_signup display_name resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_new_signup_uses_display_name_when_set(
    slack_webhook_mock, monkeypatch
):
    rec = slack_webhook_mock()
    await notify_new_signup(
        user_id="abcdef01-uuid-uuid-uuid-aaaaaaaaaaaa",
        email="alice@example.com",
        display_name="Alice Anderson",
    )
    calls = rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"]
    assert len(calls) == 1
    body = _flatten(calls[0])
    # WM-15: name appears in both body text and fields block
    assert "Alice Anderson" in body
    # Display name appears multiple times: heading-style body + name field
    assert body.count("Alice Anderson") >= 2
    # WM-04: masked email present, full email absent
    assert "a***@example.com" in body
    assert "alice@example.com" not in body


@pytest.mark.asyncio
async def test_notify_new_signup_falls_back_to_email_localpart_when_no_display_name(
    slack_webhook_mock,
):
    rec = slack_webhook_mock()
    await notify_new_signup(
        user_id="abcdef01-uuid-uuid-uuid-aaaaaaaaaaaa",
        email="bob@example.com",
        display_name=None,
    )
    body = _flatten(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"][0])
    # Fallback: local-part "bob" appears as the resolved name
    # Tighten: assert "bob" appears outside the masked email render
    # ("b***@example.com" doesn't contain "bob"), proving fallback worked.
    assert "bob" in body
    assert "b***@example.com" in body
    assert "bob@example.com" not in body


@pytest.mark.asyncio
async def test_notify_new_signup_empty_display_name_treated_as_unset(slack_webhook_mock):
    rec = slack_webhook_mock()
    await notify_new_signup(
        user_id="abcdef01-uuid-uuid-uuid-aaaaaaaaaaaa",
        email="carol@example.com",
        display_name="   ",  # whitespace-only must NOT win over fallback
    )
    body = _flatten(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"][0])
    assert "carol" in body
    # Whitespace-only display_name should NOT be rendered as a separate field value
    assert '"   "' not in body


# ---------------------------------------------------------------------------
# WM-16 — country formatting on notify_pricing_visit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_pricing_visit_renders_country_with_name_and_code(slack_webhook_mock):
    rec = slack_webhook_mock()
    req = _StubRequest(
        headers={
            "x-forwarded-for": "203.0.113.42",
            "cf-ipcountry": "IN",
            "user-agent": "test-ua",
            "referer": "https://example.com/",
        },
    )
    await notify_pricing_visit(req)
    body = _flatten(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"][0])
    assert "India (IN)" in body, body
    # Guard the regression: bare "IN" as a standalone country field value
    # must not appear (it can appear inside "India (IN)" which is fine — we
    # use the literal field-value `"IN"` separated by quotes as the canary).
    assert '"country": "IN"' not in body
    assert '"country":"IN"' not in body


@pytest.mark.asyncio
async def test_notify_pricing_visit_unknown_country(slack_webhook_mock):
    rec = slack_webhook_mock()
    req = _StubRequest(headers={"cf-ipcountry": "ZZ"})
    await notify_pricing_visit(req)
    body = _flatten(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"][0])
    assert "Unknown (ZZ)" in body


# ---------------------------------------------------------------------------
# WM-15/16 — notify_payment plumbs through both
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_payment_includes_name_and_country(slack_webhook_mock):
    rec = slack_webhook_mock()
    await notify_payment(
        user_id="abcdef01-uuid-uuid-uuid-aaaaaaaaaaaa",
        email="paying@example.com",
        amount=499.0,
        currency="INR",
        plan="basic_monthly",
        provider="razorpay",
        provider_payment_id="pay_TEST",
        display_name="Dave Doolittle",
        country="IN",
    )
    body = _flatten(rec.calls["SLACK_WEBHOOK_USER_ACTIVITY"][0])
    assert "Dave Doolittle" in body
    assert "India (IN)" in body
    # WM-04: raw email never appears in payment payload either
    assert "paying@example.com" not in body
    assert "p***@example.com" in body


# ---------------------------------------------------------------------------
# WM-04 grep guard — no raw-email regex in any notifier payload
# ---------------------------------------------------------------------------


_RAW_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)


@pytest.mark.asyncio
async def test_no_notifier_leaks_raw_email_regex(slack_webhook_mock):
    rec = slack_webhook_mock()

    await notify_new_signup(
        user_id="u" * 12,
        email="eve@example.com",
        display_name="Eve",
    )
    req = _StubRequest(headers={"cf-ipcountry": "US"})
    await notify_pricing_visit(req)
    await notify_payment(
        user_id="u" * 12,
        email="fred@example.com",
        amount=199.0,
        display_name="Fred",
        country="US",
    )

    for env, payloads in rec.calls.items():
        for payload in payloads:
            body = _flatten(payload)
            for match in _RAW_EMAIL_RE.findall(body):
                # Masked emails contain `***` — those are fine.
                assert "***" in match, (
                    f"raw email leak in {env}: {match!r} (payload={body!r})"
                )
