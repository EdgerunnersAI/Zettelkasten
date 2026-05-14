"""Bug 1 regression: Add Zettel persistence must auto-trigger ingest_node_chunks.

Iter-06 production observation: rag_chunks_enabled=False default + Dockerfile
not bundling ops/config.yaml meant the hook never fired in prod. The fix is
to flip the in-code default to True so a fresh container ingests chunks even
without an env override.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


def test_rag_chunks_enabled_defaults_to_true():
    """The in-code default must be True so production droplet behaves correctly
    even when ops/config.yaml is not bundled into the image."""
    from website.core.settings import Settings

    # Force the env-loading path to ignore any local .env / config.yaml so we
    # observe the bare class default. Pydantic Settings reads sources at
    # construction time; instantiating with explicit unrelated kwargs only
    # avoids triggering required-field validation if any exist.
    s = Settings.model_construct()
    assert s.rag_chunks_enabled is True


@pytest.mark.asyncio
async def test_summarize_persist_schedules_ingest_when_flag_on(monkeypatch):
    """When rag_chunks_enabled is True and a node is freshly added, the
    ingest_node_chunks hook must be scheduled with the user UUID and node id."""
    from website.core import persist as persist_mod

    captured = {}

    async def _fake_ingest(*, payload, user_uuid, node_id):
        captured["payload"] = payload
        captured["user_uuid"] = user_uuid
        captured["node_id"] = node_id
        return 3

    # Patch the lazy import inside _schedule_rag_chunks._run.
    import website.features.rag_pipeline.ingest.hook as hook_mod
    monkeypatch.setattr(hook_mod, "ingest_node_chunks", _fake_ingest)

    fake_settings = MagicMock()
    fake_settings.rag_chunks_enabled = True
    monkeypatch.setattr(persist_mod, "get_settings", lambda: fake_settings)

    user_uuid = uuid4()
    payload = {
        "title": "Test Node",
        "summary": "A summary",
        "raw_text": "Body text",
        "source_type": "web",
        "source_url": "https://example.com",
        "tags": [],
    }

    # Drive _schedule_rag_chunks directly; it creates a task we await.
    persist_mod._schedule_rag_chunks(
        payload=payload, user_uuid=user_uuid, node_id="web-test-node"
    )

    # Drain the task created in _schedule_rag_chunks.
    import asyncio
    pending = [t for t in asyncio.all_tasks() if t.get_name().startswith("rag-chunks-")]
    assert pending, "expected a rag-chunks-* task to be scheduled"
    await asyncio.gather(*pending)

    assert captured["user_uuid"] == user_uuid
    assert captured["node_id"] == "web-test-node"
    assert captured["payload"]["title"] == "Test Node"
