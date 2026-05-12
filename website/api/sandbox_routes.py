"""Sandbox and node-picker routes for the user-level RAG experience.

Phase 4.4 (DB v2 purge) — kasten CRUD dual-path:
 * list/create/delete kastens, list-zettels-in-kasten, and add-zettels-to-kasten
   route to ``rag.kastens`` + ``rag.kasten_zettels`` (via ``bulk_add_to_kasten``
   / ``list_kasten_zettels`` RPCs) when DB v2 is on AND the JWT subject is a
   Supabase Auth UUID with a default workspace.
 * Tag- or source-type-filtered ``add_members`` requests intentionally fall
   back to the v1 path because the v2 ``bulk_add_to_kasten`` RPC only accepts
   an explicit ``workspace_zettel_id`` array.
 * Kasten member-sharing (inviting other workspaces to a kasten via
   ``rag.kasten_members``) is wired through the v2-only POST /share endpoint
   (Phase 7.2-deferred). RLS on ``rag.kastens`` + ``rag.kasten_zettels`` is
   extended through the ``kasten_members`` join in ``_v2/29``.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from website.api.auth import get_current_user
from website.core.db_version import use_supabase_v2
from website.core.persist import get_supabase_v2_scope
from website.core.supabase_v2.repositories.rag_repository import RAGRepository as V2RAGRepository
from website.features.rag_pipeline.service import get_rag_runtime
from website.features.rag_pipeline.types import SourceType
from website.features.user_pricing.entitlements import consume_entitlement, require_entitlement
from website.features.user_pricing.models import Meter

logger = logging.getLogger("website.api.sandbox_routes")

router = APIRouter(prefix="/api/rag", tags=["rag-sandboxes"])


def _is_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _v2_scope_for(user: dict) -> tuple[V2RAGRepository, UUID, UUID] | None:
    """Return ``(rag_repo, profile_id, workspace_id)`` when v2 dual-path applies.

    Phase 4.4 dual-path gate: requires DB v2 ON + UUID auth subject + a default
    workspace via the standard ``get_supabase_v2_scope`` lookup. Returns None
    otherwise so callers fall back to the legacy v1 path unchanged.
    """
    if not use_supabase_v2() or user is None:
        return None
    if not _is_uuid(user.get("sub")):
        return None
    scope = get_supabase_v2_scope(user["sub"])
    if scope is None:
        return None
    _content_repo, profile_id, workspace_id = scope
    return V2RAGRepository(), profile_id, workspace_id


def _resolve_caller_workspace_for_kasten(
    user: dict, kasten_id: UUID
) -> tuple[V2RAGRepository, UUID]:
    """Resolve caller's workspace_id and assert kasten ownership.

    BOLA-mitigation helper used by the kasten member-mutation endpoints
    (remove_member, bulk_remove_members). The auth subject's default
    workspace is treated as the caller's acting workspace; ownership is
    proven by ``rag_repo.get_kasten`` (workspace_id-keyed read returns None
    when the workspace does not own the kasten — equivalent to the share
    handler's ownership check). Raises 403 in any failure mode so the
    response cannot reveal whether the kasten exists in another tenant.

    Returns ``(rag_repo, workspace_id)`` on success.
    """
    scope = _v2_scope_for(user)
    if scope is None:
        # Caller has no v2 workspace; the v2-only delete paths cannot proceed
        # safely. 403 (vs. 404) keeps the response identical for "kasten does
        # not exist" and "caller is not the owner" — the share handler uses
        # the same conservative pattern.
        raise HTTPException(status_code=403, detail="Forbidden")
    rag_repo, _profile_id, workspace_id = scope
    if rag_repo.get_kasten(kasten_id, workspace_id) is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    return rag_repo, workspace_id


def _serialize_kasten_zettel_v2(row: dict) -> dict:
    """Map a row from ``rag.list_kasten_zettels`` RPC into the legacy member shape.

    The RPC returns a JOIN of workspace_zettels + canonical_zettels; the v1
    client surface expects ``{node_id, added_via, added_filter, added_at, node:{...}}``
    so we reshape conservatively. Any column the RPC doesn't return defaults to
    a safe empty value rather than a 500.
    """
    wz_id = str(row.get("workspace_zettel_id") or row.get("id") or "")
    return {
        "node_id": wz_id,
        "added_via": row.get("added_via") or "manual",
        "added_filter": row.get("added_filter") or {},
        "added_at": row.get("added_at"),
        "node": {
            "id": wz_id,
            "name": row.get("title") or wz_id,
            "source_type": row.get("source_type") or "web",
            "url": row.get("normalized_url") or row.get("url") or "",
            "summary": row.get("ai_summary") or row.get("summary") or "",
            "tags": row.get("user_tags") or row.get("tags") or [],
            "node_date": row.get("publication_date") or row.get("node_date"),
        },
    }


def _serialize_kasten_v2(row: dict) -> dict:
    """Serialise a ``rag.kastens`` row in the legacy sandbox shape.

    Member count is not part of the v2 row — it is computed lazily by
    :func:`_kasten_member_count_v2` only when the response shape requires it
    (the list endpoint surfaces ``member_count`` in the UI).
    """
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description") or "",
        "icon": row.get("icon") or "stack",
        "color": row.get("color") or "#14b8a6",
        "default_quality": row.get("default_quality", "fast"),
        "member_count": row.get("member_count", 0),
        "last_used_at": row.get("last_used_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


class SandboxCreateRequest(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = "stack"
    color: str | None = "#14b8a6"
    default_quality: str = "fast"
    client_action_id: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name is required")
        if len(cleaned) > 80:
            raise ValueError("name is too long")
        return cleaned

    @field_validator("default_quality")
    @classmethod
    def validate_quality(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"fast", "high"}:
            raise ValueError("default_quality must be fast or high")
        return normalized


class SandboxUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    default_quality: str | None = None


class KastenShareRequest(BaseModel):
    """Request body for POST /api/rag/sandboxes/{id}/share — v2 kasten member-sharing.

    Phase 7.2-deferred: workspace-keyed sharing (rag.kasten_members is keyed
    by workspace_id, not profile_id, per the v2 design — sharing a kasten with
    another tenant means adding their workspace as a member). Defaults role to
    'viewer' if omitted.
    """

    workspace_id: UUID
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"viewer", "editor", "owner"}:
            raise ValueError("role must be viewer, editor, or owner")
        return normalized


class SandboxMemberAddRequest(BaseModel):
    node_ids: list[str] | None = None
    tags: list[str] | None = None
    tag_mode: str = "all"
    source_types: list[SourceType] | None = None
    added_via: str = "manual"

    @field_validator("tag_mode")
    @classmethod
    def validate_tag_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"all", "any"}:
            raise ValueError("tag_mode must be all or any")
        return normalized


class SandboxMemberRemoveRequest(BaseModel):
    node_ids: list[str] | None = None
    tags: list[str] | None = None
    tag_mode: str = "all"
    source_types: list[SourceType] | None = None

    @field_validator("tag_mode")
    @classmethod
    def validate_tag_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"all", "any"}:
            raise ValueError("tag_mode must be all or any")
        return normalized


def _runtime_for_user(user: dict):
    try:
        return get_rag_runtime(user["sub"])
    except Exception as exc:
        logger.warning("Sandbox runtime unavailable for %s: %s", user.get("sub"), exc)
        raise HTTPException(status_code=503, detail="RAG runtime is not available")


def _serialize_sandbox(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description") or "",
        "icon": row.get("icon") or "stack",
        "color": row.get("color") or "#14b8a6",
        "default_quality": row.get("default_quality", "fast"),
        "member_count": row.get("member_count", 0),
        "last_used_at": row.get("last_used_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _serialize_member(row: dict) -> dict:
    # v2 shape: rag.list_kasten_zettels returns a flat row with
    # workspace_zettel_id, canonical_zettel_id, title, source_type,
    # user_tags, ai_summary, added_at. The legacy nested ``kg_nodes``
    # embed (Phase 8.0 H9) is gone; map flat columns into the
    # response envelope clients still expect.
    member_id = row.get("workspace_zettel_id") or row.get("node_id") or ""
    return {
        "node_id": member_id,
        "added_via": row.get("added_via", "manual"),
        "added_filter": row.get("added_filter") or {},
        "added_at": row.get("added_at"),
        "node": {
            "id": member_id,
            "name": row.get("title") or member_id,
            "source_type": row.get("source_type") or "web",
            "url": row.get("url") or "",
            "summary": row.get("ai_summary") or "",
            "tags": row.get("user_tags") or [],
            "node_date": row.get("node_date"),
        },
    }


def _serialize_node(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "source_type": row.source_type,
        "summary": row.summary,
        "tags": row.tags,
        "url": row.url,
        "node_date": str(row.node_date or ""),
    }


def _member_matches_filters(row: dict, body: SandboxMemberRemoveRequest) -> bool:
    # v2: rag.list_kasten_zettels returns flat columns; the legacy
    # nested ``kg_nodes`` embed (Phase 8.0 H9) is gone. Filters now
    # match against workspace_zettel_id, user_tags, source_type
    # directly off the row.
    member_id = row.get("workspace_zettel_id") or row.get("node_id")
    if body.node_ids and member_id not in body.node_ids:
        return False

    node_tags = {tag.lower() for tag in row.get("user_tags") or []}
    requested_tags = [tag.lower() for tag in body.tags or []]
    if requested_tags:
        if body.tag_mode == "all":
            if not all(tag in node_tags for tag in requested_tags):
                return False
        elif not any(tag in node_tags for tag in requested_tags):
            return False

    if body.source_types:
        allowed = {item.value for item in body.source_types}
        if (row.get("source_type") or "").lower() not in allowed:
            return False

    return True


@router.get("/nodes")
async def list_user_nodes(
    user: Annotated[dict, Depends(get_current_user)],
    query: str | None = None,
    tags: list[str] = Query(default=[]),
    source_types: list[SourceType] = Query(default=[]),
    limit: int = 100,
    offset: int = 0,
):
    runtime = _runtime_for_user(user)
    rows = runtime.repo.search_nodes(
        runtime.kg_user_id,
        query=query,
        tags=tags or None,
        source_types=[item.value for item in source_types] or None,
        limit=limit,
        offset=offset,
    )
    return {"nodes": [_serialize_node(row) for row in rows]}


@router.get("/sandboxes")
async def list_sandboxes(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 50,
):
    # Phase 4.4 v2 dual-path: read kastens from rag.kastens scoped to the
    # authenticated user's default workspace.
    v2 = _v2_scope_for(user)
    if v2 is not None:
        rag_repo, _profile_id, workspace_id = v2
        try:
            rows = rag_repo.list_kastens(workspace_id, limit=limit)
            return {"sandboxes": [_serialize_kasten_v2(row) for row in rows]}
        except Exception as exc:  # noqa: BLE001 — surface to logs, fall back to v1
            logger.warning("v2 list_kastens failed, falling back to v1: %s", exc)

    runtime = _runtime_for_user(user)
    rows = await runtime.sandboxes.list_sandboxes(runtime.kg_user_id, limit=limit)
    return {"sandboxes": [_serialize_sandbox(row) for row in rows]}


@router.post("/sandboxes")
async def create_sandbox(
    body: SandboxCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    action_id = body.client_action_id or body.name
    await require_entitlement(Meter.KASTEN, user, action_id=action_id)

    # Phase 4.4 v2 dual-path: write to rag.kastens via the v2 RAGRepository.
    # The repo does not accept owner_profile_id — the workspace_id IS the
    # tenancy boundary; kasten ownership is handled at the schema layer.
    v2 = _v2_scope_for(user)
    if v2 is not None:
        rag_repo, _profile_id, workspace_id = v2
        try:
            row = rag_repo.create_kasten(
                workspace_id=workspace_id,
                name=body.name,
                description=body.description,
                icon=body.icon,
                color=body.color,
                default_quality=body.default_quality,
            )
        except Exception as exc:  # noqa: BLE001 — surface real driver error to logs + client
            detail_str = str(exc)
            logger.exception(
                "v2 create_kasten failed for workspace=%s name=%s: %s",
                workspace_id,
                body.name,
                detail_str,
            )
            lower = detail_str.lower()
            if "duplicate key" in lower or "unique" in lower:
                raise HTTPException(status_code=409, detail="A kasten with that name already exists") from exc
            raise HTTPException(status_code=500, detail="Create sandbox failed. Please try again.") from exc

        if row is None:
            logger.error("v2 create_kasten returned None for workspace=%s name=%s", workspace_id, body.name)
            raise HTTPException(status_code=500, detail="Create sandbox returned no row")

        await consume_entitlement(Meter.KASTEN, user, action_id=action_id)
        return {"sandbox": _serialize_kasten_v2(row)}

    runtime = _runtime_for_user(user)
    try:
        row = await runtime.sandboxes.create_sandbox(
            user_id=runtime.kg_user_id,
            name=body.name,
            description=body.description,
            icon=body.icon,
            color=body.color,
            default_quality=body.default_quality,
        )
    except Exception as exc:  # noqa: BLE001 — surface the real driver error to logs + client
        detail_str = str(exc)
        logger.exception(
            "create_sandbox failed for user=%s name=%s: %s",
            runtime.kg_user_id,
            body.name,
            detail_str,
        )
        lower = detail_str.lower()
        # Duplicate-name hits Postgres UNIQUE(user_id, name)
        if "duplicate key" in lower or "unique" in lower:
            raise HTTPException(status_code=409, detail="A kasten with that name already exists") from exc
        # Missing migration — PostgREST schema cache indicates the table is unknown
        if "pgrst205" in lower or "schema cache" in lower or "could not find the table" in lower:
            raise HTTPException(
                status_code=503,
                detail="Kastens backend is not fully provisioned. Please try again shortly.",
            ) from exc
        raise HTTPException(status_code=500, detail="Create sandbox failed. Please try again.") from exc

    if row is None:
        logger.error("create_sandbox returned None row for user=%s name=%s", runtime.kg_user_id, body.name)
        raise HTTPException(status_code=500, detail="Create sandbox returned no row")

    await consume_entitlement(Meter.KASTEN, user, action_id=action_id)
    return {"sandbox": _serialize_sandbox(row)}


@router.get("/sandboxes/{sandbox_id}")
async def get_sandbox(
    sandbox_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    row = await runtime.sandboxes.get_sandbox(sandbox_id, runtime.kg_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    members = await runtime.sandboxes.list_members(sandbox_id, runtime.kg_user_id)
    return {
        "sandbox": _serialize_sandbox(row),
        "members": [_serialize_member(member) for member in members],
    }


@router.get("/sandboxes/{sandbox_id}/members")
async def list_members(
    sandbox_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 500,
):
    # Phase 4.4 v2 dual-path: list zettels via rag.list_kasten_zettels RPC.
    v2 = _v2_scope_for(user)
    if v2 is not None:
        rag_repo, _profile_id, workspace_id = v2
        try:
            kasten = rag_repo.get_kasten(sandbox_id, workspace_id)
            if kasten is None:
                raise HTTPException(status_code=404, detail="Sandbox not found")
            rows = rag_repo.list_kasten_zettels(sandbox_id)
            return {"members": [_serialize_kasten_zettel_v2(row) for row in rows]}
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — surface to logs, fall back to v1
            logger.warning("v2 list_kasten_zettels failed, falling back to v1: %s", exc)

    runtime = _runtime_for_user(user)
    sandbox = await runtime.sandboxes.get_sandbox(sandbox_id, runtime.kg_user_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    members = await runtime.sandboxes.list_members(sandbox_id, runtime.kg_user_id, limit=limit)
    return {"members": [_serialize_member(member) for member in members]}


@router.patch("/sandboxes/{sandbox_id}")
async def update_sandbox(
    sandbox_id: UUID,
    body: SandboxUpdateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    row = await runtime.sandboxes.update_sandbox(
        sandbox_id,
        runtime.kg_user_id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        color=body.color,
        default_quality=body.default_quality,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return {"sandbox": _serialize_sandbox(row)}


@router.delete("/sandboxes/{sandbox_id}")
async def delete_sandbox(
    sandbox_id: UUID,
    user: Annotated[dict, Depends(get_current_user)],
):
    # Phase 4.4 v2 dual-path: delete from rag.kastens scoped to workspace_id;
    # cascading FKs in the schema clean up rag.kasten_zettels / kasten_members.
    v2 = _v2_scope_for(user)
    if v2 is not None:
        rag_repo, _profile_id, workspace_id = v2
        try:
            deleted = rag_repo.delete_kasten(sandbox_id, workspace_id)
        except Exception as exc:  # noqa: BLE001 — surface to logs, fall back to v1
            logger.warning("v2 delete_kasten failed, falling back to v1: %s", exc)
        else:
            if not deleted:
                raise HTTPException(status_code=404, detail="Sandbox not found")
            return {"status": "ok", "sandbox_id": str(sandbox_id)}

    runtime = _runtime_for_user(user)
    deleted = await runtime.sandboxes.delete_sandbox(sandbox_id, runtime.kg_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return {"status": "ok", "sandbox_id": str(sandbox_id)}


@router.post("/sandboxes/{sandbox_id}/share")
async def share_kasten(
    sandbox_id: UUID,
    body: KastenShareRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Add a recipient workspace as a member of a kasten (v2-only).

    Phase 7.2-deferred (closes the deferral from Phase 4.4): kasten member-
    sharing routes through ``rag.kasten_members``. The granter must be acting
    from a workspace that holds the kasten's owner row — enforced by the
    ``rag.assert_kasten_owner_can_grant`` trigger; recipient SELECT access is
    granted by the ``kastens_member_or_owner_select`` /
    ``kasten_zettels_member_or_owner_select`` policies in ``_v2/29``.
    """
    v2 = _v2_scope_for(user)
    if v2 is None:
        # v2-only feature (workspace-keyed sharing has no v1 equivalent — v1
        # rag_sandbox_members was profile-keyed). Surface a clear 501 rather
        # than a misleading fall-through to a non-existent v1 path.
        raise HTTPException(
            status_code=501,
            detail="Kasten sharing requires DB v2",
        )
    rag_repo, _profile_id, workspace_id = v2
    kasten = rag_repo.get_kasten(sandbox_id, workspace_id)
    if kasten is None:
        # 404 covers both 'kasten does not exist' and 'caller's workspace does
        # not own the kasten'. Either way, the caller cannot grant.
        raise HTTPException(status_code=404, detail="Sandbox not found")
    try:
        rag_repo.add_kasten_member(
            kasten_id=sandbox_id,
            workspace_id=body.workspace_id,
            role=body.role,
        )
    except Exception as exc:  # noqa: BLE001 — surface real driver error to logs + client
        logger.exception(
            "v2 add_kasten_member failed for kasten=%s recipient_workspace=%s role=%s: %s",
            sandbox_id,
            body.workspace_id,
            body.role,
            exc,
        )
        # The trigger raises 'only kasten owners can grant memberships' (P0001)
        # for non-owner granters; surface as 403 so callers can distinguish
        # auth failures from generic 500s.
        msg = str(exc).lower()
        if "only kasten owners" in msg or "p0001" in msg:
            raise HTTPException(status_code=403, detail="Only kasten owners can grant memberships") from exc
        raise HTTPException(status_code=500, detail="Share kasten failed.") from exc
    return {
        "status": "ok",
        "kasten_id": str(sandbox_id),
        "workspace_id": str(body.workspace_id),
        "role": body.role,
    }


@router.post("/sandboxes/{sandbox_id}/members")
async def add_members(
    sandbox_id: UUID,
    body: SandboxMemberAddRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    # Phase 4.4 v2 dual-path: take the simplest case only — an explicit list of
    # UUID-shaped node_ids (i.e. workspace_zettel_ids). Tag- or source-type-
    # filtered adds fall back to v1 because the v2 RPC `bulk_add_to_kasten`
    # only accepts an explicit workspace_zettel_id array.
    v2 = _v2_scope_for(user)
    use_v2_add = (
        v2 is not None
        and bool(body.node_ids)
        and not body.tags
        and not body.source_types
        and all(_is_uuid(nid) for nid in body.node_ids)
    )
    if use_v2_add:
        rag_repo, _profile_id, workspace_id = v2  # type: ignore[misc]
        kasten = rag_repo.get_kasten(sandbox_id, workspace_id)
        if kasten is None:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        try:
            wz_ids = [UUID(nid) for nid in body.node_ids]
            added = rag_repo.add_zettels_to_kasten(
                kasten_id=sandbox_id,
                workspace_zettel_ids=wz_ids,
            )
        except Exception as exc:  # noqa: BLE001 — surface real driver error to logs + client
            logger.exception(
                "v2 bulk_add_to_kasten failed for kasten=%s workspace=%s: %s",
                sandbox_id,
                workspace_id,
                exc,
            )
            raise HTTPException(status_code=500, detail="Add to kasten failed.") from exc
        rows = rag_repo.list_kasten_zettels(sandbox_id)
        return {
            "status": "ok",
            "added_count": added,
            "members": [_serialize_kasten_zettel_v2(row) for row in rows],
        }

    runtime = _runtime_for_user(user)
    sandbox = await runtime.sandboxes.get_sandbox(sandbox_id, runtime.kg_user_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    added = await runtime.sandboxes.add_members(
        sandbox_id=sandbox_id,
        user_id=runtime.kg_user_id,
        node_ids=body.node_ids,
        tags=body.tags,
        tag_mode=body.tag_mode,
        source_types=[item.value for item in body.source_types] if body.source_types else None,
        added_via=body.added_via,
    )
    # Post-iter-06 guard: when the caller passes explicit node_ids, every id
    # must land in rag_sandbox_members. A short count signals the silent-no-op
    # regression fixed by 2026-04-26_fix_rag_bulk_add_to_sandbox.sql.
    if body.node_ids:
        requested_node_ids = body.node_ids
        if added != len(requested_node_ids):
            raise HTTPException(
                500,
                detail=f"Sandbox bulk-add silently dropped rows: requested={len(requested_node_ids)} added={added}",
            )
    members = await runtime.sandboxes.list_members(sandbox_id, runtime.kg_user_id)
    return {
        "status": "ok",
        "added_count": added,
        "members": [_serialize_member(member) for member in members],
    }


@router.delete("/sandboxes/{sandbox_id}/members/{node_id}")
async def remove_member(
    sandbox_id: UUID,
    node_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    # BOLA fix: resolve caller's workspace + verify kasten ownership BEFORE
    # the delete. Without this, the legacy ``runtime.kg_user_id`` (auth UUID,
    # NOT workspace_id) was forwarded to a service-role DELETE keyed only on
    # (kasten_id, workspace_zettel_id) — letting any authenticated user
    # remove members from any kasten.
    if _v2_scope_for(user) is not None:
        rag_repo, workspace_id = _resolve_caller_workspace_for_kasten(user, sandbox_id)
        try:
            wz_id = UUID(node_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid node_id") from exc
        removed = rag_repo.remove_zettel_from_kasten(
            kasten_id=sandbox_id,
            workspace_zettel_id=wz_id,
            workspace_id=workspace_id,
        )
        if not removed:
            raise HTTPException(status_code=404, detail="Sandbox member not found")
        return {"status": "ok", "node_id": node_id}

    runtime = _runtime_for_user(user)
    removed = await runtime.sandboxes.remove_member(sandbox_id, runtime.kg_user_id, node_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Sandbox member not found")
    return {"status": "ok", "node_id": node_id}


@router.delete("/sandboxes/{sandbox_id}/members")
async def bulk_remove_members(
    sandbox_id: UUID,
    body: SandboxMemberRemoveRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    if not any([body.node_ids, body.tags, body.source_types]):
        raise HTTPException(status_code=400, detail="At least one filter is required")

    # BOLA fix: same as remove_member above. Resolve real workspace_id and
    # verify ownership before any delete; never trust ``runtime.kg_user_id``
    # as a workspace key.
    if _v2_scope_for(user) is not None:
        rag_repo, workspace_id = _resolve_caller_workspace_for_kasten(user, sandbox_id)
        # Use the v2 store wired with workspace_id so the BOLA gate fires
        # inside the repo as well (defense in depth).
        runtime = _runtime_for_user(user)
        members = await runtime.sandboxes.list_members(sandbox_id, workspace_id, limit=1000)
        matched_node_ids = [
            member.get("workspace_zettel_id") or member.get("node_id")
            for member in members
            if _member_matches_filters(member, body)
        ]
        matched_wz_ids: list[UUID] = []
        for mid in matched_node_ids:
            if not mid:
                continue
            try:
                matched_wz_ids.append(UUID(str(mid)))
            except (TypeError, ValueError):
                continue
        removed_count = rag_repo.remove_zettels_from_kasten(
            kasten_id=sandbox_id,
            workspace_zettel_ids=matched_wz_ids,
            workspace_id=workspace_id,
        )
        updated_members = await runtime.sandboxes.list_members(sandbox_id, workspace_id, limit=1000)
        return {
            "status": "ok",
            "removed_count": removed_count,
            "members": [_serialize_member(member) for member in updated_members],
        }

    runtime = _runtime_for_user(user)
    sandbox = await runtime.sandboxes.get_sandbox(sandbox_id, runtime.kg_user_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    members = await runtime.sandboxes.list_members(sandbox_id, runtime.kg_user_id, limit=1000)
    # v2 (Phase 8.0 H9): membership rows expose ``workspace_zettel_id``;
    # fall back to legacy ``node_id`` if a future caller still emits it.
    matched_node_ids = [
        member.get("workspace_zettel_id") or member.get("node_id")
        for member in members
        if _member_matches_filters(member, body)
    ]
    matched_node_ids = [mid for mid in matched_node_ids if mid]
    removed_count = await runtime.sandboxes.remove_members(sandbox_id, runtime.kg_user_id, matched_node_ids)
    updated_members = await runtime.sandboxes.list_members(sandbox_id, runtime.kg_user_id, limit=1000)
    return {
        "status": "ok",
        "removed_count": removed_count,
        "members": [_serialize_member(member) for member in updated_members],
    }

