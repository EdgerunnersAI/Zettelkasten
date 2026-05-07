"""iter-08 Phase 6: entity-name -> KG anchor node resolver.

iter-11 Class C: switched from a single batched RPC over ``unnest(p_entities)``
to a per-entity loop that unions the resolved node ids. Reasons:

1. Forensic visibility — the iter-11 Phase 0 scout could not tell which entity
   resolved and which did not (q10's "Steve Jobs and Naval Ravikant" failure
   shape). The per-entity loop logs ``resolved=K missing=[...]`` which makes
   the next iter's debugging cheap.
2. Failure isolation — an RPC error on one entity (e.g. transient Supabase
   503) used to poison the whole batch and return ``set()``. Per-entity calls
   isolate failures so the surviving entities still resolve.
3. Empty-entity hygiene — strips whitespace-only / empty strings before the
   RPC instead of relying on the RPC to no-op them.

iter-12 Class P: per-entity calls are now gathered concurrently via
asyncio.gather with a Semaphore(3) per-request gate. RPC bodies run in
asyncio.to_thread via rpc_call so the event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

_log = logging.getLogger(__name__)

_ENTITY_GATHER_SIZE = int(os.environ.get("RAG_ENTITY_GATHER_SEMAPHORE", "3"))


async def resolve_anchor_nodes(
    entities: list[str],
    sandbox_id: UUID | str | None,
    supabase: Any,
) -> set[str]:
    """Map entity names to canonical Kasten node_ids via fuzzy title/tag match.

    iter-11 Class C: per-entity calls with union semantics. iter-12 Class P:
    calls are concurrent via asyncio.gather with a Semaphore(3) per-request
    gate so fan-out stays bounded.
    """
    if not entities or sandbox_id is None:
        return set()
    request_sem = asyncio.Semaphore(_ENTITY_GATHER_SIZE)

    async def _resolve_one(entity: str) -> set[str]:
        if not isinstance(entity, str):
            return set()
        cleaned = entity.strip()
        if not cleaned:
            return set()
        try:
            response = await rpc_call(
                supabase.rpc(
                    "rag_resolve_entity_anchors",
                    {"p_sandbox_id": str(sandbox_id), "p_entities": [cleaned]},
                ),
                request_sem=request_sem,
            )
            return {row["node_id"] for row in (response.data or []) if row.get("node_id")}
        except Exception as exc:  # noqa: BLE001 — best-effort, isolated
            _log.debug("entity_anchor rpc_error entity=%r exc=%s", cleaned, type(exc).__name__)
            return set()

    results = await asyncio.gather(*[_resolve_one(e) for e in entities])
    resolved = set().union(*results)
    clean_entities = {e.strip() for e in entities if isinstance(e, str) and e.strip()}
    missing = clean_entities - resolved
    missing_repr = sorted(missing)[:10]
    _log.info(
        "entity_anchor_resolve n_entities=%d resolved=%d missing=%r",
        len(entities),
        len(resolved),
        missing_repr,
    )
    return resolved


async def get_one_hop_neighbours(
    anchor_nodes: set[str],
    sandbox_id: UUID | str | None,
    supabase: Any,
) -> set[str]:
    """Return all node_ids 1-hop adjacent to any anchor in the Kasten subgraph."""
    if not anchor_nodes or sandbox_id is None:
        return set()
    try:
        response = await rpc_call(supabase.rpc(
            "rag_one_hop_neighbours",
            {"p_sandbox_id": str(sandbox_id), "p_anchor_nodes": list(anchor_nodes)},
        ))
        return {row["node_id"] for row in (response.data or [])}
    except Exception:
        return set()
