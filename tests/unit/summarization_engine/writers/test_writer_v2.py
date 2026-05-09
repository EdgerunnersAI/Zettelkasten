"""v2 unit tests for the SupabaseWriter (Phase 3.1 — supabase_kg purge).

Asserts:
1. The writer no longer imports anything from ``website.core.supabase_kg``.
2. ``write(...)`` calls ``ContentRepository.upsert_canonical_zettel`` with
   a workspace overlay carrying ``ai_summary`` (text), NOT a legacy
   ``kg_nodes`` row with ``summary`` (jsonb).
3. The workspace_id is sourced from
   ``CoreRepository.get_default_workspace_id`` (NOT NULL guarantee on
   ``content.workspace_zettels``).
4. ``BaseWriter.write(result, *, user_id)`` signature is preserved.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from website.core.supabase_v2.models import CanonicalUpsertResult
from website.features.summarization_engine.core.models import (
    SourceType,
    SummaryMetadata,
    SummaryResult,
)
from website.features.summarization_engine.writers.base import BaseWriter
from website.features.summarization_engine.writers.supabase import (
    SupabaseWriter,
    _content_hash_bytes,
    _encode_summary_blob,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_summary(url: str = "https://example.com/post") -> SummaryResult:
    metadata = SummaryMetadata(
        source_type=SourceType.WEB,
        url=url,
        extraction_confidence="high",
        confidence_reason="test",
        total_tokens_used=10,
        total_latency_ms=1,
        engine_version="2.0.0",
    )
    return SummaryResult(
        mini_title="Mini",
        brief_summary="Brief",
        detailed_summary=[],
        closing_remarks="Done",
        tags=["foo"],
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Phase 3.1 grep gate
# ---------------------------------------------------------------------------


def test_writer_module_does_not_import_supabase_kg():
    """File-level grep: `supabase_kg` must not appear in the refactored writer."""
    src = Path(
        "website/features/summarization_engine/writers/supabase.py"
    ).read_text(encoding="utf-8")
    assert "supabase_kg" not in src, (
        "writers/supabase.py must not reference supabase_kg after v2 purge"
    )
    assert "from website.core.supabase_v2" in src, (
        "writers/supabase.py must use the v2 client module"
    )


# ---------------------------------------------------------------------------
# Signature preservation
# ---------------------------------------------------------------------------


def test_write_signature_preserved():
    """``BaseWriter.write(result, *, user_id)`` must match exactly so existing
    call sites in summarization_engine/api/routes.py keep working."""
    base_sig = inspect.signature(BaseWriter.write)
    impl_sig = inspect.signature(SupabaseWriter.write)
    assert list(base_sig.parameters.keys()) == list(impl_sig.parameters.keys())
    user_id_param = impl_sig.parameters["user_id"]
    assert user_id_param.kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Behaviour: writes land in content.workspace_zettels.ai_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_persists_to_canonical_and_overlay():
    """The writer must call ContentRepository.upsert_canonical_zettel with
    a workspace overlay whose ``ai_summary`` carries the canonical summary
    envelope and ``ai_summary_engine_version`` is stamped."""
    fake_workspace_id = uuid4()
    fake_canonical_id = uuid4()
    fake_overlay_id = uuid4()

    content_repo = MagicMock()
    content_repo.upsert_canonical_zettel.return_value = CanonicalUpsertResult(
        canonical_zettel_id=fake_canonical_id,
        workspace_zettel_id=fake_overlay_id,
        was_new=True,
    )
    core_repo = MagicMock()
    core_repo.get_default_workspace_id.return_value = fake_workspace_id

    writer = SupabaseWriter(repository=content_repo, core_repo=core_repo)
    profile_id = uuid4()
    result = _make_summary()

    outcome = await writer.write(result, user_id=profile_id)

    # The mock got called exactly once.
    assert content_repo.upsert_canonical_zettel.call_count == 1
    args, kwargs = content_repo.upsert_canonical_zettel.call_args
    zettel = args[0]
    overlay = kwargs["workspace"]

    # Canonical row carries the v2-shape fields.
    assert zettel.normalized_url == result.metadata.url
    assert zettel.source_type == "web"
    assert zettel.body_md  # the canonical envelope blob

    # Workspace overlay carries ai_summary text (NOT a kg_nodes.summary jsonb).
    assert overlay.workspace_id == fake_workspace_id
    assert overlay.ai_summary  # populated text
    assert overlay.ai_summary_engine_version == result.metadata.engine_version
    assert overlay.added_via == "website"

    # Workspace_id is resolved from core repo, exactly once (cached after).
    core_repo.get_default_workspace_id.assert_called_once_with(profile_id)

    assert outcome["status"] == "created"
    assert outcome["node_id"] == str(fake_canonical_id)
    assert outcome["workspace_zettel_id"] == str(fake_overlay_id)


@pytest.mark.asyncio
async def test_write_marks_skipped_on_duplicate():
    fake_workspace_id = uuid4()
    fake_canonical_id = uuid4()
    fake_overlay_id = uuid4()

    content_repo = MagicMock()
    content_repo.upsert_canonical_zettel.return_value = CanonicalUpsertResult(
        canonical_zettel_id=fake_canonical_id,
        workspace_zettel_id=fake_overlay_id,
        was_new=False,
    )
    core_repo = MagicMock()
    core_repo.get_default_workspace_id.return_value = fake_workspace_id

    writer = SupabaseWriter(repository=content_repo, core_repo=core_repo)
    outcome = await writer.write(_make_summary(), user_id=uuid4())

    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "duplicate_url"


@pytest.mark.asyncio
async def test_write_raises_when_profile_has_no_workspace():
    """No default workspace -> _UnknownWorkspaceError. We must NOT silently
    insert with NULL workspace_id (would violate the v2 NOT NULL constraint
    anyway)."""
    content_repo = MagicMock()
    core_repo = MagicMock()
    core_repo.get_default_workspace_id.return_value = None

    writer = SupabaseWriter(repository=content_repo, core_repo=core_repo)
    with pytest.raises(RuntimeError, match="no default workspace"):
        await writer.write(_make_summary(), user_id=uuid4())
    content_repo.upsert_canonical_zettel.assert_not_called()


def test_content_hash_bytes_changes_with_url_or_summary():
    blob = _encode_summary_blob(_make_summary())
    a = _content_hash_bytes(url="https://a.example/x", summary_blob=blob)
    b = _content_hash_bytes(url="https://b.example/x", summary_blob=blob)
    c = _content_hash_bytes(url="https://a.example/x", summary_blob=blob + " ")
    assert a != b
    assert a != c
    assert isinstance(a, bytes)
    assert len(a) == 32  # sha256
