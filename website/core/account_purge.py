"""User-account purge helper — pre-flight cleanup before auth.admin.delete_user.

Phase 8.5.R2-2. Reused by both:
- production "delete account" flow (when implemented)
- tests/integration/v2/test_user_cascade.py regression test

The v2 schema places ON DELETE CASCADE on every direct child of auth.users via
core.profiles (commit 6790a22, fresh-start migration). So most user data is
auto-cleaned by Postgres when auth.admin.delete_user fires. This helper handles
the few categories that need EXPLICIT pre-flight treatment:

1. Append-only event logs that must be anonymised (not deleted) to preserve
   aggregate signal in derived MVs (rag.retrieval_feedback_events). Pattern:
   GDPR-pseudonymisation via NULL user_id (Salesforce / Stripe / AWS telemetry-
   on-account-closure convention).

2. Soft-cancel rather than hard-delete for billing audit trails.
3. Storage-bucket objects (deferred until storage is wired into v2).

The introspection RPC (core.introspect_auth_users_dependents()) is the
authoritative source for "what FKs to auth.users transitively"; this helper
covers the SUBSET that needs non-default treatment.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel
from supabase import Client

from website.core.supabase_v2.client import get_v2_client

logger = logging.getLogger(__name__)


class UserPurgeReport(BaseModel):
    """Counts of mutations applied during pre-delete cleanup."""

    retrieval_feedback_events_anonymised: int = 0
    pricing_subscriptions_cancelled: int = 0
    kasten_memberships_removed: int = 0
    notes: list[str] = []


def purge_user_dependencies(
    profile_id: uuid.UUID,
    *,
    client: Client | None = None,
) -> UserPurgeReport:
    """Pre-flight cleanup; safe to call before ``auth.admin.delete_user``.

    Idempotent — re-running on an already-purged user returns zero counts.
    Never raises on individual table failures (each block is best-effort);
    the caller should still treat a thrown error as a hard stop.
    """
    sb = client or get_v2_client()
    report = UserPurgeReport()

    # 1) Anonymise retrieval feedback events (preserve aggregate signal in MVs)
    try:
        resp = (
            sb.schema("rag")
            .table("retrieval_feedback_events")
            .update({"user_id": None})
            .eq("user_id", str(profile_id))
            .execute()
        )
        report.retrieval_feedback_events_anonymised = len(resp.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieval_feedback_events anonymise failed: %s", exc)
        report.notes.append(f"feedback_events_anon_failed: {exc!r}")

    # 2) Soft-cancel active billing subscriptions (audit trail)
    try:
        resp = (
            sb.schema("billing")
            .table("pricing_subscriptions")
            .update({"status": "cancelled"})
            .eq("profile_id", str(profile_id))
            .neq("status", "cancelled")
            .execute()
        )
        report.pricing_subscriptions_cancelled = len(resp.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("pricing_subscriptions cancel failed: %s", exc)
        report.notes.append(f"pricing_cancel_failed: {exc!r}")

    # 3) Explicit kasten_members removal (Phase 8.5.R2 amendment 2026-05-10).
    # CASCADE chain through core.profiles -> core.workspaces handles this
    # functionally today, but explicit handling is belt-and-suspenders against
    # a future FK clause change (e.g. dropping CASCADE) silently breaking
    # offboarding.
    #
    # Phase 8.5.R2-amend 2026-05-11: rag.kasten_members is keyed by
    # (kasten_id, workspace_id) — there's no per-profile column. The prior
    # block referenced `member_profile_id` which does not exist; the silent
    # try/except meant the belt-and-suspenders never actually fired. Bug
    # surfaced by P2.5 ranker-boost test wiring purge_user_dependencies into
    # conftest teardown. Correct shape: look up the user's workspaces first,
    # then purge kasten_members scoped to those workspaces.
    try:
        ws_resp = (
            sb.schema("core")
            .table("workspaces")
            .select("id")
            .eq("owner_profile_id", str(profile_id))
            .execute()
        )
        ws_ids = [str(row["id"]) for row in (ws_resp.data or [])]
        if ws_ids:
            resp = (
                sb.schema("rag")
                .table("kasten_members")
                .delete()
                .in_("workspace_id", ws_ids)
                .execute()
            )
            report.kasten_memberships_removed = len(resp.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("kasten_members removal failed: %s", exc)
        report.notes.append(f"kasten_members_failed: {exc!r}")

    # Future: storage.objects cleanup once Supabase Storage is wired into v2.
    # Tracked in the v2 final-acceptance test plan.

    return report
