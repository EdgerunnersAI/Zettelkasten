"""Supabase v2 knowledge graph writer.

Writes the engine v2 summary into ``content.workspace_zettels.ai_summary``
(text) plus the canonical row in ``content.canonical_zettels``.

Phase 3.1 of the v2 purge: this writer was rebased off the legacy
``kg_nodes.summary`` jsonb column onto the v2 canonical+overlay model:

    1. Upsert the canonical row in ``content.canonical_zettels`` keyed by
       ``(normalized_url, content_hash)`` via the race-safe RPC wrapper.
    2. Upsert the workspace overlay in ``content.workspace_zettels`` with
       ``ai_summary`` (text, the canonical-JSON envelope) and
       ``ai_summary_engine_version``.

The summary text is the same canonical JSON envelope shape that the
frontend has always consumed; only its column-of-record changes.

The public class name + ``write(result, *, user_id)`` signature is
preserved byte-for-byte. ``user_id`` is interpreted as the v1 profile_id
(auth.users.id) and is mapped to the user's default workspace_id at
write time. Workspace_id is NOT NULL on ``content.workspace_zettels``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from website.core.supabase_v2.models import (
    CanonicalZettelCreate,
    WorkspaceZettelCreate,
)
from website.core.supabase_v2.repositories.content_repository import ContentRepository
from website.core.supabase_v2.repositories.core_repository import CoreRepository
from website.core.text_polish import polish, polish_envelope, rewrite_tags
from website.features.summarization_engine.core.errors import WriterError
from website.features.summarization_engine.core.models import SummaryResult
from website.features.summarization_engine.writers.base import BaseWriter

_log = logging.getLogger(__name__)


class _UnknownWorkspaceError(RuntimeError):
    """Raised when a profile has no default workspace — fail loud rather
    than silently insert with NULL workspace_id (which the v2 NOT NULL
    constraint would reject anyway)."""


def _encode_summary_blob(result: SummaryResult) -> str:
    """Serialize the full structured summary into the canonical JSON envelope.

    Applies the deterministic polish + caveat-strip pass at the WRITE
    boundary so every persisted row is born clean. Idempotent.
    """
    dump = result.model_dump(mode="json")
    payload = {
        "mini_title": dump.get("mini_title") or "",
        "brief_summary": dump.get("brief_summary") or "",
        "detailed_summary": dump.get("detailed_summary") or [],
        "closing_remarks": dump.get("closing_remarks") or "",
    }
    payload = polish_envelope(payload)
    return json.dumps(payload, ensure_ascii=False)


def _content_hash_bytes(*, url: str, summary_blob: str) -> bytes:
    """Stable canonical content_hash for dedup. Combines normalized_url
    and the summary envelope so re-summaries with new content produce a
    new canonical row."""
    digest = hashlib.sha256()
    digest.update(url.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(summary_blob.encode("utf-8"))
    return digest.digest()


class SupabaseWriter(BaseWriter):
    def __init__(
        self,
        repository: ContentRepository | None = None,
        key_pool: Any = None,
        *,
        core_repo: CoreRepository | None = None,
    ):
        self._repository = repository or ContentRepository()
        self._core = core_repo or CoreRepository()
        self._key_pool = key_pool  # reserved for future entity canonicalisation
        self._workspace_cache: dict[UUID, UUID] = {}

    def _resolve_workspace_id(self, user_id: UUID) -> UUID:
        cached = self._workspace_cache.get(user_id)
        if cached is not None:
            return cached
        workspace_id = self._core.get_default_workspace_id(user_id)
        if workspace_id is None:
            raise _UnknownWorkspaceError(
                f"profile {user_id} has no default workspace; cannot write canonical zettel"
            )
        self._workspace_cache[user_id] = workspace_id
        return workspace_id

    async def write(self, result: SummaryResult, *, user_id: UUID) -> dict[str, Any]:
        try:
            workspace_id = self._resolve_workspace_id(user_id)
            summary_blob = _encode_summary_blob(result)
            url = result.metadata.url

            zettel = CanonicalZettelCreate(
                normalized_url=url,
                content_hash=_content_hash_bytes(url=url, summary_blob=summary_blob),
                source_type=result.metadata.source_type.value,
                title=polish(result.mini_title or "") or None,
                body_md=summary_blob,
                source_metadata={"engine_version": result.metadata.engine_version},
            )
            overlay = WorkspaceZettelCreate(
                workspace_id=workspace_id,
                ai_summary=summary_blob,
                ai_summary_engine_version=result.metadata.engine_version,
                user_tags=list(rewrite_tags(result.tags or [])),
                added_via="website",
            )
            outcome = self._repository.upsert_canonical_zettel(
                zettel,
                workspace=overlay,
            )
        except _UnknownWorkspaceError:
            raise
        except Exception as exc:
            raise WriterError(
                f"Failed to write Supabase v2 canonical zettel: {exc}",
                writer="supabase",
            ) from exc

        canonical_id = str(outcome.canonical_zettel_id)
        if not outcome.was_new:
            return {
                "status": "skipped",
                "reason": "duplicate_url",
                "node_id": canonical_id,
                "workspace_zettel_id": (
                    str(outcome.workspace_zettel_id)
                    if outcome.workspace_zettel_id
                    else None
                ),
            }
        return {
            "status": "created",
            "node_id": canonical_id,
            "workspace_zettel_id": (
                str(outcome.workspace_zettel_id)
                if outcome.workspace_zettel_id
                else None
            ),
        }
