"""Repository for DB v2 RAG tables."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class RAGRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    # ─────────────────────────────────────────────────────────────────────
    # rag.kastens CRUD (Phase 2.6)
    # ─────────────────────────────────────────────────────────────────────

    def create_kasten(
        self,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        default_quality: str = "fast",
    ) -> dict:
        """Insert a kasten row, returning the full row dict."""
        response = self._client.schema("rag").table("kastens").insert(
            {
                "workspace_id": str(workspace_id),
                "name": name,
                "description": description,
                "icon": icon,
                "color": color,
                "default_quality": default_quality,
            }
        ).execute()
        return _first(response.data)

    def get_kasten(self, kasten_id: UUID, workspace_id: UUID) -> dict | None:
        response = (
            self._client.schema("rag").table("kastens")
            .select("*")
            .eq("id", str(kasten_id))
            .eq("workspace_id", str(workspace_id))
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def list_kastens(self, workspace_id: UUID, limit: int = 50) -> list[dict]:
        response = (
            self._client.schema("rag").table("kastens")
            .select("*")
            .eq("workspace_id", str(workspace_id))
            .order("last_used_at", desc=True, nullsfirst=False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    def update_kasten(
        self,
        kasten_id: UUID,
        workspace_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        default_quality: str | None = None,
    ) -> dict | None:
        payload: dict = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if icon is not None:
            payload["icon"] = icon
        if color is not None:
            payload["color"] = color
        if default_quality is not None:
            payload["default_quality"] = default_quality
        if not payload:
            return self.get_kasten(kasten_id, workspace_id)
        response = (
            self._client.schema("rag").table("kastens")
            .update(payload)
            .eq("id", str(kasten_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return response.data[0] if response.data else None

    def delete_kasten(self, kasten_id: UUID, workspace_id: UUID) -> bool:
        response = (
            self._client.schema("rag").table("kastens")
            .delete()
            .eq("id", str(kasten_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return bool(response.data)

    def touch_kasten(self, kasten_id: UUID, workspace_id: UUID) -> dict | None:
        response = (
            self._client.schema("rag").table("kastens")
            .update({"last_used_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", str(kasten_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return response.data[0] if response.data else None

    def list_kasten_zettels(self, kasten_id: UUID) -> list[dict]:
        """Wraps `rag.list_kasten_zettels(p_kasten_id)` (Phase 1.A RPC).

        Returns the workspace_zettel + canonical_zettel JOIN shape — replaces
        the legacy nested PostgREST embed `select("..., kg_nodes(...)")`.
        """
        response = self._client.schema("rag").rpc(
            "list_kasten_zettels", {"p_kasten_id": str(kasten_id)}
        ).execute()
        return response.data or []

    def add_zettels_to_kasten(
        self,
        *,
        kasten_id: UUID,
        workspace_zettel_ids: list[UUID],
    ) -> int:
        """Wraps `rag.bulk_add_to_kasten(p_kasten_id, p_workspace_zettel_ids)`.

        Returns the number of newly-inserted rows (existing memberships are
        ON CONFLICT DO NOTHING-skipped). 0 on empty input.
        """
        if not workspace_zettel_ids:
            return 0
        response = self._client.schema("rag").rpc(
            "bulk_add_to_kasten",
            {
                "p_kasten_id": str(kasten_id),
                "p_workspace_zettel_ids": [str(wz) for wz in workspace_zettel_ids],
            },
        ).execute()
        return int(response.data or 0)

    def remove_zettel_from_kasten(
        self,
        *,
        kasten_id: UUID,
        workspace_zettel_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        # BOLA guard: refuse the delete unless the kasten belongs to the
        # caller's workspace. The PostgREST DELETE below uses the service-role
        # client (RLS bypassed by design), so the workspace gate MUST be
        # explicit here to prevent cross-tenant member deletion.
        if self.get_kasten(kasten_id, workspace_id) is None:
            return False
        response = (
            self._client.schema("rag").table("kasten_zettels")
            .delete()
            .eq("kasten_id", str(kasten_id))
            .eq("workspace_zettel_id", str(workspace_zettel_id))
            .execute()
        )
        return bool(response.data)

    def remove_zettels_from_kasten(
        self,
        *,
        kasten_id: UUID,
        workspace_zettel_ids: list[UUID],
        workspace_id: UUID,
    ) -> int:
        if not workspace_zettel_ids:
            return 0
        # BOLA guard: same as remove_zettel_from_kasten above.
        if self.get_kasten(kasten_id, workspace_id) is None:
            return 0
        response = (
            self._client.schema("rag").table("kasten_zettels")
            .delete()
            .eq("kasten_id", str(kasten_id))
            .in_("workspace_zettel_id", [str(wz) for wz in workspace_zettel_ids])
            .execute()
        )
        return len(response.data or [])

    # ─────────────────────────────────────────────────────────────────────
    # rag.chat_sessions / rag.chat_messages CRUD (Phase 2.7)
    # ─────────────────────────────────────────────────────────────────────

    def create_chat_session(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        kasten_id: UUID | None = None,
        title: str | None = None,
    ) -> UUID:
        """Insert a chat_session row. workspace_id is NOT NULL (DB constraint)."""
        response = self._client.schema("rag").table("chat_sessions").insert(
            {
                "workspace_id": str(workspace_id),
                "profile_id": str(profile_id),
                "kasten_id": str(kasten_id) if kasten_id else None,
                "title": title,
            }
        ).execute()
        return UUID(str(_first(response.data)["id"]))

    def get_chat_session(
        self,
        session_id: UUID,
        workspace_id: UUID,
    ) -> dict | None:
        response = (
            self._client.schema("rag").table("chat_sessions")
            .select("*")
            .eq("id", str(session_id))
            .eq("workspace_id", str(workspace_id))
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def list_chat_sessions(
        self,
        *,
        workspace_id: UUID,
        kasten_id: UUID | None = None,
        limit: int = 50,
    ) -> list[dict]:
        query = (
            self._client.schema("rag").table("chat_sessions")
            .select("*")
            .eq("workspace_id", str(workspace_id))
            .order("updated_at", desc=True)
            .limit(limit)
        )
        if kasten_id is not None:
            query = query.eq("kasten_id", str(kasten_id))
        response = query.execute()
        return response.data or []

    def update_chat_session(
        self,
        session_id: UUID,
        workspace_id: UUID,
        *,
        kasten_id: UUID | None = None,
        title: str | None = None,
    ) -> dict | None:
        payload: dict = {}
        if kasten_id is not None:
            payload["kasten_id"] = str(kasten_id)
        if title is not None:
            payload["title"] = title
        if not payload:
            return None
        response = (
            self._client.schema("rag").table("chat_sessions")
            .update(payload)
            .eq("id", str(session_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return response.data[0] if response.data else None

    def delete_chat_session(
        self,
        session_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        response = (
            self._client.schema("rag").table("chat_sessions")
            .delete()
            .eq("id", str(session_id))
            .eq("workspace_id", str(workspace_id))
            .execute()
        )
        return bool(response.data)

    def append_chat_message(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        role: str,
        content: str,
        citations: list[dict] | None = None,
        verdict: str | None = None,
        retrieval_run_id: UUID | None = None,
        token_counts: dict | None = None,
        latency_ms: int | None = None,
    ) -> dict:
        """Insert a chat_messages row. workspace_id is NOT NULL (DB constraint).

        The trigger ``rag.assert_chat_message_workspace_match()`` enforces
        ``chat_messages.workspace_id == chat_sessions.workspace_id`` — passing
        a mismatched workspace_id will raise.
        """
        response = self._client.schema("rag").table("chat_messages").insert(
            {
                "session_id": str(session_id),
                "workspace_id": str(workspace_id),
                "role": role,
                "content": content,
                "citations": citations or [],
                "verdict": verdict,
                "retrieval_run_id": str(retrieval_run_id) if retrieval_run_id else None,
                "token_counts": token_counts,
                "latency_ms": latency_ms,
            }
        ).execute()
        return _first(response.data)

    def list_chat_messages(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        limit: int = 100,
    ) -> list[dict]:
        response = (
            self._client.schema("rag").table("chat_messages")
            .select("*")
            .eq("session_id", str(session_id))
            .eq("workspace_id", str(workspace_id))
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return response.data or []

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

    def search_signal_weights(
        self,
        *,
        workspace_id: UUID | str,
        target_chunk_ids: list[UUID | str],
        query_class: str,
    ) -> list[dict]:
        """Per-(source, target) decay-weighted retrieval signal weights.

        Wraps `rag.search_signal_weights(p_workspace_id, p_target_chunk_ids,
        p_query_class)` (Phase 1.A v2 RPC) which returns rows of
        {source_canonical_chunk_id, target_canonical_chunk_id, weight} filtered
        to the requested workspace + query_class + target chunk IDs.

        The RPC is SECURITY DEFINER and authorises against the caller via
        `core.jwt_workspace_ids()` / service-role. Caller is expected to handle
        / catch RPC errors — the repository deliberately does not swallow them
        so failure modes stay visible at the call site (graph_score wraps in a
        broad except to preserve the v1 "MV missing → 0 bonus" identity).
        """
        response = self._client.schema("rag").rpc(
            "search_signal_weights",
            {
                "p_workspace_id": str(workspace_id),
                "p_target_chunk_ids": [str(cc) for cc in target_chunk_ids],
                "p_query_class": str(query_class),
            },
        ).execute()
        return response.data or []


def _first(data):
    if not data:
        raise RuntimeError("Supabase returned no rows")
    return data[0] if isinstance(data, list) else data

