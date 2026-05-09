"""Live integration test for adding zettels to a kasten under the v2 schema.

Replaces the iter-06 regression guard for the now-retired
``rag_bulk_add_to_sandbox`` RPC. Under the v2 schema, kasten membership is a
direct INSERT into ``rag.kasten_zettels`` rather than an RPC call:

    INSERT INTO rag.kasten_zettels (kasten_id, workspace_zettel_id, added_via)
    VALUES ($1, $2, 'manual') ON CONFLICT DO NOTHING;

The supabase-py equivalent is::

    client.schema("rag").table("kasten_zettels").insert([
        {"kasten_id": k, "workspace_zettel_id": wz, "added_via": "manual"}
        for wz in workspace_zettel_ids
    ]).execute()

Required env vars:
    SUPABASE_V2_URL, SUPABASE_V2_ANON_KEY, SUPABASE_V2_SERVICE_ROLE_KEY,
    TEST_KASTEN_ID, TEST_WORKSPACE_ZETTEL_IDS (comma-separated UUIDs).

Run with::

    pytest tests/integration_tests/test_rag_sandbox_rpc.py --live -v

NOTE: Skipped until the v2 integration test framework lands (Phase 6 of the
db-v2 plan). See ``docs/superpowers/plans/2026-05-08-db-refactor-implementation.md``.
"""
from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_KASTEN_ID") or not os.environ.get("TEST_WORKSPACE_ZETTEL_IDS"),
    reason="v2 integration fixtures not configured (Phase 6 of db-v2 plan).",
)


@pytest.mark.live
def test_kasten_zettels_bulk_insert_returns_added_rows() -> None:
    """Direct INSERT into rag.kasten_zettels returns one row per zettel.

    Replaces the old rag_bulk_add_to_sandbox RPC test. The v2 path uses
    PostgREST INSERT (no RPC) and ``returning='representation'`` to confirm
    every requested row landed.
    """
    from website.core.supabase_v2.client import get_v2_client  # local import; v2 may not be configured at collection time

    client = get_v2_client()
    kasten_id = os.environ["TEST_KASTEN_ID"]
    workspace_zettel_ids = [
        wz.strip() for wz in os.environ["TEST_WORKSPACE_ZETTEL_IDS"].split(",") if wz.strip()
    ]
    rows = [
        {
            "kasten_id": kasten_id,
            "workspace_zettel_id": wz,
            "added_via": "manual",
        }
        for wz in workspace_zettel_ids
    ]

    result = (
        client.schema("rag")
        .table("kasten_zettels")
        .insert(rows, returning="representation")
        .execute()
    )

    assert result.data is not None, f"silent no-op: {result}"
    assert len(result.data) == len(rows), (
        f"expected {len(rows)} inserted rows, got {len(result.data)}: {result.data}"
    )
