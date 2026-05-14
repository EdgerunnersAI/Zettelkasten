"""Persistence DTO and outcome breakdown for Add Zettel."""
from __future__ import annotations

import pytest


def test_persistence_outcome_carries_file_saved_and_supabase_saved():
    """PersistenceOutcome must track both stores independently."""
    from website.core.persist import PersistenceOutcome

    o = PersistenceOutcome(result={"title": "x"}, file_saved=True, supabase_saved=False)
    assert o.file_saved is True
    assert o.supabase_saved is False


def test_persistence_outcome_defaults_to_false():
    """Defaults preserve safety — caller must explicitly mark each store as saved."""
    from website.core.persist import PersistenceOutcome

    o = PersistenceOutcome(result={"title": "x"})
    assert o.file_saved is False
    assert o.supabase_saved is False


def test_persistence_outcome_supabase_only():
    """Supabase-only write: file_saved False, supabase_saved True."""
    from website.core.persist import PersistenceOutcome

    o = PersistenceOutcome(result={"title": "x"}, file_saved=False, supabase_saved=True)
    assert o.file_saved is False
    assert o.supabase_saved is True


def test_add_zettel_persistence_dto_includes_store_breakdown():
    """/api/zettels/add returns persistence:{file_store, supabase} in its body."""
    from website.core.persist import PersistenceOutcome
    from website.api.module_runners.summarization import persistence_dto

    fake_outcome = PersistenceOutcome(
        result={"title": "Test", "source_url": "https://example.com"},
        file_saved=True,
        supabase_saved=False,
    )

    result = persistence_dto(True, fake_outcome).model_dump(mode="json")
    assert result == {
        "requested": True,
        "persisted": True,
        "file_store": True,
        "supabase": False,
        "duplicate": False,
    }


@pytest.mark.asyncio
async def test_v2_persist_returns_workspace_zettel_id():
    """The facade must expose the workspace row UUID, not the canonical UUID."""
    from datetime import date
    from uuid import uuid4

    from website.core.persist import _persist_supabase_v2_zettel
    from website.core.supabase_v2.models import CanonicalUpsertResult

    canonical_id = uuid4()
    workspace_zettel_id = uuid4()
    workspace_id = uuid4()

    class _Repo:
        def upsert_canonical_zettel(self, *_args, **_kwargs):
            return CanonicalUpsertResult(
                canonical_zettel_id=canonical_id,
                workspace_zettel_id=workspace_zettel_id,
                was_new=False,
            )

    returned_id, saved, duplicate = await _persist_supabase_v2_zettel(
        payload={
            "source_url": "https://example.com",
            "source_type": "web",
            "title": "Title",
            "summary": "Summary",
            "tags": [],
            "metadata": {},
        },
        repo=_Repo(),
        workspace_id=workspace_id,
        captured_on=date.today(),
        detailed_summary="Summary",
    )

    assert returned_id == str(workspace_zettel_id)
    assert returned_id != str(canonical_id)
    assert saved is True
    assert duplicate is True
