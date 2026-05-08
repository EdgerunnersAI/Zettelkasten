"""Iter-03 mem-bounded §2.9: middleware MUST NOT shed exempt paths even when
VmRSS is over the threshold. Exempt prefixes: /api/health, /api/admin/,
/favicon.ico, /favicon.svg.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from website.api import _memory_guard
from website.app import create_app


@pytest.fixture
def under_pressure_app(monkeypatch):
    monkeypatch.setattr(_memory_guard, "_detect_mem_max", lambda: 1_000_000_000)
    monkeypatch.setattr(_memory_guard, "_read_vm_rss_bytes", lambda: 950_000_000)
    monkeypatch.setenv("RAG_MEMORY_GUARD_THRESHOLD_PERCENT", "90")
    return create_app()


def test_health_passes_through_under_pressure(under_pressure_app):
    client = TestClient(under_pressure_app)
    r = client.get("/api/health")
    assert r.status_code == 200


def test_favicon_ico_passes_through_under_pressure(under_pressure_app):
    client = TestClient(under_pressure_app)
    r = client.get("/favicon.ico")
    assert r.status_code in (200, 304, 404)


def test_favicon_svg_passes_through_under_pressure(under_pressure_app):
    client = TestClient(under_pressure_app)
    r = client.get("/favicon.svg")
    assert r.status_code in (200, 304, 404)


def test_admin_proc_stats_path_passes_through_under_pressure(under_pressure_app):
    client = TestClient(under_pressure_app)
    # Unauthenticated → 401 from auth dependency BEFORE the guard could 503.
    # The point: NOT 503. /api/admin/* prefix is exempt regardless of auth.
    r = client.get("/api/admin/_proc_stats")
    assert r.status_code != 503


