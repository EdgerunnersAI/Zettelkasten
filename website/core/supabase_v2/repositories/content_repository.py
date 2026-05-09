"""Repository for canonical content and workspace overlays."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client
from website.core.supabase_v2.models import (
    CanonicalChunkCreate,
    CanonicalUpsertResult,
    CanonicalZettelCreate,
    SearchChunkResult,
    WorkspaceZettelCreate,
)


def _bytes_to_hex(value: bytes) -> str:
    return "\\x" + value.hex()


class ContentRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def upsert_canonical_zettel(
        self,
        zettel: CanonicalZettelCreate,
        *,
        workspace: WorkspaceZettelCreate | None = None,
        chunks: list[CanonicalChunkCreate] | None = None,
    ) -> CanonicalUpsertResult:
        payload = zettel.model_dump(exclude_none=True)
        payload["content_hash"] = _bytes_to_hex(zettel.content_hash)

        response = (
            self._client.schema("content")
            .table("canonical_zettels")
            .upsert(payload, on_conflict="normalized_url,content_hash")
            .execute()
        )
        row = _first(response.data)
        canonical_id = UUID(str(row["id"]))

        was_new = bool(row.get("was_new", False))
        chunk_ids = self.upsert_chunks(canonical_id, chunks or [])

        workspace_zettel_id: UUID | None = None
        if workspace:
            workspace_zettel_id = self.upsert_workspace_zettel(canonical_id, workspace)
            self.upsert_workspace_chunk_membership(
                workspace_id=workspace.workspace_id,
                workspace_zettel_id=workspace_zettel_id,
                canonical_chunk_ids=chunk_ids,
            )

        return CanonicalUpsertResult(
            canonical_zettel_id=canonical_id,
            workspace_zettel_id=workspace_zettel_id,
            was_new=was_new,
        )

    def upsert_chunks(
        self,
        canonical_zettel_id: UUID,
        chunks: list[CanonicalChunkCreate],
    ) -> list[UUID]:
        if not chunks:
            return []
        payloads = []
        for chunk in chunks:
            payload = chunk.model_dump(exclude_none=True)
            payload["canonical_zettel_id"] = str(canonical_zettel_id)
            payload["content_hash"] = _bytes_to_hex(chunk.content_hash)
            if chunk.embedding is not None:
                payload["embedding"] = chunk.embedding
            payloads.append(payload)

        response = (
            self._client.schema("content")
            .table("canonical_chunks")
            .upsert(payloads, on_conflict="canonical_zettel_id,chunk_idx")
            .execute()
        )
        return [UUID(str(row["id"])) for row in response.data or []]

    def upsert_workspace_zettel(
        self,
        canonical_zettel_id: UUID,
        workspace: WorkspaceZettelCreate,
    ) -> UUID:
        payload = workspace.model_dump(exclude_none=True)
        payload["workspace_id"] = str(workspace.workspace_id)
        payload["canonical_zettel_id"] = str(canonical_zettel_id)

        response = (
            self._client.schema("content")
            .table("workspace_zettels")
            .upsert(payload, on_conflict="workspace_id,canonical_zettel_id")
            .execute()
        )
        row = _first(response.data)
        return UUID(str(row["id"]))

    def upsert_workspace_chunk_membership(
        self,
        *,
        workspace_id: UUID,
        workspace_zettel_id: UUID,
        canonical_chunk_ids: list[UUID],
    ) -> None:
        if not canonical_chunk_ids:
            return

        payloads = [
            {
                "workspace_id": str(workspace_id),
                "canonical_chunk_id": str(chunk_id),
                "workspace_zettel_id": str(workspace_zettel_id),
            }
            for chunk_id in canonical_chunk_ids
        ]
        (
            self._client.schema("content")
            .table("workspace_chunk_membership")
            .upsert(
                payloads,
                on_conflict="workspace_id,canonical_chunk_id,workspace_zettel_id",
            )
            .execute()
        )

    def search_chunks(
        self,
        *,
        workspace_id: UUID,
        query_embedding: list[float],
        limit: int = 32,
    ) -> list[SearchChunkResult]:
        response = self._client.schema("content").rpc(
            "search_chunks",
            {
                "p_workspace_id": str(workspace_id),
                "p_query_embedding": query_embedding,
                "p_limit": limit,
            },
        ).execute()
        return [SearchChunkResult(**row) for row in response.data or []]


def _first(data):
    if not data:
        raise RuntimeError("Supabase returned no rows")
    if isinstance(data, list):
        return data[0]
    return data
