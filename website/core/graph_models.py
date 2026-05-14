"""Frontend-compatible graph payload models.

Pure presentation-layer Pydantic models that wrap the JSON shape the website
frontend reads from ``/api/graph``. Lifted out of
``website.core.supabase_kg.models`` so they can survive the v2 schema purge:
the kg_features cluster + the v2 repositories both consume these without any
old-schema dependency.

The models are deliberately schema-agnostic — they mirror the JSON contract
between backend and frontend, not any database table layout.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KGGraphNode(BaseModel):
    """Node in the frontend-compatible graph format."""

    id: str
    name: str
    group: str  # source_type mapped to group name
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    url: str
    date: str = ""  # ISO date string (primary key the frontend reads)
    node_date: str = ""  # alias of ``date`` — belt-and-suspenders for JS lookup
    # ``node.date || node.node_date || node.captured_at || node.created_at``
    owner: str | None = None  # display_name of the node creator (global view)
    contributors: int | None = None  # how many users captured this (global view)


class KGGraphLink(BaseModel):
    """Link in the frontend-compatible graph format."""

    source: str
    target: str
    relation: str
    weight: int | None = Field(
        default=None, ge=1, le=10, description="Link strength 1-10; null for auto-derived links"
    )
    link_type: str = Field(
        default="tag", description="Link origin: 'tag' | 'semantic' | 'entity'"
    )
    description: str | None = Field(
        default=None, description="Human-readable link description"
    )
    connection_strength: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalized strength used by /api/graph min_strength filtering",
    )


class KGGraph(BaseModel):
    """Full graph payload matching the frontend's expected structure."""

    nodes: list[KGGraphNode] = Field(default_factory=list)
    links: list[KGGraphLink] = Field(default_factory=list)
    total_nodes: int | None = None  # total count for pagination awareness


__all__ = ["KGGraphNode", "KGGraphLink", "KGGraph"]
