"""Per-test resets for ``website.features.web_monitor`` integration tests.

Module-level state in ``_slack_client`` (the ``asyncio.Semaphore`` permit
counter + the ``_inflight`` task set) survives across tests in the same
worker. A test that crashes mid-burst can leave the semaphore at a
non-default permit count, which silently skews the saturation assertions
of subsequent tests. This conftest restores both to a clean slate before
every test, so failures are local and reproducible.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_slack_semaphore():
    """Per-test reset of ``_slack_client._sem`` to prevent cross-test counter drift."""
    from website.features.web_monitor import _slack_client

    _slack_client._sem = asyncio.Semaphore(_slack_client._MAX_INFLIGHT)
    yield
