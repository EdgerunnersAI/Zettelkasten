"""Self-tests for the WAVE-D Phase 1 shared fixtures.

These tests exist to (a) prove the new conftest fixtures work in isolation
before module sub-agents consume them and (b) lock the public surface so a
later refactor cannot silently change the contract under feet of the
web_monitor / user_home / header impl tasks.
"""
from __future__ import annotations

import httpx
import pytest


def test_slack_webhook_mock_default_200(slack_webhook_mock, monkeypatch):
    """Default factory call: all 3 webhooks return 200; payloads recorded."""
    rec = slack_webhook_mock()  # default status=200

    # Post to each of the 3 mocked URLs and confirm the recorder caught it.
    import os

    for env_name in (
        "SLACK_WEBHOOK_APP_ERRORS",
        "SLACK_WEBHOOK_DO_ALERT",
        "SLACK_WEBHOOK_USER_ACTIVITY",
    ):
        url = os.environ[env_name]
        resp = httpx.post(url, json={"text": f"hello {env_name}"})
        assert resp.status_code == 200

    assert rec.total_calls() == 3
    assert rec.calls["SLACK_WEBHOOK_APP_ERRORS"][0]["text"] == "hello SLACK_WEBHOOK_APP_ERRORS"


def test_slack_webhook_mock_429_with_retry_after(slack_webhook_mock):
    """status=429 + retry_after sets the Retry-After response header."""
    rec = slack_webhook_mock(status=429, retry_after=5)
    import os

    url = os.environ["SLACK_WEBHOOK_APP_ERRORS"]
    resp = httpx.post(url, json={"text": "rate limited"})

    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "5"
    assert rec.total_calls() == 1


def test_static_color_scan_clean_input(static_color_scan):
    """Teal/amber are not flagged."""
    css = """
    .zettel { color: #0d9488; background: hsl(174, 60%, 40%); }
    .kg-amber { color: #D4A024; }
    """
    findings = static_color_scan(css, source="website/static/foo.css")
    assert findings == []


def test_static_color_scan_named_violation(static_color_scan):
    """The literal token 'purple' fires the named rule."""
    css = ".bad { color: purple; }"
    findings = static_color_scan(css, source="website/static/foo.css")
    assert len(findings) == 1
    assert findings[0].rule == "named"
    assert findings[0].match.lower() == "purple"


def test_static_color_scan_hex_violation(static_color_scan):
    """The tailwind violet hex #A78BFA fires the hex rule."""
    css = ".bad { background: #A78BFA; }"
    findings = static_color_scan(css, source="website/static/foo.css")
    assert any(f.rule == "hex" for f in findings)


def test_static_color_scan_hsl_violation(static_color_scan):
    """HSL hues in [250, 290] fire the hsl rule."""
    css = ".bad { color: hsl(265, 50%, 60%); }"
    findings = static_color_scan(css, source="website/static/foo.css")
    assert any(f.rule == "hsl" for f in findings)


def test_static_color_scan_knowledge_graph_allowlisted(static_color_scan):
    """KG scope is exempt — amber/gold rendering on the 3D viz isn't a regression."""
    css = ".kg-node { color: purple; }"  # would normally fail
    findings = static_color_scan(
        css,
        source="website/features/knowledge-graph/static/viz.css",
    )
    assert findings == []


def test_frozen_clock_anchor(frozen_clock):
    """frozen_clock anchors at 2026-05-12T00:00:00Z; .tick() advances without sleep."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert now == datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)

    frozen_clock.tick(timedelta(seconds=30))
    advanced = datetime.now(timezone.utc)
    assert advanced == datetime(2026, 5, 12, 0, 0, 30, tzinfo=timezone.utc)
