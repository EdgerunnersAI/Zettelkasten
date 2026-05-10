"""Typed Candidate models for the v2 retrieval pipeline.

Discriminated union of (Chunk, Entity, Doc) candidates per Microsoft GraphRAG /
LlamaIndex / Pinecone / Weaviate conventions: chunk + document + entity IDs
stay separate, never collapsed into a single string. ACL-001 sunset closed
2026-05-10 (commit 8.5.R4-cleanup) — the legacy ``node_id`` alias property +
``candidate_to_legacy_dict`` projector + ``default_rrf_score`` knob were
deleted after audit confirmed zero consumers in ``website/``.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# kind discriminator values
ChunkKind = Literal["chunk"]
EntityKind = Literal["entity"]
DocKind = Literal["doc"]

# score_kind — set at the CALL-SITE that invoked the producing RPC.
# Adding a new score_kind requires updating this Literal AND the call-site
# that produces it; never derive from the row contents heuristically.
ScoreKind = Literal["dense", "fts", "graph", "graph_seed", "rerank", "anchor_seed"]


class _CandidateBase(BaseModel):
    """Common fields across all Candidate variants.

    Every Candidate has a kind discriminator + a score + a score_kind set at
    the producing call-site. The ID fields are typed and nullable per kind:
    chunks always have canonical_chunk_id; entities always have kg_node_id;
    docs always have canonical_zettel_id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Source-typed identifiers — at least one must be set per the kind discriminator
    canonical_chunk_id: uuid.UUID | None = None
    canonical_zettel_id: uuid.UUID | None = None
    kg_node_id: int | None = None

    # Scoring
    score: float
    rrf_score: float = Field(
        ..., description="Cosmetic alias for score; RRF is rank-based per Cormack 2009."
    )
    score_kind: ScoreKind = Field(
        ..., description="Set at call-site, never derived heuristically from row contents."
    )

    # Optional fields downstream may consume
    fts_text: str = ""

    # Raw component scores from hybrid RPCs (e.g. hybrid_search_chunks_kasten).
    # Populated when the producing RPC returns them; ``score``/``rrf_score`` stay
    # the fused score per Cormack 2009 RRF semantics. Diagnostics + future
    # weight-tuning consume these without re-running fusion. Default ``None``
    # for back-compat with RPCs that only emit a single score.
    raw_dense_score: float | None = None
    raw_fts_score: float | None = None


class ChunkCandidate(_CandidateBase):
    """A retrieval candidate that points at a content.canonical_chunks row."""
    kind: ChunkKind = "chunk"
    canonical_chunk_id: uuid.UUID
    canonical_zettel_id: uuid.UUID
    chunk_idx: int | None = None
    content: str = ""


class EntityCandidate(_CandidateBase):
    """A retrieval candidate that points at a kg.kg_nodes row (typed entity)."""
    kind: EntityKind = "entity"
    kg_node_id: int
    title: str = ""
    entity_type: str | None = None


class DocCandidate(_CandidateBase):
    """A retrieval candidate that points at a content.canonical_zettels row."""
    kind: DocKind = "doc"
    canonical_zettel_id: uuid.UUID
    title: str = ""


# Discriminated union — Pydantic uses ``kind`` to select the variant on parsing
Candidate = Annotated[
    Union[ChunkCandidate, EntityCandidate, DocCandidate],
    Field(discriminator="kind"),
]


# ============================================================================
# Row → typed Candidate adapters
# ============================================================================

def chunk_from_v2_row(row: dict[str, Any], *, score_kind: ScoreKind) -> ChunkCandidate:
    """Adapt a v2 RPC chunk row into a typed ChunkCandidate.

    The caller passes ``score_kind`` because the call-site is the only place
    that knows which RPC produced the row. NEVER derive score_kind from the
    row contents (per LlamaIndex v0.10 break-cases).
    """
    score = float(row["score"]) if "score" in row else 0.0
    if score_kind == "fts":
        fts_text = row.get("fts_text", "") or row.get("content", "") or ""
    else:
        fts_text = ""
    raw_dense = row.get("raw_dense_score")
    raw_fts = row.get("raw_fts_score")
    return ChunkCandidate(
        canonical_chunk_id=uuid.UUID(str(row["canonical_chunk_id"])),
        canonical_zettel_id=uuid.UUID(str(row["canonical_zettel_id"])),
        chunk_idx=row.get("chunk_idx"),
        content=row.get("content", "") or "",
        score=score,
        rrf_score=score,
        score_kind=score_kind,
        fts_text=fts_text,
        raw_dense_score=float(raw_dense) if raw_dense is not None else None,
        raw_fts_score=float(raw_fts) if raw_fts is not None else None,
    )


def entity_from_v2_row(row: dict[str, Any], *, score_kind: ScoreKind) -> EntityCandidate:
    """Adapt a v2 KG row into a typed EntityCandidate. See chunk_from_v2_row notes."""
    score = float(row["score"]) if "score" in row else 0.0
    return EntityCandidate(
        kg_node_id=int(row["kg_node_id"]),
        title=row.get("title", "") or "",
        entity_type=row.get("entity_type"),
        score=score,
        rrf_score=score,
        score_kind=score_kind,
    )


def doc_from_v2_row(row: dict[str, Any], *, score_kind: ScoreKind) -> DocCandidate:
    """Adapt a v2 zettel row into a typed DocCandidate. See chunk_from_v2_row notes."""
    score = float(row["score"]) if "score" in row else 0.0
    return DocCandidate(
        canonical_zettel_id=uuid.UUID(str(row["canonical_zettel_id"])),
        title=row.get("title", "") or "",
        score=score,
        rrf_score=score,
        score_kind=score_kind,
    )
