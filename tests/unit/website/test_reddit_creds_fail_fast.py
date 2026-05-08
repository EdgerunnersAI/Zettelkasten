"""Reddit OAuth credential gate.

In production (ENV=production) AND Reddit OAuth credentials missing, startup
must raise RuntimeError. In non-production, missing creds produce a one-shot
warning instead.
"""
from __future__ import annotations

import logging

import pytest

import website.core.settings as settings_module
from website.core.settings import Settings, validate_reddit_credentials


@pytest.fixture(autouse=True)
def _reset_warning_latch(monkeypatch):
    """Each test starts with the warning latch cleared and ENV unset."""
    settings_module._reddit_warning_emitted = False
    monkeypatch.delenv("ENV", raising=False)
    yield
    settings_module._reddit_warning_emitted = False


def _s(**overrides) -> Settings:
    base = dict(reddit_client_id="", reddit_client_secret="")
    base.update(overrides)
    return Settings(**base)


def test_production_with_missing_reddit_creds_raises(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="Reddit OAuth"):
        validate_reddit_credentials(_s())


def test_production_with_full_reddit_creds_no_raise(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    validate_reddit_credentials(
        _s(reddit_client_id="id", reddit_client_secret="secret")
    )


def test_non_production_with_missing_creds_warns_not_raises(caplog):
    caplog.set_level(logging.WARNING)
    validate_reddit_credentials(_s())
    assert any("Reddit OAuth" in r.getMessage() for r in caplog.records)


def test_non_production_warning_fires_only_once(caplog):
    caplog.set_level(logging.WARNING)
    validate_reddit_credentials(_s())
    validate_reddit_credentials(_s())
    validate_reddit_credentials(_s())
    reddit_warnings = [
        rec for rec in caplog.records
        if "Reddit OAuth" in rec.getMessage()
    ]
    assert len(reddit_warnings) == 1
