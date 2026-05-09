"""Bulk import orchestration for Nexus provider ingestion.

Phase 3.5 of the v2 purge: rebases off the legacy
``public.nexus_ingest_runs`` and ``public.nexus_ingested_artifacts``
tables onto ``pipelines.pipeline_runs`` (with ``kind='nexus_ingest'``)
and ``pipelines.pipeline_run_items``. The flat per-artifact fields
(external_id, url, title, description, source_type, metadata) move into
the ``result`` jsonb column on ``pipeline_run_items`` since the v2
schema kept the run-items table generic across all pipeline kinds.

Workspace_id is NOT NULL on ``pipelines.pipeline_runs``, so every run
insert resolves the profile's default workspace via
``CoreRepository.get_default_workspace_id`` before writing.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from website.core.pipeline import summarize_url
from website.core.persist import (
    PersistenceOutcome,
    get_supabase_scope,
    persist_summarized_result,
)
from website.core.supabase_v2.client import get_v2_client, is_v2_configured
from website.core.supabase_v2.repositories.core_repository import CoreRepository
from website.experimental_features.nexus.service.token_store import ProviderTokenStore
from website.experimental_features.nexus.source_ingest.common.models import (
    ImportRequest,
    ImportRun,
    NexusProvider,
    ProviderArtifact,
    StoredProviderAccount,
)

logger = logging.getLogger("website.experimental_features.nexus.bulk_import")

_PIPELINE_KIND = "nexus_ingest"
_RUNS_SCHEMA = "pipelines"
_RUNS_TABLE = "pipeline_runs"
_RUN_ITEMS_TABLE = "pipeline_run_items"


@dataclass(slots=True)
class BulkImportResult:
    provider: NexusProvider
    run: ImportRun | None
    total_artifacts: int
    imported_count: int
    skipped_count: int
    failed_count: int
    results: list[dict[str, Any]]
    credentials_forgotten: bool = False


def _is_configured() -> bool:
    return is_v2_configured()


def _resolve_workspace_id(profile_id: UUID) -> UUID:
    workspace_id = CoreRepository().get_default_workspace_id(profile_id)
    if workspace_id is None:
        raise RuntimeError(
            f"profile {profile_id} has no default workspace; cannot run Nexus import"
        )
    return workspace_id


def get_provider_account(user_id: str, provider: NexusProvider) -> StoredProviderAccount | None:
    """Return a stored provider account for a profile."""

    if not _is_configured():
        return None

    try:
        user_uuid = UUID(str(user_id))
    except (TypeError, ValueError):
        logger.warning("Invalid profile id passed to get_provider_account: %s", user_id)
        return None

    return ProviderTokenStore().get_account(user_uuid, provider)


def list_provider_accounts(user_id: str) -> dict[NexusProvider, StoredProviderAccount]:
    """Return all stored provider accounts for a profile."""

    if not _is_configured():
        return {}

    try:
        user_uuid = UUID(str(user_id))
    except (TypeError, ValueError):
        logger.warning("Invalid profile id passed to list_provider_accounts: %s", user_id)
        return {}

    decrypted_accounts = ProviderTokenStore().list_accounts(user_uuid)
    accounts: dict[NexusProvider, StoredProviderAccount] = {}
    for account in decrypted_accounts:
        accounts[account.provider] = account
    return accounts


def upsert_provider_account(account: StoredProviderAccount) -> StoredProviderAccount:
    """Insert or update a Nexus provider account."""
    return ProviderTokenStore().upsert_account(account)


def disconnect_provider_account(user_id: str, provider: NexusProvider) -> bool:
    """Delete a stored provider account."""

    if not _is_configured():
        return False

    try:
        user_uuid = UUID(str(user_id))
    except (TypeError, ValueError):
        logger.warning("Invalid profile id passed to disconnect_provider_account: %s", user_id)
        return False

    return ProviderTokenStore().delete_account(user_uuid, provider)


def list_import_runs(user_id: str, limit: int = 20) -> list[ImportRun]:
    """Return recent Nexus ingest runs for a profile.

    Reads ``pipelines.pipeline_runs`` filtered to ``kind='nexus_ingest'``
    and the user's default workspace.
    """

    if not _is_configured():
        return []

    try:
        profile_uuid = UUID(str(user_id))
    except (TypeError, ValueError):
        logger.warning("Invalid profile id passed to list_import_runs: %s", user_id)
        return []
    try:
        workspace_id = _resolve_workspace_id(profile_uuid)
    except RuntimeError as exc:
        logger.warning("list_import_runs: %s", exc)
        return []

    client = get_v2_client()
    response = (
        client.schema(_RUNS_SCHEMA)
        .table(_RUNS_TABLE)
        .select("*")
        .eq("workspace_id", str(workspace_id))
        .eq("kind", _PIPELINE_KIND)
        .order("created_at", desc=True)
        .limit(max(1, min(limit, 100)))
        .execute()
    )
    runs: list[ImportRun] = []
    for row in response.data or []:
        try:
            runs.append(_pipeline_run_row_to_import_run(row))
        except Exception as exc:
            logger.warning("Skipping invalid pipeline_run row: %s", exc)
    return runs


def _pipeline_run_row_to_import_run(row: dict[str, Any]) -> ImportRun:
    """Adapt a pipelines.pipeline_runs row to the legacy ImportRun shape.

    Per-run counters (total_artifacts/imported/skipped/failed) and the
    legacy ``provider`` field live in the ``metrics`` jsonb column on
    pipeline_runs; the run's ``status`` is normalized below.
    """
    metrics = row.get("metrics") or {}
    config = row.get("config") or {}
    provider_value = config.get("provider") or metrics.get("provider")
    if not provider_value:
        raise ValueError("pipeline_runs row is missing config.provider")

    status_map = {
        "queued": "running",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "failed",
    }
    raw_status = row.get("status", "running")
    failed = int(metrics.get("failed_count") or 0)
    imported = int(metrics.get("imported_count") or 0)
    skipped = int(metrics.get("skipped_count") or 0)
    if raw_status == "succeeded" and failed and (imported or skipped):
        normalized_status = "partial_success"
    else:
        normalized_status = status_map.get(raw_status, raw_status)

    return ImportRun(
        id=UUID(str(row["id"])),
        provider=NexusProvider(provider_value),
        status=normalized_status,
        total_artifacts=int(metrics.get("total_artifacts") or 0),
        imported_count=imported,
        skipped_count=skipped,
        failed_count=failed,
        started_at=row.get("started_at"),
        completed_at=row.get("finished_at"),
        error_message=row.get("error"),
        metadata=metrics.get("metadata") or {},
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_provider_module(provider: NexusProvider, module_name: str):
    module_path = f"website.experimental_features.nexus.source_ingest.{provider.value}.{module_name}"
    return importlib.import_module(module_path)


def provider_handler_available(provider: NexusProvider, handler_type: str) -> bool:
    try:
        module = _load_provider_module(provider, handler_type)
    except Exception:
        return False

    return _resolve_callable(
        module,
        _oauth_handler_names() if handler_type == "oauth" else ("ingest_artifacts", "run_import", "import_artifacts"),
    ) is not None


def _oauth_handler_names() -> tuple[str, ...]:
    return (
        "start_oauth",
        "begin_oauth",
        "start_connect",
        "build_authorization_url",
        "handle_callback",
        "oauth_callback",
        "complete_oauth",
        "exchange_code_for_tokens",
    )


def _resolve_callable(module: Any, names: tuple[str, ...]) -> Any | None:
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def _call_with_supported_kwargs(func: Any, **candidate_kwargs: Any) -> Any:
    signature = inspect.signature(func)
    accepted_kwargs = {}
    has_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    for name, value in candidate_kwargs.items():
        if value is None:
            continue
        if has_var_keyword or name in signature.parameters:
            accepted_kwargs[name] = value

    return func(**accepted_kwargs)


def _normalize_artifacts(raw_result: Any, provider: NexusProvider) -> tuple[list[ProviderArtifact], dict[str, Any]]:
    metadata: dict[str, Any] = {}
    raw_artifacts = raw_result

    if hasattr(raw_result, "model_dump"):
        raw_result = raw_result.model_dump()

    if isinstance(raw_result, dict):
        metadata = {
            key: value
            for key, value in raw_result.items()
            if key != "artifacts"
        }
        raw_artifacts = raw_result.get("artifacts", [])

    if raw_artifacts is None:
        return [], metadata

    artifacts: list[ProviderArtifact] = []
    for item in raw_artifacts:
        try:
            if isinstance(item, ProviderArtifact):
                artifact = item
            elif hasattr(item, "model_dump"):
                artifact = ProviderArtifact.model_validate(item.model_dump())
            else:
                payload = dict(item)
                payload.setdefault("provider", provider.value)
                artifact = ProviderArtifact.model_validate(payload)
            artifacts.append(artifact)
        except Exception as exc:
            logger.warning("Skipping invalid %s artifact: %s", provider.value, exc)

    return artifacts, metadata


async def summarize_artifact_url(url: str) -> dict[str, Any]:
    """Summarize an artifact URL using the website-native summarization pipeline."""

    return await summarize_url(url)


def _create_run(
    profile_id: str,
    workspace_id: UUID,
    provider: NexusProvider,
    *,
    provider_account_id: str | None = None,
) -> ImportRun:
    client = get_v2_client()
    started_at_iso = _utcnow().isoformat()
    payload = {
        "workspace_id": str(workspace_id),
        "kind": _PIPELINE_KIND,
        "status": "running",
        "config": {
            "provider": provider.value,
            "profile_id": profile_id,
            "provider_account_id": provider_account_id,
        },
        "metrics": {},
        "started_at": started_at_iso,
    }
    response = (
        client.schema(_RUNS_SCHEMA)
        .table(_RUNS_TABLE)
        .insert(payload)
        .execute()
    )
    if not response.data:
        raise RuntimeError("Failed to create ingest run")
    return _pipeline_run_row_to_import_run(response.data[0])


def _update_run(run_id: str, **fields: Any) -> ImportRun:
    """Update a pipeline_runs row.

    Translates the legacy keyword args (status, total_artifacts, imported_count,
    skipped_count, failed_count, completed_at, error_message, metadata) into
    the v2 column shape (status normalized, counters folded into metrics jsonb,
    completed_at -> finished_at, error_message -> error).
    """
    client = get_v2_client()

    # Pull current metrics so we can merge counter updates without a SELECT race
    # (single-process, single-flight per run, no contention).
    payload: dict[str, Any] = {}
    metrics_keys = {"total_artifacts", "imported_count", "skipped_count", "failed_count", "metadata"}
    metrics_updates: dict[str, Any] = {}

    for key, value in fields.items():
        if key in metrics_keys:
            metrics_updates[key] = value
            continue
        if key == "completed_at":
            payload["finished_at"] = value
            continue
        if key == "error_message":
            payload["error"] = value
            continue
        if key == "status":
            payload["status"] = _normalize_run_status_for_v2(value)
            continue
        payload[key] = value

    if metrics_updates:
        existing = (
            client.schema(_RUNS_SCHEMA)
            .table(_RUNS_TABLE)
            .select("metrics")
            .eq("id", run_id)
            .limit(1)
            .execute()
        )
        prior = (existing.data[0]["metrics"] if existing.data else {}) or {}
        merged = {**prior, **{k: v for k, v in metrics_updates.items() if v is not None}}
        payload["metrics"] = merged

    response = (
        client.schema(_RUNS_SCHEMA)
        .table(_RUNS_TABLE)
        .update(payload)
        .eq("id", run_id)
        .execute()
    )
    if not response.data:
        raise RuntimeError("Failed to update ingest run")
    return _pipeline_run_row_to_import_run(response.data[0])


def _normalize_run_status_for_v2(legacy_status: str) -> str:
    """Map the legacy ImportRun statuses to pipeline_runs CHECK constraint."""
    mapping = {
        "running": "running",
        "completed": "succeeded",
        "partial_success": "succeeded",  # surfaced via metrics.failed_count > 0
        "failed": "failed",
        "cancelled": "cancelled",
        "queued": "queued",
    }
    return mapping.get(legacy_status, "running")


def _artifact_exists(run_workspace_id: UUID, provider: NexusProvider, external_id: str) -> bool:
    """Has this provider+external_id already been recorded as imported?

    Scans pipeline_run_items rows whose run is this workspace's nexus_ingest
    run with matching provider+external_id in the result jsonb. The lookup is
    workspace-scoped (RLS-equivalent) and bounded by the most recent runs.
    """
    client = get_v2_client()
    # Find recent nexus_ingest runs for this workspace (cap at 50 to bound work).
    runs_resp = (
        client.schema(_RUNS_SCHEMA)
        .table(_RUNS_TABLE)
        .select("id")
        .eq("workspace_id", str(run_workspace_id))
        .eq("kind", _PIPELINE_KIND)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    run_ids = [row["id"] for row in (runs_resp.data or [])]
    if not run_ids:
        return False
    items_resp = (
        client.schema(_RUNS_SCHEMA)
        .table(_RUN_ITEMS_TABLE)
        .select("id, result")
        .in_("run_id", run_ids)
        .eq("status", "succeeded")
        .execute()
    )
    for row in items_resp.data or []:
        result = row.get("result") or {}
        if (
            result.get("provider") == provider.value
            and result.get("external_id") == external_id
        ):
            return True
    return False


def _record_artifact(
    *,
    workspace_id: UUID,
    provider: NexusProvider,
    provider_account_id: str | None,
    artifact: ProviderArtifact,
    ingest_run_id: str,
    status: str,
    persistence: PersistenceOutcome | None = None,
    error_message: str | None = None,
) -> None:
    """Insert a per-artifact row into ``pipelines.pipeline_run_items``.

    Per-artifact fields (external_id, url, title, description, source_type,
    provider, metadata) are folded into the ``result`` jsonb column since the
    v2 ``pipeline_run_items`` table is generic across all pipeline kinds.
    """
    try:
        v2_status = _v2_run_item_status(status)
        node_id = (
            (persistence.supabase_node_id or persistence.file_node_id)
            if persistence is not None
            else None
        )
        result = {
            "provider": provider.value,
            "provider_account_id": provider_account_id,
            "external_id": artifact.external_id,
            "url": artifact.url,
            "title": artifact.title or "",
            "description": artifact.description or "",
            "source_type": artifact.source_type,
            "metadata": dict(artifact.metadata or {}),
            "node_id": node_id,
            "imported_at": _utcnow().isoformat(),
            "legacy_status": status,
        }
        # If the canonical zettel id is a UUID, surface it on the structured
        # FK column so future joins can light up; if it's a non-UUID legacy
        # node id ("yt-...", "rd-...") we leave that column NULL and keep the
        # value in result.node_id only.
        workspace_zettel_id = _maybe_uuid(node_id)
        payload: dict[str, Any] = {
            "run_id": ingest_run_id,
            "status": v2_status,
            "attempt": 1,
            "result": result,
            "error": error_message,
        }
        if workspace_zettel_id is not None:
            payload["workspace_zettel_id"] = str(workspace_zettel_id)
        client = get_v2_client()
        (
            client.schema(_RUNS_SCHEMA)
            .table(_RUN_ITEMS_TABLE)
            .insert(payload)
            .execute()
        )
    except Exception as exc:
        logger.warning(
            "Failed to record Nexus artifact %s for %s: %s",
            artifact.external_id,
            provider.value,
            exc,
        )


def _v2_run_item_status(legacy: str) -> str:
    """Map the legacy artifact status to pipeline_run_items CHECK constraint."""
    return {
        "imported": "succeeded",
        "skipped": "skipped",
        "failed": "failed",
        "running": "running",
        "queued": "queued",
    }.get(legacy, "failed")


def _maybe_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _touch_account_imported_at(account: StoredProviderAccount) -> None:
    # In v2 the token table no longer persists last_imported_at; the
    # mark_imported call updates the in-memory record only. Cross-process
    # "when did the last import run" should be sourced from
    # pipelines.pipeline_runs by callers that need it.
    try:
        ProviderTokenStore().mark_imported(account.user_id, account.provider)
    except Exception as exc:
        logger.warning("Failed to update last_imported_at for %s: %s", account.provider.value, exc)


def _should_forget_credentials(account: StoredProviderAccount, request: ImportRequest) -> bool:
    request_forget = not bool(request.remember_connection)
    metadata_value = (account.metadata or {}).get("remember_connection")
    if metadata_value is None:
        return request_forget
    if isinstance(metadata_value, bool):
        return not metadata_value
    if isinstance(metadata_value, str):
        return metadata_value.strip().lower() in {"0", "false", "no", "off"}
    return request_forget


async def _invoke_ingest_handler(
    provider: NexusProvider,
    account: StoredProviderAccount,
    request: ImportRequest,
) -> tuple[list[ProviderArtifact], dict[str, Any]]:
    module = _load_provider_module(provider, "ingest")
    handler = _resolve_callable(module, ("ingest_artifacts", "run_import", "import_artifacts"))
    if handler is None:
        raise RuntimeError(f"No ingest handler available for provider '{provider.value}'")

    result = _call_with_supported_kwargs(
        handler,
        account=account,
        provider_account=account,
        stored_account=account,
        provider=provider,
        limit=request.limit,
        force=request.force,
    )
    if inspect.isawaitable(result):
        result = await result
    return _normalize_artifacts(result, provider)


def _resolve_user_scope(auth_user_sub: str) -> str:
    scope = get_supabase_scope(auth_user_sub)
    if not scope:
        raise RuntimeError("Supabase is required for Nexus imports")
    _repo, kg_user_id = scope
    return kg_user_id


async def run_provider_import(
    *,
    auth_user_sub: str,
    provider: NexusProvider,
    request: ImportRequest,
) -> BulkImportResult:
    """Import artifacts from one provider, summarize them, and persist them."""

    profile_id = _resolve_user_scope(auth_user_sub)
    profile_uuid = UUID(str(profile_id))
    workspace_id = _resolve_workspace_id(profile_uuid)
    account = get_provider_account(profile_id, provider)
    if account is None:
        raise ValueError(f"No connected account for provider '{provider.value}'")

    provider_account_id = str(account.user_id)
    run = _create_run(
        profile_id,
        workspace_id,
        provider,
        provider_account_id=provider_account_id,
    )
    forget_after_import = _should_forget_credentials(account, request)
    credentials_forgotten = False

    try:
        artifacts, run_metadata = await _invoke_ingest_handler(provider, account, request)
        processed = await _process_artifacts(
            artifacts=artifacts,
            request=request,
            provider=provider,
            provider_account_id=provider_account_id,
            workspace_id=workspace_id,
            profile_id=profile_id,
            auth_user_sub=auth_user_sub,
            ingest_run_id=str(run.id),
        )
        run = _finalize_run(
            run_id=str(run.id),
            total_artifacts=len(artifacts),
            imported_count=processed["imported_count"],
            skipped_count=processed["skipped_count"],
            failed_count=processed["failed_count"],
            metadata=run_metadata,
        )
        _touch_account_imported_at(account)
        credentials_forgotten = _forget_credentials_if_requested(
            profile_id=profile_id,
            provider=provider,
            forget_after_import=forget_after_import,
        )

        return BulkImportResult(
            provider=provider,
            run=run,
            total_artifacts=len(artifacts),
            imported_count=processed["imported_count"],
            skipped_count=processed["skipped_count"],
            failed_count=processed["failed_count"],
            results=processed["results"],
            credentials_forgotten=credentials_forgotten,
        )
    except Exception as exc:
        run = _update_run(
            str(run.id),
            status="failed",
            total_artifacts=0,
            imported_count=0,
            skipped_count=0,
            failed_count=1,
            completed_at=_utcnow().isoformat(),
            error_message=str(exc),
        )
        if forget_after_import:
            try:
                credentials_forgotten = disconnect_provider_account(profile_id, provider)
            except Exception as disconnect_exc:
                logger.warning(
                    "Failed to forget provider credentials for %s after failed import: %s",
                    provider.value,
                    disconnect_exc,
                )
        raise RuntimeError(f"{provider.value} import failed: {exc}") from exc


async def _process_artifacts(
    *,
    artifacts: list[ProviderArtifact],
    request: ImportRequest,
    provider: NexusProvider,
    provider_account_id: str | None,
    workspace_id: UUID,
    profile_id: str,
    auth_user_sub: str,
    ingest_run_id: str,
) -> dict[str, Any]:
    imported_count = 0
    skipped_count = 0
    failed_count = 0
    results: list[dict[str, Any]] = []

    for artifact in artifacts:
        artifact_result, status = await _process_single_artifact(
            artifact=artifact,
            request=request,
            provider=provider,
            provider_account_id=provider_account_id,
            workspace_id=workspace_id,
            profile_id=profile_id,
            auth_user_sub=auth_user_sub,
            ingest_run_id=ingest_run_id,
        )
        results.append(artifact_result)
        if status == "imported":
            imported_count += 1
        elif status == "skipped":
            skipped_count += 1
        else:
            failed_count += 1

    return {
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "results": results,
    }


async def _process_single_artifact(
    *,
    artifact: ProviderArtifact,
    request: ImportRequest,
    provider: NexusProvider,
    provider_account_id: str | None,
    workspace_id: UUID,
    profile_id: str,
    auth_user_sub: str,
    ingest_run_id: str,
) -> tuple[dict[str, Any], str]:
    artifact_result = {
        "external_id": artifact.external_id,
        "url": artifact.url,
        "title": artifact.title,
        "status": "pending",
    }

    if not artifact.external_id or not artifact.url:
        return _fail_artifact(
            artifact=artifact,
            artifact_result=artifact_result,
            error_message="Artifact is missing required external_id or url",
            provider=provider,
            provider_account_id=provider_account_id,
            workspace_id=workspace_id,
            ingest_run_id=ingest_run_id,
        )

    if not request.force and _artifact_exists(workspace_id, provider, artifact.external_id):
        artifact_result["status"] = "skipped"
        artifact_result["reason"] = "Artifact already imported"
        _record_artifact(
            workspace_id=workspace_id,
            provider=provider,
            provider_account_id=provider_account_id,
            artifact=artifact,
            ingest_run_id=ingest_run_id,
            status="skipped",
        )
        return artifact_result, "skipped"

    try:
        summary_result = await summarize_artifact_url(artifact.url)
        persistence = await persist_summarized_result(
            summary_result,
            user_sub=auth_user_sub,
        )
        if persistence.supabase_duplicate and not request.force:
            artifact_result["status"] = "skipped"
            artifact_result["reason"] = "Artifact URL already exists in the knowledge graph"
            status = "skipped"
        else:
            artifact_result["status"] = "imported"
            artifact_result["node_id"] = persistence.supabase_node_id or persistence.file_node_id
            status = "imported"
        _record_artifact(
            workspace_id=workspace_id,
            provider=provider,
            provider_account_id=provider_account_id,
            artifact=artifact,
            ingest_run_id=ingest_run_id,
            status=artifact_result["status"],
            persistence=persistence,
        )
        return artifact_result, status
    except Exception as exc:
        return _fail_artifact(
            artifact=artifact,
            artifact_result=artifact_result,
            error_message=str(exc),
            provider=provider,
            provider_account_id=provider_account_id,
            workspace_id=workspace_id,
            ingest_run_id=ingest_run_id,
        )


def _fail_artifact(
    *,
    artifact: ProviderArtifact,
    artifact_result: dict[str, Any],
    error_message: str,
    provider: NexusProvider,
    provider_account_id: str | None,
    workspace_id: UUID,
    ingest_run_id: str,
) -> tuple[dict[str, Any], str]:
    artifact_result["status"] = "failed"
    artifact_result["error"] = error_message
    _record_artifact(
        workspace_id=workspace_id,
        provider=provider,
        provider_account_id=provider_account_id,
        artifact=artifact,
        ingest_run_id=ingest_run_id,
        status="failed",
        error_message=error_message,
    )
    return artifact_result, "failed"


def _finalize_run(
    *,
    run_id: str,
    total_artifacts: int,
    imported_count: int,
    skipped_count: int,
    failed_count: int,
    metadata: dict[str, Any] | None,
) -> ImportRun:
    status = _resolve_run_status(
        imported_count=imported_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
    )
    return _update_run(
        run_id,
        status=status,
        total_artifacts=total_artifacts,
        imported_count=imported_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        completed_at=_utcnow().isoformat(),
        metadata=dict(metadata or {}),
    )


def _resolve_run_status(
    *,
    imported_count: int,
    skipped_count: int,
    failed_count: int,
) -> str:
    if failed_count and (imported_count or skipped_count):
        return "partial_success"
    if failed_count:
        return "failed"
    return "completed"


def _forget_credentials_if_requested(
    *,
    profile_id: str,
    provider: NexusProvider,
    forget_after_import: bool,
) -> bool:
    if not forget_after_import:
        return False
    forgotten = disconnect_provider_account(profile_id, provider)
    if not forgotten:
        logger.warning(
            "Testing mode requested forget_connection for %s, but account delete returned false.",
            provider.value,
        )
    return forgotten


async def run_all_imports(
    *,
    auth_user_sub: str,
    request: ImportRequest,
) -> list[BulkImportResult]:
    """Run imports across every connected provider account for the user."""

    kg_scope = get_supabase_scope(auth_user_sub)
    if not kg_scope:
        raise RuntimeError("Supabase is required for Nexus imports")
    _repo, profile_id = kg_scope

    accounts = list_provider_accounts(profile_id)
    if not accounts:
        return []

    results: list[BulkImportResult] = []
    for provider in NexusProvider:
        if provider not in accounts:
            continue
        try:
            results.append(
                await run_provider_import(
                    auth_user_sub=auth_user_sub,
                    provider=provider,
                    request=request,
                )
            )
        except Exception as exc:
            logger.warning("Nexus import/all failed for %s: %s", provider.value, exc)
            results.append(
                BulkImportResult(
                    provider=provider,
                    run=None,
                    total_artifacts=0,
                    imported_count=0,
                    skipped_count=0,
                    failed_count=1,
                    results=[{"status": "failed", "error": str(exc)}],
                )
            )
    return results
