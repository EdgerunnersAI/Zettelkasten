"""Guardrails for the newsletter paywall_fallbacks config + provider registry.

12ft.io was decommissioned in July 2025 — these tests prevent it from being
re-added by accident.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from website.features.summarization_engine.source_ingest.newsletter import ingest


_CONFIG_PATH = (
    Path(__file__).resolve().parents[4]
    / "website"
    / "features"
    / "summarization_engine"
    / "config.yaml"
)


def _load_paywall_fallbacks() -> list[str]:
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    return list(data["sources"]["newsletter"]["paywall_fallbacks"])


def test_config_does_not_contain_twelveft():
    assert "twelveft" not in _load_paywall_fallbacks()


def test_config_contains_freedium():
    assert "freedium" in _load_paywall_fallbacks()


def test_provider_order_does_not_contain_twelveft():
    assert "twelveft" not in ingest._PROVIDER_ORDER


def test_twelveft_handler_removed():
    assert not hasattr(ingest, "_twelveft")
