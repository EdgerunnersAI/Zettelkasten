from __future__ import annotations

from uuid import UUID

import pytest

from website.core import db_version
from website.core import persist
from website.core.supabase_v2.models import CanonicalUpsertResult


def test_db_schema_version_requires_v2_credentials(monkeypatch) -> None:
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.delenv("SUPABASE_V2_URL", raising=False)
    monkeypatch.delenv("SUPABASE_V2_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_V2_ANON_KEY", raising=False)

    assert db_version.get_db_schema_version() == "v2"
    assert db_version.use_supabase_v2() is False


class _FakeV2Repo:
    def __init__(self) -> None:
        self.calls = []

    def upsert_canonical_zettel(self, zettel, *, workspace=None, chunks=None):
        self.calls.append((zettel, workspace, chunks))
        return CanonicalUpsertResult(
            canonical_zettel_id=UUID("00000000-0000-0000-0000-000000000111"),
            workspace_zettel_id=UUID("00000000-0000-0000-0000-000000000222"),
            was_new=True,
        )


@pytest.mark.asyncio
async def test_persist_routes_to_v2_when_scope_available(monkeypatch) -> None:
    repo = _FakeV2Repo()
    monkeypatch.setattr(
        persist,
        "get_supabase_v2_scope",
        lambda user_sub: (
            repo,
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
        ),
    )
    monkeypatch.setattr(persist, "get_supabase_scope", lambda user_sub=None: None)
    monkeypatch.setattr(persist, "_file_graph_contains_url", lambda url: False)
    monkeypatch.setattr(persist, "_persist_file_node", lambda payload, skip_duplicate: None)

    outcome = await persist.persist_summarized_result(
        {
            "title": "Example",
            "source_type": "web",
            "source_url": "https://example.com",
            "summary": "Detailed summary.",
            "tags": ["Test"],
        },
        user_sub="00000000-0000-0000-0000-000000000001",
    )

    assert outcome.supabase_saved is True
    assert outcome.supabase_node_id == "00000000-0000-0000-0000-000000000111"
    zettel, workspace, chunks = repo.calls[0]
    assert zettel.normalized_url == "https://example.com"
    assert workspace.workspace_id == UUID("00000000-0000-0000-0000-000000000002")
    assert chunks and chunks[0].chunk_idx == 0

