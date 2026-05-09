"""Repository for DB v2 RAG tables."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class RAGRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def create_kasten(
        self,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None = None,
        default_quality: str = "fast",
    ) -> UUID:
        response = self._client.schema("rag").table("kastens").insert(
            {
                "workspace_id": str(workspace_id),
                "name": name,
                "description": description,
                "default_quality": default_quality,
            }
        ).execute()
        return UUID(str(_first(response.data)["id"]))

    def add_kasten_member(self, *, kasten_id: UUID, workspace_id: UUID, role: str) -> None:
        self._client.schema("rag").table("kasten_members").upsert(
            {
                "kasten_id": str(kasten_id),
                "workspace_id": str(workspace_id),
                "role": role,
            },
            on_conflict="kasten_id,workspace_id",
        ).execute()

    def add_zettel_to_kasten(
        self,
        *,
        kasten_id: UUID,
        workspace_zettel_id: UUID,
        added_via: str = "manual",
        added_filter: dict | None = None,
    ) -> None:
        self._client.schema("rag").table("kasten_zettels").upsert(
            {
                "kasten_id": str(kasten_id),
                "workspace_zettel_id": str(workspace_zettel_id),
                "added_via": added_via,
                "added_filter": added_filter,
            },
            on_conflict="kasten_id,workspace_zettel_id",
        ).execute()

    def chunk_share_for_kasten(self, kasten_id: UUID) -> dict[str, int]:
        """Per-canonical-chunk usage count inside a Kasten.

        Wraps `rag.chunk_share_for_kasten(p_kasten_id)` (Phase 1.A v2 RPC).
        Returns ``{canonical_chunk_id_str: chunk_count}``. Empty dict on no rows.
        Caller is expected to handle / catch RPC errors — repository deliberately
        does not swallow them so failure modes stay visible.
        """
        response = self._client.schema("rag").rpc(
            "chunk_share_for_kasten", {"p_kasten_id": str(kasten_id)}
        ).execute()
        rows = response.data or []
        return {
            str(row["canonical_chunk_id"]): int(row.get("chunk_count", 0))
            for row in rows
        }


def _first(data):
    if not data:
        raise RuntimeError("Supabase returned no rows")
    return data[0] if isinstance(data, list) else data

