"""Sandbox and node-picker routes for the user-level RAG experience."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from website.api.auth import get_current_user
from website.features.rag_pipeline.service import get_rag_runtime
from website.features.rag_pipeline.types import SourceType

logger = logging.getLogger("website.api.sandbox_routes")

router = APIRouter(prefix="/api/rag", tags=["rag-sandboxes"])


class SandboxCreateRequest(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = "stack"
    color: str | None = "#14b8a6"
    default_quality: str = "fast"

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
    runtime = _runtime_for_user(user)
    rows = await runtime.sandboxes.list_sandboxes(runtime.kg_user_id, limit=limit)
    return {"sandboxes": [_serialize_sandbox(row) for row in rows]}


@router.post("/sandboxes")
async def create_sandbox(
    body: SandboxCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    runtime = _runtime_for_user(user)
    row = await runtime.sandboxes.create_sandbox(
        user_id=runtime.kg_user_id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        color=body.color,
        default_quality=body.default_quality,
    )
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
    runtime = _runtime_for_user(user)
    deleted = await runtime.sandboxes.delete_sandbox(sandbox_id, runtime.kg_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return {"status": "ok", "sandbox_id": str(sandbox_id)}


@router.post("/sandboxes/{sandbox_id}/members")
async def add_members(
    sandbox_id: UUID,
    body: SandboxMemberAddRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
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
    runtime = _runtime_for_user(user)
    sandbox = await runtime.sandboxes.get_sandbox(sandbox_id, runtime.kg_user_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    if not any([body.node_ids, body.tags, body.source_types]):
        raise HTTPException(status_code=400, detail="At least one filter is required")

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

