"""iter-09 RES-7: anchor-seed RPC client for Q10-style cross-tenant safe seeding."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call


async def fetch_anchor_seeds(
    anchor_nodes: list[str],
    sandbox_id: UUID | str | None,
    query_embedding: list[float],
    supabase: Any,
) -> list[dict]:
    """Fetch seed candidates for anchor nodes restricted to the sandbox's members.

    Returns a list of `{node_id, score}` dicts. RPC failure or empty input
    degrades to empty list (mirrors entity_anchor.py error semantics).
    """
    if not anchor_nodes or sandbox_id is None or not query_embedding:
        return []
    try:
        response = await rpc_call(supabase.rpc(
            "rag_fetch_anchor_seeds",
            {
                "p_sandbox_id": str(sandbox_id),
                "p_anchor_nodes": list(anchor_nodes),
                "p_query_embedding": list(query_embedding),
            },
        ))
        return list(response.data or [])
    except Exception:
        return []
