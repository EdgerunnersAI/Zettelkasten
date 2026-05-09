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

    def get_profile(self, profile_id: UUID) -> dict | None:
        """Fetch the ``core.profiles`` row for the given profile UUID.

        Returns ``None`` if no row exists (PostgREST returns an empty/None
        response under ``maybe_single``). Selects only the columns the
        ``GET /api/me`` handler needs so we don't drag larger profile fields
        across the wire on every authenticated request.
        """
        resp = (
            self._client.schema("core")
            .table("profiles")
            .select("id, email, display_name, avatar_url, created_at")
            .eq("id", str(profile_id))
            .maybe_single()
            .execute()
        )
        if resp is None:
            return None
        return resp.data if resp.data else None

    def ensure_profile(self, *, profile_id: UUID, email: str | None = None, display_name: str | None = None) -> None:
        self._client.schema("core").table("profiles").upsert(
            {
                "id": str(profile_id),
                "email": email,
                "display_name": display_name,
            },
            on_conflict="id",
        ).execute()

