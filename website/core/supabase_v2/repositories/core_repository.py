"""Core schema repository for profile/workspace lookups."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class CoreRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def get_default_workspace_id(self, profile_id: UUID) -> UUID | None:
        response = (
            self._client.schema("core")
            .table("workspace_members")
            .select("workspace_id, role")
            .eq("profile_id", str(profile_id))
            .order("added_at")
            .limit(1)
            .execute()
        )
        if not response.data:
            return None
        return UUID(str(response.data[0]["workspace_id"]))

    def ensure_profile(self, *, profile_id: UUID, email: str | None = None, display_name: str | None = None) -> None:
        self._client.schema("core").table("profiles").upsert(
            {
                "id": str(profile_id),
                "email": email,
                "display_name": display_name,
            },
            on_conflict="id",
        ).execute()

