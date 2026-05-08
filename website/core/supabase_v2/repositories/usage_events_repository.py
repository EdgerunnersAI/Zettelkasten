"""Usage-event writes for DB v2."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class UsageEventsRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def record_event(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        feature: str,
        unit: str,
        quantity: float = 1,
        metadata: dict | None = None,
    ) -> None:
        self._client.schema("core").table("usage_events").insert(
            {
                "workspace_id": str(workspace_id),
                "profile_id": str(profile_id),
                "feature": feature,
                "unit": unit,
                "quantity": quantity,
                "metadata": metadata or {},
            }
        ).execute()

