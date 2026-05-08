"""Billing repository for DB v2 typed RPCs."""

from __future__ import annotations

from supabase import Client

from website.core.supabase_v2.client import get_v2_client
from uuid import UUID

from website.core.supabase_v2.models import QuotaDebitRequest


class BillingRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def consume_quota(self, request: QuotaDebitRequest) -> bool:
        response = self._client.schema("core").rpc(
            "consume_quota",
            {
                "p_workspace_id": str(request.workspace_id),
                "p_feature": request.feature,
                "p_unit": request.unit,
                "p_period_start": request.period_start.isoformat(),
            },
        ).execute()
        return bool(response.data)

    def check_entitlement(self, *, profile_id: UUID, feature: str, unit: str) -> bool:
        response = self._client.schema("billing").rpc(
            "pricing_consume_entitlement",
            {
                "p_profile_id": str(profile_id),
                "p_feature": feature,
                "p_unit": unit,
            },
        ).execute()
        return bool(response.data)
