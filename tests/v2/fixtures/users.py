"""Helpers for minting and tearing down v2 Supabase test users.

These are plain functions (not pytest fixtures) so they can be called from any
sync or async context. They hit the live Supabase project configured by
``website.core.supabase_v2.client`` — callers are responsible for cleanup via
``delete_test_user``.
"""
from __future__ import annotations

import time
import uuid
from typing import NamedTuple

from postgrest.exceptions import APIError

from website.core.supabase_v2.client import get_v2_anon_client, get_v2_client

_DEFAULT_PASSWORD = "x" * 16


class MintedUser(NamedTuple):
    """Result of ``mint_test_user_with_workspaces``.

    ``auth_user_id`` is the ``auth.users.id`` UUID — this is what
    ``delete_test_user`` requires for teardown. ``profile_id`` is the
    ``core.profiles.id`` UUID; today the FK invariant makes them equal,
    but callers should not rely on that and should use ``auth_user_id``
    explicitly when deleting.
    """

    auth_user_id: uuid.UUID
    profile_id: uuid.UUID
    workspace_ids: list[uuid.UUID]
    jwt: str
    email: str  # 8.0-TX: PII canary for cross-tenant leak detection


def mint_test_user_with_workspaces(*, workspace_count: int = 1) -> MintedUser:
    """Create a fresh Supabase auth user, sign them in, return a ``MintedUser``.

    Steps:
      1. Service-role client creates an auth user with a unique e2e email. The
         ``core.handle_new_auth_user`` trigger inserts the profile, which in
         turn fires ``core.create_personal_workspace`` to create the personal
         workspace and owner membership.
      2. Briefly poll for the profile + personal workspace via the service-role
         PostgREST client (the trigger chain is synchronous, but the auth API
         response and the profile/workspace rows can show up on slightly
         different read snapshots in practice). Only ``APIError`` with the
         "no rows" code (PGRST116) is treated as transient — every other error
         re-raises immediately so tests fail fast.
      3. If ``workspace_count`` > 1, insert additional workspaces (with
         ``is_personal=false``) and matching owner ``workspace_members`` rows.
         The personal workspace is always first in the returned list.
      4. Sign in via the anon client to mint a fresh JWT (whose
         ``app_metadata.workspace_ids`` is populated by the
         ``trg_workspace_members_jwt_sync`` trigger).
      5. Return ``MintedUser(auth_user_id, profile_id, workspace_ids, jwt)``.
    """
    if workspace_count < 1:
        raise ValueError("workspace_count must be >= 1")

    service = get_v2_client()
    email = f"e2e-{uuid.uuid4().hex[:8]}@test.com"
    password = _DEFAULT_PASSWORD

    auth_resp = service.auth.admin.create_user(
        {
            "email": email,
            "password": password,
            "email_confirm": True,
        }
    )
    auth_user = getattr(auth_resp, "user", None) or auth_resp
    auth_user_id = uuid.UUID(str(auth_user.id))

    # Wait briefly for the auth -> profile -> personal-workspace trigger chain.
    # Narrow transient catch: only PGRST116 ("no rows") is retried; anything
    # else (auth, 401/403, 5xx) re-raises so the test fails fast.
    profile_id: uuid.UUID | None = None
    personal_ws_id: uuid.UUID | None = None
    deadline = time.monotonic() + 5.0
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if profile_id is None:
                profile_row = (
                    service.schema("core")
                    .table("profiles")
                    .select("id")
                    .eq("id", str(auth_user_id))
                    .maybe_single()
                    .execute()
                )
                if profile_row and profile_row.data:
                    profile_id = uuid.UUID(str(profile_row.data["id"]))
            if profile_id is not None and personal_ws_id is None:
                members = (
                    service.schema("core")
                    .table("workspace_members")
                    .select("workspace_id")
                    .eq("profile_id", str(profile_id))
                    .execute()
                )
                if members.data:
                    personal_ws_id = uuid.UUID(str(members.data[0]["workspace_id"]))
            if profile_id is not None and personal_ws_id is not None:
                break
        except APIError as exc:
            # PGRST116 = "no rows returned" from .maybe_single()/.single() — transient
            # while the trigger chain is still propagating. Anything else is permanent.
            if getattr(exc, "code", None) == "PGRST116":
                last_err = exc
            else:
                raise
        time.sleep(0.25)

    if profile_id is None or personal_ws_id is None:
        raise TimeoutError(
            f"Trigger handle_new_auth_user did not produce profile+workspace within 5s "
            f"for auth_user={auth_user_id} "
            f"(profile_found={profile_id is not None}, "
            f"workspace_found={personal_ws_id is not None}, last_err={last_err!r})"
        )

    workspace_ids: list[uuid.UUID] = [personal_ws_id]

    for i in range(1, workspace_count):
        new_ws = (
            service.schema("core")
            .table("workspaces")
            .insert(
                {
                    "owner_profile_id": str(profile_id),
                    "name": f"e2e-extra-{i}",
                    "is_personal": False,
                }
            )
            .execute()
        )
        if not new_ws.data:
            raise RuntimeError(f"failed to insert extra workspace #{i}")
        new_ws_id = uuid.UUID(str(new_ws.data[0]["id"]))
        (
            service.schema("core")
            .table("workspace_members")
            .insert(
                {
                    "workspace_id": str(new_ws_id),
                    "profile_id": str(profile_id),
                    "role": "owner",
                }
            )
            .execute()
        )
        workspace_ids.append(new_ws_id)

    anon = get_v2_anon_client()
    session_resp = anon.auth.sign_in_with_password(
        {"email": email, "password": password}
    )
    session = getattr(session_resp, "session", None)
    if session is None or not getattr(session, "access_token", None):
        raise RuntimeError(
            f"sign-in returned no session for {email} (resp={session_resp!r})"
        )
    jwt = session.access_token

    return MintedUser(
        auth_user_id=auth_user_id,
        profile_id=profile_id,
        workspace_ids=workspace_ids,
        jwt=jwt,
        email=email,
    )


def delete_test_user(auth_user_id: uuid.UUID) -> None:
    """Delete the auth user; ON DELETE CASCADE removes profile/workspaces/members."""
    service = get_v2_client()
    service.auth.admin.delete_user(str(auth_user_id))


