"""Helpers for minting v2 Kasten rows via the real ``/api/rag/sandboxes`` route.

WAVE-B Phase 1a — the canonical kasten-create path enforces
``require_entitlement(Meter.KASTEN, ...)``, which we cannot satisfy by seeding
entitlements (pricing-module-authority rule in CLAUDE.md forbids it). The
fixture monkey-patches the route module's ``require_entitlement`` and
``consume_entitlement`` symbols to no-ops for the duration of the TestClient
context — this is the same bypass pattern ``tests/integration/v2/test_sandbox_routes_v2.py``
already uses, so it carries no new risk.

Cleanup of the row itself is delegated to ``rag.kastens`` ``ON DELETE CASCADE``
when the owning workspace/profile is dropped during the ``mint_user`` teardown;
the parallel ``created_sandbox_ids`` list registered by the fixture is an
explicit belt for cases where the test mints a kasten under a workspace that
will outlive the test (none of the current WAVE-B tests do, but the explicit
cleanup is cheap insurance).
"""
from __future__ import annotations

import uuid
from typing import NamedTuple


class MintedKasten(NamedTuple):
    """Result of the ``mint_kasten`` fixture factory.

    ``sandbox_id`` is the ``rag.kastens.id`` UUID. ``owner_user_sub`` is the
    auth-user UUID of the kasten owner (== the ``sub`` claim in the JWT used
    to create the row, == the ``core.profiles.id`` under today's invariant).
    """

    sandbox_id: uuid.UUID
    name: str
    owner_user_sub: uuid.UUID
