from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from website.experimental_features.nexus.service.bulk_import import run_provider_import
from website.experimental_features.nexus.source_ingest.common.models import (
    ImportRequest,
    NexusProvider,
    ProviderArtifact,
    StoredProviderAccount,
)

# Phase 3.5: bulk_import was rebased from public.nexus_ingest_runs +
# public.nexus_ingested_artifacts (v1) onto pipelines.pipeline_runs +
# pipelines.pipeline_run_items. The patches below target legacy v1 helper
# names and shapes that no longer exist; new v2 coverage lives in
# tests/unit/experimental_features/test_nexus_v2.py.
pytestmark = pytest.mark.skip(
    reason="v1 nexus bulk_import retired in Phase 3.5; replaced by tests/unit/experimental_features/test_nexus_v2.py"
)


async def test_run_provider_import_handles_skip_import_and_failure() -> None:
    user_id = uuid4()
    account = StoredProviderAccount(
        user_id=user_id,
        provider=NexusProvider.REDDIT,
        account_id="acct-1",
        account_username="reader",
        access_token="token",
        metadata={"remember_connection": True},
    )
    artifacts = [
        ProviderArtifact(
            provider=NexusProvider.REDDIT,
            external_id="skip-me",
            url="https://example.com/skip",
            title="Skip",
        ),
        ProviderArtifact(
            provider=NexusProvider.REDDIT,
            external_id="import-me",
            url="https://example.com/import",
            title="Import",
        ),
        ProviderArtifact(
            provider=NexusProvider.REDDIT,
            external_id="fail-me",
            url="https://example.com/fail",
            title="Fail",
        ),
    ]
    run = SimpleNamespace(id=uuid4())
    updated_run = SimpleNamespace(id=run.id, status="partial_success")
    persistence = SimpleNamespace(
        supabase_duplicate=False,
        supabase_node_id="rd-import",
        file_node_id=None,
    )

    async def fake_summarize(url: str) -> dict[str, str]:
        if url.endswith("/fail"):
            raise RuntimeError("boom")
        return {
            "title": "Imported",
            "summary": "Summary",
            "brief_summary": "Summary",
            "source_type": "reddit",
            "source_url": url,
            "tags": ["reddit"],
        }

    with (
        patch(
            "website.experimental_features.nexus.service.bulk_import.get_supabase_scope",
            return_value=(object(), str(user_id)),
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.get_provider_account",
            return_value=account,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._create_run",
            return_value=run,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._invoke_ingest_handler",
            return_value=(artifacts, {"source": "test"}),
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._artifact_exists",
            side_effect=lambda kg_user_id, provider, external_id: external_id == "skip-me",
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.summarize_artifact_url",
            side_effect=fake_summarize,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.persist_summarized_result",
            return_value=persistence,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._update_run",
            return_value=updated_run,
        ) as update_run_mock,
        patch(
            "website.experimental_features.nexus.service.bulk_import._record_artifact",
        ) as record_artifact_mock,
        patch(
            "website.experimental_features.nexus.service.bulk_import._touch_account_imported_at",
        ),
    ):
        result = await run_provider_import(
            auth_user_sub="auth-user",
            provider=NexusProvider.REDDIT,
            request=ImportRequest(limit=10),
        )

    assert result.imported_count == 1
    assert result.skipped_count == 1
    assert result.failed_count == 1
    assert [item["status"] for item in result.results] == ["skipped", "imported", "failed"]
    assert record_artifact_mock.call_count == 3
    assert update_run_mock.call_args.kwargs["status"] == "partial_success"


async def test_run_provider_import_forgets_credentials_when_requested() -> None:
    user_id = uuid4()
    account = StoredProviderAccount(
        user_id=user_id,
        provider=NexusProvider.GITHUB,
        account_id="acct-2",
        account_username="builder",
        access_token="token",
        metadata={"remember_connection": False},
    )
    artifact = ProviderArtifact(
        provider=NexusProvider.GITHUB,
        external_id="gh-1",
        url="https://github.com/openai/openai",
        title="Repo",
    )
    run = SimpleNamespace(id=uuid4())
    persistence = SimpleNamespace(
        supabase_duplicate=False,
        supabase_node_id="gh-openai",
        file_node_id=None,
    )

    with (
        patch(
            "website.experimental_features.nexus.service.bulk_import.get_supabase_scope",
            return_value=(object(), str(user_id)),
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.get_provider_account",
            return_value=account,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._create_run",
            return_value=run,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._invoke_ingest_handler",
            return_value=([artifact], {}),
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._artifact_exists",
            return_value=False,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.summarize_artifact_url",
            return_value={
                "title": "Repo",
                "summary": "Summary",
                "brief_summary": "Summary",
                "source_type": "github",
                "source_url": artifact.url,
                "tags": ["github"],
            },
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.persist_summarized_result",
            return_value=persistence,
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._update_run",
            return_value=SimpleNamespace(id=run.id, status="completed", completed_at=datetime.now(timezone.utc)),
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._record_artifact",
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import._touch_account_imported_at",
        ),
        patch(
            "website.experimental_features.nexus.service.bulk_import.disconnect_provider_account",
            return_value=True,
        ) as disconnect_mock,
    ):
        result = await run_provider_import(
            auth_user_sub="auth-user",
            provider=NexusProvider.GITHUB,
            request=ImportRequest(limit=5, remember_connection=False),
        )

    assert result.credentials_forgotten is True
    disconnect_mock.assert_called_once_with(str(user_id), NexusProvider.GITHUB)
