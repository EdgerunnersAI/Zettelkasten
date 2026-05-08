"""Repository for DB v2 chat sessions and messages."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class ChatRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def create_session(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        kasten_id: UUID | None = None,
        title: str | None = None,
    ) -> UUID:
        response = self._client.schema("rag").table("chat_sessions").insert(
            {
                "workspace_id": str(workspace_id),
                "profile_id": str(profile_id),
                "kasten_id": str(kasten_id) if kasten_id else None,
                "title": title,
            }
        ).execute()
        return UUID(str(_first(response.data)["id"]))

    def append_message(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        role: str,
        content: str,
        citations: list[dict] | None = None,
        retrieval_run_id: UUID | None = None,
        token_counts: dict | None = None,
        latency_ms: int | None = None,
    ) -> UUID:
        response = self._client.schema("rag").table("chat_messages").insert(
            {
                "session_id": str(session_id),
                "workspace_id": str(workspace_id),
                "role": role,
                "content": content,
                "citations": citations or [],
                "retrieval_run_id": str(retrieval_run_id) if retrieval_run_id else None,
                "token_counts": token_counts,
                "latency_ms": latency_ms,
            }
        ).execute()
        return UUID(str(_first(response.data)["id"]))


def _first(data):
    if not data:
        raise RuntimeError("Supabase returned no rows")
    return data[0] if isinstance(data, list) else data

