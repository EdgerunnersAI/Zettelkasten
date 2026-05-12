"""WM-14: boot-time env validation emits one warning per unset SLACK_WEBHOOK_*."""
from __future__ import annotations

import logging

import pytest

from website.features.web_monitor._env_validation import (
    log_web_monitor_env_warnings,
)

_VARS = (
    "SLACK_WEBHOOK_APP_ERRORS",
    "SLACK_WEBHOOK_DO_ALERT",
    "SLACK_WEBHOOK_USER_ACTIVITY",
)


@pytest.fixture(autouse=True)
def _clear_slack_env(monkeypatch):
    """Each test starts with all 3 vars cleared so the fixture is hermetic."""
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_all_vars_unset_logs_three_warnings(caplog):
    with caplog.at_level(logging.WARNING, logger="website.web_monitor.env_validation"):
        unset = log_web_monitor_env_warnings()
    assert set(unset) == set(_VARS)
    warning_records = [
        r for r in caplog.records
        if r.name == "website.web_monitor.env_validation" and r.levelno == logging.WARNING
    ]
    assert len(warning_records) == 3
    # Each warning must name its env var so ops can grep for the missing one.
    var_mentions = {r.getMessage() for r in warning_records}
    for v in _VARS:
        assert any(v in msg for msg in var_mentions), f"missing {v} in {var_mentions!r}"


def test_partial_unset_only_warns_for_missing(caplog, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_APP_ERRORS", "https://hooks.slack.com/x/y/z")
    with caplog.at_level(logging.WARNING, logger="website.web_monitor.env_validation"):
        unset = log_web_monitor_env_warnings()
    assert unset == ["SLACK_WEBHOOK_DO_ALERT", "SLACK_WEBHOOK_USER_ACTIVITY"]
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("SLACK_WEBHOOK_APP_ERRORS" in m for m in msgs)


def test_all_vars_set_logs_info_no_warnings(caplog, monkeypatch):
    for v in _VARS:
        monkeypatch.setenv(v, "https://hooks.slack.com/x/y/z")
    with caplog.at_level(logging.INFO, logger="website.web_monitor.env_validation"):
        unset = log_web_monitor_env_warnings()
    assert unset == []
    info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("all 3 Slack webhook env vars present" in m for m in info_msgs)
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records == []
