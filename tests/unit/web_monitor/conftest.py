"""Per-test resets for ``website.features.web_monitor`` unit tests.

Mirrors ``tests/integration/web_monitor/conftest.py`` (M-8) so unit tests
exercising ``_slack_client._sem`` also get a fresh Semaphore bound to the
current test's event loop. Without this, a Semaphore acquired under one
test's loop survives into a later test's loop and ``acquire`` raises
``RuntimeError: ... bound to a different event loop`` (Python 3.10+
behaviour change).
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_slack_semaphore():
    """Per-test reset of ``_slack_client._sem`` to current event loop."""
    from website.features.web_monitor import _slack_client

    _slack_client._sem = asyncio.Semaphore(_slack_client._MAX_INFLIGHT)
    yield
