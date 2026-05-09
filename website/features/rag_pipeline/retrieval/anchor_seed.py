"""Phase 2.4.3: kasten-scoped anchor-seed RPC client (v2 cutover).

Replaces the legacy ``rag_fetch_anchor_seeds`` RPC with the v2 kasten-scoped
``rag.fetch_anchor_seeds_v2`` (per ``13_v2_kasten_rpcs.sql`` lines 111-168).

Semantic shift (operator ruling #4): the input space changes from
"anchor zettel ids" to "anchor canonical_chunk_ids" — the GraphRAG-local
pattern. Callers resolve kg_node_ids first via ``kg.resolve_entity_anchors_v2``
+ ``kg.expand_subgraph``, then bridge to canonical_chunk_ids via
``kg.entities_to_anchor_chunks``, then pass the chunk-id set here. The seed
selector returns the highest-similarity chunk *per zettel* (PARTITION BY +
ROW_NUMBER) so the anchor pool stays diverse.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call


async def fetch_anchor_seeds(
    anchor_canonical_chunk_ids: list[str] | list[UUID],
    kasten_id: UUID | str | None,
    query_embedding: list[float],
    supabase: Any,
) -> list[dict]:
    """Fetch best-per-zettel seed candidates restricted to a kasten.

    Phase 2.4.3: calls ``rag.fetch_anchor_seeds_v2(p_kasten_id,
    p_anchor_canonical_chunk_ids, p_query_embedding)``. Returns rows of
    ``{canonical_chunk_id, canonical_zettel_id, chunk_idx, content, score}``
    — one per distinct canonical_zettel_id in the anchor set, ranked by
    cosine similarity to the query embedding.

    Anti-pattern guard: chunk_ids are uuid strings. NEVER pass bigint
    kg_node_ids directly to this helper — bridge via
    ``kg.entities_to_anchor_chunks`` first (see entity_anchor module).

    RPC failure or empty input degrades to ``[]`` (mirrors entity_anchor
    error semantics — best-effort, never raises).
    """
    if not anchor_canonical_chunk_ids or kasten_id is None or not query_embedding:
        return []
    try:
        response = await rpc_call(supabase.schema("rag").rpc(
            "fetch_anchor_seeds_v2",
            {
                "p_kasten_id": str(kasten_id),
                "p_anchor_canonical_chunk_ids": [
                    str(cid) for cid in anchor_canonical_chunk_ids
                ],
                "p_query_embedding": list(query_embedding),
            },
        ))
        return list(response.data or [])
    except Exception:
        return []
