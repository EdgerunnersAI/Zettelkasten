"""Pydantic models for Supabase knowledge graph data."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── User ─────────────────────────────────────────────────────────────────────

class KGUser(BaseModel):
    """A knowledge-graph user (maps to Render auth)."""
    id: UUID
    render_user_id: str
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KGUserCreate(BaseModel):
    """Fields for creating a new KG user."""
    render_user_id: str
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None


# ── Node ─────────────────────────────────────────────────────────────────────

class KGNode(BaseModel):
    """A knowledge-graph node (persisted in Supabase)."""
    id: str
    user_id: UUID
    name: str
    source_type: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str
    node_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary_v2: dict[str, Any] | None = None
    extraction_confidence: str | None = None
    engine_version: str | None = None
    embedding: list[float] | None = Field(default=None, description="Semantic embedding vector (768-dim)")
    # iter-12 R5: LLM-generated entity aliases + hash for idempotent re-gen
    aliases: list[str] = Field(default_factory=list)
    summary_hash: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("embedding", mode="before")
    @classmethod
    def _parse_pgvector_string(cls, v: Any) -> list[float] | None:
        """Supabase returns pgvector columns as strings like '[0.1,0.2,...]'."""
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


class KGNodeCreate(BaseModel):
    """Fields for creating a new KG node."""
    id: str
    name: str
    source_type: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str
    node_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary_v2: dict[str, Any] | None = None
    extraction_confidence: str | None = None
    engine_version: str | None = None
    embedding: list[float] | None = Field(default=None, description="Semantic embedding vector (768-dim)")
    # iter-12 R5: LLM-generated entity aliases + hash for idempotent re-gen
    aliases: list[str] = Field(default_factory=list)
    summary_hash: str | None = None


# ── Link ─────────────────────────────────────────────────────────────────────

class KGLink(BaseModel):
    """An edge between two KG nodes."""
    id: UUID
    user_id: UUID
    source_node_id: str
    target_node_id: str
    relation: str
    weight: int | None = Field(default=None, ge=1, le=10, description="Link strength 1-10; null for auto-derived links")
    link_type: str = Field(default="tag", description="Link origin: 'tag' | 'semantic' | 'entity'")
    description: str | None = Field(default=None, description="Human-readable link description")
    created_at: datetime | None = None


class KGLinkCreate(BaseModel):
    """Fields for creating a new KG link."""
    source_node_id: str
    target_node_id: str
    relation: str
    weight: int | None = Field(default=None, ge=1, le=10, description="Link strength 1-10; null for auto-derived links")
    link_type: str = Field(default="tag", description="Link origin: 'tag' | 'semantic' | 'entity'")
    description: str | None = Field(default=None, description="Human-readable link description")


# ── Graph (aggregate) — relocated to website.core.graph_models ───────────────
# These presentation-layer models live in ``website.core.graph_models`` so they
# survive the v2 schema purge. Re-exported here for back-compat with code that
# still imports from ``website.core.supabase_kg.models``. Update those imports
# to ``from website.core.graph_models import KGGraph, ...`` and this shim can
# be removed when supabase_kg is retired.
from website.core.graph_models import KGGraph, KGGraphLink, KGGraphNode  # noqa: F401, E402
