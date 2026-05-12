"""iter-12 β SECONDARY: /api/summarize exposes persistence breakdown."""
from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


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


@pytest.mark.asyncio
async def test_summarize_response_includes_persistence_field():
    """/api/summarize handler returns persistence:{file_store, supabase} in its body."""
    from website.core.persist import PersistenceOutcome

    fake_outcome = PersistenceOutcome(
        result={"title": "Test", "source_url": "https://example.com"},
        file_saved=True,
        supabase_saved=False,
    )

    # Direct call to the underlying persist_summarized_result path is mocked;
    # we verify the route handler builds the correct response shape.
    with patch(
        "website.api.routes.persist_summarized_result",
        new_callable=AsyncMock,
        return_value=fake_outcome,
    ), patch(
        "website.api.routes.summarize_url",
        new_callable=AsyncMock,
        return_value={"title": "Test", "source_url": "https://example.com"},
    ), patch(
        "website.api.routes.require_entitlement",
        new_callable=AsyncMock,
    ), patch(
        "website.api.routes.consume_entitlement",
        new_callable=AsyncMock,
    ):
        from website.api.routes import summarize

        # Build a minimal mock request body and user
        body = MagicMock()
        body.url = "https://example.com"
        body.client_action_id = None

        result = await summarize(body=body, request=MagicMock(), user=None)

        # Core assertion: persistence key present with correct shape
        assert "persistence" in result, f"'persistence' key missing from response: {list(result.keys())}"
        assert result["persistence"] == {"file_store": True, "supabase": False}
