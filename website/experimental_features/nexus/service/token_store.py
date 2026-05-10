"""Provider OAuth token store backed by ``pipelines.nexus_provider_tokens``.

V2 surface (Phase 8 of the v2 purge): persists provider OAuth tokens to
``pipelines.nexus_provider_tokens``. PK = ``(profile_id, provider)``;
``workspace_id`` is NOT NULL (RLS predicate target). Profile id refers to
``core.profiles(id)``, which is the Supabase auth user UUID.

Storage contract:
    * Columns: ``profile_id`` (uuid), ``provider`` (text),
      ``workspace_id`` (uuid, NOT NULL), ``encrypted_token`` (bytea),
      ``refresh_token`` (bytea, nullable), ``expires_at`` (timestamptz,
      nullable). The table is intentionally token-only; per-account
      metadata (``account_id``, ``account_username``, ``scopes``,
      ``metadata``, ``last_refreshed_at``, ``last_imported_at``) is NOT
      persisted in v2. Callers that read those fields receive
      ``None`` / ``[]`` / ``{}`` on roundtrip — this is a documented v2
      simplification, not a regression.
    * Token encryption uses Fernet with ``NEXUS_TOKEN_ENCRYPTION_KEY``;
      the bytea column stores the UTF-8 base64 ciphertext bytes.

Public class name (``ProviderTokenStore``) and method signatures are
preserved byte-for-byte across the v1→v2 migration.
"""
from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from website.core.supabase_v2.client import get_v2_client
from website.core.supabase_v2.repositories.core_repository import CoreRepository
from website.experimental_features.nexus.source_ingest.common.models import (
    NexusProvider,
    ProviderTokenSet,
    StoredProviderAccount,
)

TOKEN_ENCRYPTION_KEY_ENV = "NEXUS_TOKEN_ENCRYPTION_KEY"
_TOKENS_SCHEMA = "pipelines"
_TOKENS_TABLE = "nexus_provider_tokens"
logger = logging.getLogger("website.experimental_features.nexus.token_store")

TokenRefreshCallback = Callable[
    [StoredProviderAccount],
    ProviderTokenSet | Awaitable[ProviderTokenSet],
]


class _UnknownWorkspaceError(RuntimeError):
    """Raised when a profile has no default workspace — required for
    NOT NULL workspace_id on ``pipelines.nexus_provider_tokens``."""


