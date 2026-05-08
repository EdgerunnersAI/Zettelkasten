"""Pydantic models for the DB v2 repository layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class CanonicalZettelCreate(BaseModel):
    normalized_url: str
    content_hash: bytes
    source_type: str
    title: str | None = None
    body_md: str | None = None
    publication_date: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalChunkCreate(BaseModel):
    chunk_idx: int
    content: str
    content_hash: bytes
    chunk_type: str = "semantic"
    start_offset: int | None = None
    end_offset: int | None = None
    token_count: int | None = None
    embedding: list[float] | None = None
    embedding_model_version: str = "gemini-001-mrl-768"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceZettelCreate(BaseModel):
    workspace_id: UUID
    ai_summary: str | None = None
    ai_summary_engine_version: str | None = None
    user_tags: list[str] = Field(default_factory=list)
    user_note: str | None = None
    pinned: bool = False
    added_via: str = "website"


class CanonicalUpsertResult(BaseModel):
    canonical_zettel_id: UUID
    workspace_zettel_id: UUID | None = None
    was_new: bool


class QuotaDebitRequest(BaseModel):
    workspace_id: UUID
    feature: str
    unit: str
    period_start: datetime


class SearchChunkResult(BaseModel):
    chunk_id: UUID
    canonical_zettel_id: UUID
    content: str
    score: float


class ScorerConfig(BaseModel):
    environment: str
    scorer_name: str
    version_id: str
    enabled: bool
    weight: float
    params: dict[str, Any] = Field(default_factory=dict)

