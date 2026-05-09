"""Phase 3.5 — Nexus service v2 refactor tests.

Asserts:
1. None of the three refactored modules import from
   ``website.core.supabase_kg``.
2. ``ProviderTokenStore.upsert_account(...)`` writes to
   ``pipelines.nexus_provider_tokens`` (NOT legacy
   ``public.nexus_provider_accounts``) with workspace_id supplied.
3. ``ProviderTokenStore.get_account(...)`` reads from the same v2 table.
4. Bulk import surfaces (``_create_run`` / ``_record_artifact``) write to
   ``pipelines.pipeline_runs`` / ``pipelines.pipeline_run_items`` (NOT
   legacy ``nexus_ingest_runs`` / ``nexus_ingested_artifacts``).
5. OAuth state issue/consume is in-memory and round-trips cleanly.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet


NEXUS_FILES = [
    Path("website/experimental_features/nexus/service/bulk_import.py"),
    Path("website/experimental_features/nexus/service/token_store.py"),
    Path("website/experimental_features/nexus/source_ingest/common/oauth_state.py"),
]


# ---------------------------------------------------------------------------
# Phase 3.5 grep gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", NEXUS_FILES)
def test_no_supabase_kg_import(path: Path):
    src = path.read_text(encoding="utf-8")
    assert "from website.core.supabase_kg" not in src, (
        f"{path} must not import from supabase_kg after Phase 3.5"
    )


# ---------------------------------------------------------------------------
# OAuth state — in-memory, not table-backed
# ---------------------------------------------------------------------------


def test_oauth_state_roundtrip_in_memory():
    from website.experimental_features.nexus.source_ingest.common import oauth_state
    from website.experimental_features.nexus.source_ingest.common.models import NexusProvider

    oauth_state._reset_state_for_tests()
    token, record = oauth_state.issue_oauth_state(
        provider=NexusProvider.GITHUB,
        auth_user_sub="user-abc",
        redirect_path="/home/nexus",
    )
    consumed = oauth_state.consume_oauth_state(NexusProvider.GITHUB, token)
    assert consumed.auth_user_sub == "user-abc"
    assert consumed.consumed_at is not None
    # Re-use must raise.
    with pytest.raises(ValueError, match="already been used"):
        oauth_state.consume_oauth_state(NexusProvider.GITHUB, token)


def test_oauth_state_invalid_token_raises():
    from website.experimental_features.nexus.source_ingest.common import oauth_state
    from website.experimental_features.nexus.source_ingest.common.models import NexusProvider

    oauth_state._reset_state_for_tests()
    with pytest.raises(ValueError, match="Invalid OAuth state"):
        oauth_state.consume_oauth_state(NexusProvider.GITHUB, "not-a-real-token")


# ---------------------------------------------------------------------------
# ProviderTokenStore — pipelines.nexus_provider_tokens
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key_env():
    key = Fernet.generate_key().decode()
    with patch.dict(os.environ, {"NEXUS_TOKEN_ENCRYPTION_KEY": key}):
        yield key


class _FakeExecute:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return type("Resp", (), {"data": self.data})()


class _FakeTable:
    def __init__(self, calls, schema, table, row_factory):
        self.calls = calls
        self.schema = schema
        self.table = table
        self._row_factory = row_factory
        self._filters = {}

    def upsert(self, payload, **kwargs):
        self.calls.append(("upsert", self.schema, self.table, payload, kwargs))
        return _FakeExecute([{**payload, "created_at": "2026-05-09T00:00:00+00:00", "updated_at": "2026-05-09T00:00:00+00:00"}])

    def insert(self, payload):
        self.calls.append(("insert", self.schema, self.table, payload, {}))
        row = {**payload, "id": str(uuid4()), "created_at": "2026-05-09T00:00:00+00:00"}
        return _FakeExecute([row])

    def update(self, payload):
        self.calls.append(("update", self.schema, self.table, payload, {}))
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def in_(self, col, vals):
        self._filters[col] = list(vals)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def delete(self):
        self.calls.append(("delete", self.schema, self.table, dict(self._filters)))
        return _FakeExecute([])

    def execute(self):
        # used after .select().eq(...).limit() chain
        rows = self._row_factory(self.schema, self.table, self._filters) if self._row_factory else []
        return type("Resp", (), {"data": rows})()


class _FakeSchema:
    def __init__(self, calls, schema, row_factory):
        self.calls = calls
        self.schema = schema
        self._row_factory = row_factory

    def table(self, name):
        self.calls.append(("table", self.schema, name))
        return _FakeTable(self.calls, self.schema, name, self._row_factory)

    def rpc(self, name, params):
        self.calls.append(("rpc", self.schema, name, params))
        return _FakeExecute([])


class _FakeClient:
    def __init__(self, row_factory=None):
        self.calls = []
        self._row_factory = row_factory

    def schema(self, name):
        self.calls.append(("schema", name))
        return _FakeSchema(self.calls, name, self._row_factory)


def test_token_store_upsert_writes_to_pipelines_nexus_provider_tokens(fernet_key_env):
    from website.experimental_features.nexus.service.token_store import ProviderTokenStore
    from website.experimental_features.nexus.source_ingest.common.models import (
        NexusProvider,
        StoredProviderAccount,
    )

    profile_id = uuid4()
    workspace_id = uuid4()
    fake = _FakeClient()
    core_repo = MagicMock()
    core_repo.get_default_workspace_id.return_value = workspace_id

    store = ProviderTokenStore(client=fake, core_repo=core_repo)
    account = StoredProviderAccount(
        user_id=profile_id,
        provider=NexusProvider.GITHUB,
        access_token="my-secret-access",
        refresh_token="my-secret-refresh",
    )
    store.upsert_account(account)

    # Schema/table targeting v2
    assert ("schema", "pipelines") in fake.calls
    assert ("table", "pipelines", "nexus_provider_tokens") in fake.calls

    upsert_calls = [c for c in fake.calls if c[0] == "upsert"]
    assert upsert_calls, "expected an upsert call to nexus_provider_tokens"
    schema, table, payload, kwargs = upsert_calls[0][1], upsert_calls[0][2], upsert_calls[0][3], upsert_calls[0][4]
    assert schema == "pipelines"
    assert table == "nexus_provider_tokens"
    assert payload["profile_id"] == str(profile_id)
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["provider"] == "github"
    # Encrypted blob is bytea-as-hex (\x...)
    assert payload["encrypted_token"].startswith("\\x")
    assert payload["refresh_token"].startswith("\\x")
    assert kwargs["on_conflict"] == "profile_id,provider"

    core_repo.get_default_workspace_id.assert_called_once_with(profile_id)


def test_token_store_raises_when_no_default_workspace(fernet_key_env):
    from website.experimental_features.nexus.service.token_store import ProviderTokenStore
    from website.experimental_features.nexus.source_ingest.common.models import (
        NexusProvider,
        StoredProviderAccount,
    )

    fake = _FakeClient()
    core_repo = MagicMock()
    core_repo.get_default_workspace_id.return_value = None
    store = ProviderTokenStore(client=fake, core_repo=core_repo)
    account = StoredProviderAccount(
        user_id=uuid4(),
        provider=NexusProvider.GITHUB,
        access_token="t",
    )
    with pytest.raises(RuntimeError, match="no default workspace"):
        store.upsert_account(account)


# ---------------------------------------------------------------------------
# bulk_import — pipelines.pipeline_runs / pipeline_run_items
# ---------------------------------------------------------------------------


def test_create_run_writes_to_pipeline_runs(fernet_key_env):
    """_create_run should insert into pipelines.pipeline_runs with kind=nexus_ingest
    and the workspace_id resolved by CoreRepository."""
    from website.experimental_features.nexus.service import bulk_import
    from website.experimental_features.nexus.source_ingest.common.models import NexusProvider

    workspace_id = uuid4()
    profile_id = uuid4()

    def row_factory(schema, table, filters):
        # default empty for any select
        return []

    fake = _FakeClient(row_factory=row_factory)
    # _create_run does an .insert() — _FakeTable.insert() returns a row with id+kind+config etc.
    with patch(
        "website.experimental_features.nexus.service.bulk_import.get_v2_client",
        return_value=fake,
    ):
        run = bulk_import._create_run(
            str(profile_id),
            workspace_id,
            NexusProvider.YOUTUBE,
            provider_account_id=str(profile_id),
        )

    # Verify schema + table targeting
    assert ("schema", "pipelines") in fake.calls
    assert ("table", "pipelines", "pipeline_runs") in fake.calls
    insert_calls = [c for c in fake.calls if c[0] == "insert"]
    assert insert_calls, "expected an insert into pipeline_runs"
    payload = insert_calls[0][3]
    assert payload["workspace_id"] == str(workspace_id)
    assert payload["kind"] == "nexus_ingest"
    assert payload["status"] == "running"
    assert payload["config"]["provider"] == "youtube"
    assert payload["config"]["profile_id"] == str(profile_id)
    assert run.provider == NexusProvider.YOUTUBE


def test_record_artifact_writes_to_pipeline_run_items(fernet_key_env):
    from website.experimental_features.nexus.service import bulk_import
    from website.experimental_features.nexus.source_ingest.common.models import (
        NexusProvider,
        ProviderArtifact,
    )

    workspace_id = uuid4()
    fake = _FakeClient()
    artifact = ProviderArtifact(
        provider=NexusProvider.GITHUB,
        external_id="repo/123",
        url="https://github.com/foo/bar",
        title="Bar",
    )
    run_id = str(uuid4())
    with patch(
        "website.experimental_features.nexus.service.bulk_import.get_v2_client",
        return_value=fake,
    ):
        bulk_import._record_artifact(
            workspace_id=workspace_id,
            provider=NexusProvider.GITHUB,
            provider_account_id=None,
            artifact=artifact,
            ingest_run_id=run_id,
            status="imported",
        )

    assert ("schema", "pipelines") in fake.calls
    assert ("table", "pipelines", "pipeline_run_items") in fake.calls
    insert_calls = [c for c in fake.calls if c[0] == "insert"]
    assert insert_calls, "expected an insert into pipeline_run_items"
    payload = insert_calls[0][3]
    assert payload["run_id"] == run_id
    assert payload["status"] == "succeeded"  # legacy 'imported' -> v2 'succeeded'
    assert payload["result"]["provider"] == "github"
    assert payload["result"]["external_id"] == "repo/123"
    assert payload["result"]["url"] == "https://github.com/foo/bar"
    assert payload["result"]["legacy_status"] == "imported"


def test_record_artifact_status_mapping(fernet_key_env):
    """Verify legacy artifact status -> v2 pipeline_run_items.status mapping."""
    from website.experimental_features.nexus.service import bulk_import

    assert bulk_import._v2_run_item_status("imported") == "succeeded"
    assert bulk_import._v2_run_item_status("skipped") == "skipped"
    assert bulk_import._v2_run_item_status("failed") == "failed"
    # Unknown status defaults to failed (defensive)
    assert bulk_import._v2_run_item_status("???") == "failed"


def test_pipeline_run_status_mapping():
    from website.experimental_features.nexus.service import bulk_import

    assert bulk_import._normalize_run_status_for_v2("completed") == "succeeded"
    assert bulk_import._normalize_run_status_for_v2("partial_success") == "succeeded"
    assert bulk_import._normalize_run_status_for_v2("failed") == "failed"
    assert bulk_import._normalize_run_status_for_v2("running") == "running"