class ProviderTokenStore:
    def __init__(
        self,
        *,
        client: Any | None = None,
        encryption_key: str | None = None,
        core_repo: CoreRepository | None = None,
    ) -> None:
        self._client = client or get_v2_client()
        self._fernet = Fernet(_load_encryption_key(encryption_key))
        self._core = core_repo or CoreRepository()
        self._workspace_cache: dict[UUID, UUID] = {}

    def _resolve_workspace_id(self, profile_id: UUID) -> UUID:
        cached = self._workspace_cache.get(profile_id)
        if cached is not None:
            return cached
        workspace_id = self._core.get_default_workspace_id(profile_id)
        if workspace_id is None:
            raise _UnknownWorkspaceError(
                f"profile {profile_id} has no default workspace; cannot persist Nexus token row"
            )
        self._workspace_cache[profile_id] = workspace_id
        return workspace_id

    def upsert_account(self, account: StoredProviderAccount) -> StoredProviderAccount:
        if not account.access_token:
            raise ValueError("Provider accounts must include a non-empty access token.")
        workspace_id = self._resolve_workspace_id(account.user_id)
        payload = {
            "profile_id": str(account.user_id),
            "workspace_id": str(workspace_id),
            "provider": account.provider.value,
            "encrypted_token": _bytes_to_hex(self._encrypt_bytes(account.access_token)),
            "refresh_token": (
                _bytes_to_hex(self._encrypt_bytes(account.refresh_token))
                if account.refresh_token
                else None
            ),
            "expires_at": _isoformat(account.expires_at),
        }
        response = (
            self._client.schema(_TOKENS_SCHEMA)
            .table(_TOKENS_TABLE)
            .upsert(payload, on_conflict="profile_id,provider")
            .execute()
        )
        if not response.data:
            stored = self.get_account(account.user_id, account.provider)
            if stored is None:
                raise RuntimeError("Failed to persist Nexus provider account.")
            return stored
        return self._row_to_account(response.data[0])

    def get_account(
        self,
        user_id: UUID,
        provider: NexusProvider,
    ) -> StoredProviderAccount | None:
        response = (
            self._client.schema(_TOKENS_SCHEMA)
            .table(_TOKENS_TABLE)
            .select("*")
            .eq("profile_id", str(user_id))
            .eq("provider", provider.value)
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return self._row_to_account(response.data[0])

    def list_accounts(self, user_id: UUID) -> list[StoredProviderAccount]:
        response = (
            self._client.schema(_TOKENS_SCHEMA)
            .table(_TOKENS_TABLE)
            .select("*")
            .eq("profile_id", str(user_id))
            .execute()
        )
        rows = response.data or []
        accounts: list[StoredProviderAccount] = []
        for row in rows:
            try:
                accounts.append(self._row_to_account(row))
            except Exception as exc:
                logger.warning("Skipping provider token row that could not be decoded: %s", exc)
        return accounts

    def delete_account(self, user_id: UUID, provider: NexusProvider) -> bool:
        (
            self._client.schema(_TOKENS_SCHEMA)
            .table(_TOKENS_TABLE)
            .delete()
            .eq("profile_id", str(user_id))
            .eq("provider", provider.value)
            .execute()
        )
        return self.get_account(user_id, provider) is None

    async def refresh_and_persist(
        self,
        user_id: UUID,
        provider: NexusProvider,
        refresh_callback: TokenRefreshCallback,
    ) -> StoredProviderAccount:
        account = self.get_account(user_id, provider)
        if account is None:
            raise LookupError(f"No Nexus provider account found for provider {provider.value}.")
        if not account.refresh_token:
            raise RuntimeError(
                f"Provider account for {provider.value} does not have a refresh token."
            )

        refreshed_tokens = refresh_callback(account)
        if inspect.isawaitable(refreshed_tokens):
            refreshed_tokens = await refreshed_tokens

        updated_account = account.model_copy(
            update={
                "access_token": refreshed_tokens.access_token,
                "refresh_token": refreshed_tokens.refresh_token or account.refresh_token,
                "token_type": refreshed_tokens.token_type or account.token_type,
                "scopes": refreshed_tokens.scopes or account.scopes,
                "expires_at": refreshed_tokens.expires_at or account.expires_at,
                "last_refreshed_at": _utcnow(),
                "metadata": account.metadata,
            }
        )
        return self.upsert_account(updated_account)

    def mark_imported(
        self,
        user_id: UUID,
        provider: NexusProvider,
        *,
        imported_at: datetime | None = None,
    ) -> StoredProviderAccount:
        # v2 schema does not persist last_imported_at — the in-memory copy
        # is updated for callers that hold the returned account, but the
        # value is not roundtripped through the database. Callers that need
        # cross-process visibility into "when did the last import run for
        # provider X" should use ``pipelines.pipeline_runs`` rows instead.
        account = self.get_account(user_id, provider)
        if account is None:
            raise LookupError(f"No Nexus provider account found for provider {provider.value}.")
        return account.model_copy(update={"last_imported_at": imported_at or _utcnow()})

    def _row_to_account(self, row: dict[str, Any]) -> StoredProviderAccount:
        try:
            access_token = self._decrypt_bytes(row.get("encrypted_token"))
            refresh_token = self._decrypt_bytes(row.get("refresh_token"))
        except InvalidToken as exc:
            raise RuntimeError(
                "Failed to decrypt stored Nexus provider tokens. "
                f"Verify {TOKEN_ENCRYPTION_KEY_ENV}."
            ) from exc

        if not access_token:
            raise RuntimeError("Stored Nexus provider account does not include a usable access token.")

        return StoredProviderAccount(
            user_id=row["profile_id"],
            provider=NexusProvider(row["provider"]),
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            scopes=[],
            expires_at=row.get("expires_at"),
            metadata={},
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            last_refreshed_at=None,
            last_imported_at=None,
        )

    def _encrypt_bytes(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def _decrypt_bytes(self, value: Any) -> str | None:
        if value is None:
            return None
        # PostgREST returns bytea as a hex-prefixed string ("\x...") or raw bytes
        # depending on representation. Normalise both.
        if isinstance(value, str):
            if value.startswith("\\x"):
                raw = bytes.fromhex(value[2:])
            else:
                # Already decrypted ciphertext token (legacy fallback path).
                raw = value.encode("utf-8")
        elif isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        elif isinstance(value, memoryview):
            raw = value.tobytes()
        else:
            return None
        if not raw:
            return None
        return self._fernet.decrypt(raw).decode("utf-8")


def _load_encryption_key(explicit_key: str | None) -> bytes:
    raw_key = (explicit_key or os.environ.get(TOKEN_ENCRYPTION_KEY_ENV) or "").strip()
    if not raw_key:
        raise RuntimeError(
            f"Missing required environment variable: {TOKEN_ENCRYPTION_KEY_ENV}"
        )
    try:
        Fernet(raw_key.encode("utf-8"))
    except ValueError as exc:
        raise RuntimeError(
            f"{TOKEN_ENCRYPTION_KEY_ENV} must be a valid Fernet key."
        ) from exc
    return raw_key.encode("utf-8")


def _bytes_to_hex(value: bytes) -> str:
    return "\\x" + value.hex()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["ProviderTokenStore", "TOKEN_ENCRYPTION_KEY_ENV", "TokenRefreshCallback"]
