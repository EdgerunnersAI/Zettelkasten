"""Chunk persistence helpers for RAG ingestion (DB v2).

Phase 2.5 of the v2 purge: rewires this module from the legacy
``kg_node_chunks`` surface to the canonical+overlay tables shipped in
``_v2/02_content_schema.sql``. Every chunk now lands in TWO places:

* ``content.canonical_chunks`` — the workspace-agnostic chunk content,
  hash, embedding (halfvec(768)), fts_text, and metadata. Cross-workspace
  dedup is handled here via the ``(canonical_zettel_id, chunk_idx)``
  unique constraint.
* ``content.workspace_chunk_membership`` — the per-workspace overlay row
  pointing at the canonical chunk + the workspace-zettel that produced it.
  This is what RLS reads (no workspace_id leakage to canonical content).

The function intentionally takes UUID identifiers (workspace_id +
workspace_zettel_id + canonical_zettel_id) instead of the legacy
``user_id, node_id`` pair. The v1 caller (``hook.py`` -> v1 KG persistence)
no longer applies in v2; this v2 entry point is wired by the v2 ingest
scheduler downstream.
"""

from __future__ import annotations

from uuid import UUID

from website.core.supabase_v2.models import CanonicalChunkCreate
from website.core.supabase_v2.repositories.content_repository import (
    ContentRepository,
)
from website.features.rag_pipeline.ingest.chunker import Chunk
from website.features.rag_pipeline.ingest.embedder import ChunkEmbedder

# Embedding model version stamp matching the row default in
# content.embedding_model_versions.is_default=true (gemini-001-mrl-768).
_EMBED_MODEL_VERSION = "gemini-001-mrl-768"


async def upsert_chunks(
    *,
    workspace_id: UUID,
    canonical_zettel_id: UUID,
    workspace_zettel_id: UUID,
    chunks: list[Chunk],
    embedder: ChunkEmbedder,
    repo: ContentRepository | None = None,
) -> int:
    """Embed + upsert chunks into canonical_chunks + workspace_chunk_membership.

    Returns the number of chunks newly embedded (i.e., the size of the
    ``chunks`` list — v2's upsert is idempotent on
    ``(canonical_zettel_id, chunk_idx)`` so we always re-embed and let the
    DB resolve the conflict). 0 for empty input.

    Halfvec(768) cast: ``CanonicalChunkCreate.embedding`` is a
    ``list[float]`` of length 768 which the supabase-py PostgREST client
    serialises to a JSON array; PostgREST then casts to halfvec via the
    column type. Embedding model version is stamped on every row.

    Intra-call dedupe: drops chunks whose content_hash already appeared
    earlier in this list. Preserves chunk_idx ordering by keeping the
    first occurrence and renumbering compactly. Without this, chunkers
    that emit duplicate text on overlap windows would pollute retrieval.
    """
    if not chunks:
        return 0

    # Intra-call dedupe by content hash (preserves first occurrence).
    seen_hashes: set[bytes] = set()
    deduped: list[Chunk] = []
    for chunk in chunks:
        h = ChunkEmbedder.content_hash(chunk.content)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        deduped.append(chunk)
    if len(deduped) != len(chunks):
        chunks = [
            c.model_copy(update={"chunk_idx": i})
            for i, c in enumerate(deduped)
        ]

    # Embed every chunk. v2 upsert is on (canonical_zettel_id, chunk_idx)
    # so re-embedding is the safe + simple choice — the unique constraint
    # collapses repeated calls to a single row.
    embeddings = await embedder.embed([chunk.content for chunk in chunks])

    chunk_creates = [
        CanonicalChunkCreate(
            chunk_idx=chunk.chunk_idx,
            content=chunk.content,
            content_hash=ChunkEmbedder.content_hash(chunk.content),
            chunk_type=chunk.chunk_type.value,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            token_count=chunk.token_count,
            embedding=embeddings[i] if i < len(embeddings) else None,
            embedding_model_version=_EMBED_MODEL_VERSION,
            metadata=chunk.metadata,
        )
        for i, chunk in enumerate(chunks)
    ]

    repo = repo or ContentRepository()
    canonical_chunk_ids = repo.upsert_chunks(canonical_zettel_id, chunk_creates)
    repo.upsert_workspace_chunk_membership(
        workspace_id=workspace_id,
        workspace_zettel_id=workspace_zettel_id,
        canonical_chunk_ids=canonical_chunk_ids,
    )
    return len(chunks)
