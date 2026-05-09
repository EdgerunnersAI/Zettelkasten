"""Typed Candidate models for the v2 retrieval pipeline.

This module is the v2-purge "Anti-Corruption Layer" boundary (per Microsoft
Azure Architecture Center / AWS Prescriptive Guidance ACL pattern). It accepts
v2 RPC row shapes from the repository layer and projects them into the
discriminated-union Candidate types that downstream scoring/dedup/bandit code
will eventually consume natively.

Industry pattern references (verified during operator decision 2026-05-09):
- Microsoft GraphRAG: chunks carry id + chunk_id + document_id; entities are
  reconciled by deterministic (title, type) keys.
- LlamaIndex: TextNode.node_id + ref_doc_id keep document and chunk IDs
  separate.
- Pinecone: composite ID convention but typed parts remain in metadata.
- Weaviate: typed cross-references, never collapsed.

The legacy v1 surface used a single ``node_id`` text key for chunks, entities,
and documents indistinguishably. We preserve a back-compat ``node_id`` property
on every Candidate variant so the ``_dedup_and_fuse``-style consumers keep
working. Phase 7 will flip them to native typed access; THIS COMMIT IS THE
FOUNDATION ONLY.

TECH DEBT — sunset trigger: when every consumer in
``website/features/rag_pipeline/`` accesses Candidate fields by name (not via
``.node_id`` alias), delete the ``node_id`` property + ``candidate_to_legacy_dict``
helper below. Tracked in ``docs/db-v2/tech-debt-tracker.md`` (ACL-001).
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

    # ====================================================================
    # Back-compat alias (TECH DEBT — sunset when downstream uses typed access)
    # ====================================================================
    @property
    def node_id(self) -> str:
        """Legacy ``node_id`` alias.

        Returns canonical_chunk_id (chunks), kg_node_id (entities), or
        canonical_zettel_id (docs) as a string. DO NOT add new code that
        depends on this — see TECH DEBT note in module docstring.
        """
        # Subclasses override; this is unreachable except in malformed state.
        raise NotImplementedError("Subclass must implement node_id alias")


class ChunkCandidate(_CandidateBase):
    """A retrieval candidate that points at a content.canonical_chunks row."""
    kind: ChunkKind = "chunk"
    canonical_chunk_id: uuid.UUID
    canonical_zettel_id: uuid.UUID
    chunk_idx: int | None = None
    content: str = ""

    @property
    def node_id(self) -> str:
        return str(self.canonical_chunk_id)


class EntityCandidate(_CandidateBase):
    """A retrieval candidate that points at a kg.kg_nodes row (typed entity)."""
    kind: EntityKind = "entity"
    kg_node_id: int
    title: str = ""
    entity_type: str | None = None

    @property
    def node_id(self) -> str:
        return str(self.kg_node_id)


class DocCandidate(_CandidateBase):
    """A retrieval candidate that points at a content.canonical_zettels row."""
    kind: DocKind = "doc"
    canonical_zettel_id: uuid.UUID
    title: str = ""

    @property
    def node_id(self) -> str:
        return str(self.canonical_zettel_id)


# Discriminated union — Pydantic uses ``kind`` to select the variant on parsing
Candidate = Annotated[
    Union[ChunkCandidate, EntityCandidate, DocCandidate],
    Field(discriminator="kind"),
]


# ============================================================================
# ACL adapter helpers — convert v2 RPC rows to dict-shaped legacy rows
# ============================================================================

def candidate_to_legacy_dict(c: Candidate) -> dict[str, Any]:
    """Project a typed Candidate to the legacy v1 dict shape.

    Used at the repository boundary while downstream consumers
    (``_dedup_and_fuse``, RRF fusion, bandit) still expect dict access.
    Phase 7 hardening will delete this projection.
    """
    base: dict[str, Any] = {
        "node_id": c.node_id,
        "score": c.score,
        "rrf_score": c.rrf_score,
        "score_kind": c.score_kind,
        "fts_text": c.fts_text,
        "kind": c.kind,
        # typed triple — exposed for forward-compatible code
        "canonical_chunk_id": str(c.canonical_chunk_id) if c.canonical_chunk_id else None,
        "canonical_zettel_id": str(c.canonical_zettel_id) if c.canonical_zettel_id else None,
        "kg_node_id": c.kg_node_id,
    }
    if isinstance(c, ChunkCandidate):
        base["chunk_idx"] = c.chunk_idx
        base["content"] = c.content
    elif isinstance(c, EntityCandidate):
        base["title"] = c.title
        base["entity_type"] = c.entity_type
    elif isinstance(c, DocCandidate):
        base["title"] = c.title
    return base


def chunk_from_v2_row(
    row: dict[str, Any], *, score_kind: ScoreKind, default_rrf_score: float | None = None
) -> ChunkCandidate:
    """Adapt a v2 RPC chunk row into a typed ChunkCandidate.

    The caller passes ``score_kind`` because the call-site is the only place
    that knows which RPC produced the row. NEVER derive score_kind from the
    row contents (per LlamaIndex v0.10 break-cases).

    The ``default_rrf_score`` knob lets the call-site override
    ``rrf_score = score`` when the call-site has a more accurate per-source
    rank-based RRF value (e.g. when fusing two recall sources).
    """
    score = float(row["score"]) if "score" in row else 0.0
    rrf_score = default_rrf_score if default_rrf_score is not None else score
    if score_kind == "fts":
        fts_text = row.get("fts_text", "") or row.get("content", "") or ""
    else:
        fts_text = ""
    # Raw component scores from hybrid RPCs (None if absent — never coerce to 0).
    raw_dense = row.get("raw_dense_score")
    raw_fts = row.get("raw_fts_score")
    return ChunkCandidate(
        canonical_chunk_id=uuid.UUID(str(row["canonical_chunk_id"])),
        canonical_zettel_id=uuid.UUID(str(row["canonical_zettel_id"])),
        chunk_idx=row.get("chunk_idx"),
        content=row.get("content", "") or "",
        score=score,
        rrf_score=rrf_score,
        score_kind=score_kind,
        fts_text=fts_text,
        raw_dense_score=float(raw_dense) if raw_dense is not None else None,
        raw_fts_score=float(raw_fts) if raw_fts is not None else None,
    )


def entity_from_v2_row(
    row: dict[str, Any], *, score_kind: ScoreKind, default_rrf_score: float | None = None
) -> EntityCandidate:
    """Adapt a v2 KG row into a typed EntityCandidate. See chunk_from_v2_row notes."""
    score = float(row["score"]) if "score" in row else 0.0
    rrf_score = default_rrf_score if default_rrf_score is not None else score
    return EntityCandidate(
        kg_node_id=int(row["kg_node_id"]),
        title=row.get("title", "") or "",
        entity_type=row.get("entity_type"),
        score=score,
        rrf_score=rrf_score,
        score_kind=score_kind,
    )


def doc_from_v2_row(
    row: dict[str, Any], *, score_kind: ScoreKind, default_rrf_score: float | None = None
) -> DocCandidate:
    """Adapt a v2 zettel row into a typed DocCandidate. See chunk_from_v2_row notes."""
    score = float(row["score"]) if "score" in row else 0.0
    rrf_score = default_rrf_score if default_rrf_score is not None else score
    return DocCandidate(
        canonical_zettel_id=uuid.UUID(str(row["canonical_zettel_id"])),
        title=row.get("title", "") or "",
        score=score,
        rrf_score=rrf_score,
        score_kind=score_kind,
    )
