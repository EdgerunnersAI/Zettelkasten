"""Phase 2.4.1+2.4.2: KG anchor resolution via v2 RPCs.

iter-08 Phase 6 introduced this module as the entity-name -> KG anchor node
resolver. v2 cutover (Phase 2.4) swaps the legacy ``rag_resolve_entity_anchors``
+ ``rag_one_hop_neighbours`` RPCs for the new tenant-scoped v2 versions:

  - ``kg.resolve_entity_anchors_v2(p_workspace_id, p_terms, p_min_similarity)``
    returns ``(kg_node_id bigint, canonical_name, matched_alias, matched_kind,
    similarity)``.
  - ``kg.expand_subgraph(p_workspace_id, p_node_ids bigint[], p_depth int)``
    returns ``(id bigint)`` for each node within depth.
  - ``kg.entities_to_anchor_chunks(p_workspace_id, p_kg_node_ids bigint[])``
    bridges the bigint kg_node_id space to canonical_chunk_id (uuid) via
    ``kg.chunk_node_mentions``.

The public surface is the same set of helpers; their return shape changes:

  * ``resolve_anchor_nodes`` now returns ``set[int]`` (kg_node_id bigints).
  * ``get_one_hop_neighbours`` now returns ``set[int]`` (the depth-1 expansion
    of those kg_node_ids).
  * ``entities_to_anchor_chunks`` is new — returns the canonical_chunk_id
    bridge needed by the chunk-level retrieval pipeline.

Per Phase 2.4 ruling #4 (operator): the input shape semantically shifts from
"anchor zettels" to "anchor canonical chunks". Each helper now operates on
typed bigint kg_node_ids; the bridge to canonical_chunk_id (uuid) happens at
the call site that needs it (HybridRetriever).

iter-12 R5 / R6 telemetry (matched_via logging + EntityBlocklist hit/miss
recording) is preserved end-to-end — only the RPC names + ID types change.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, TYPE_CHECKING
from uuid import UUID

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

if TYPE_CHECKING:
    from website.features.rag_pipeline.query.blocklist import EntityBlocklist

_log = logging.getLogger(__name__)

_ENTITY_GATHER_SIZE = int(os.environ.get("RAG_ENTITY_GATHER_SEMAPHORE", "3"))

# v2 default: 0.30 matches the SQL default in 23_resolve_entity_anchors_rpc.sql.
_MIN_SIMILARITY = float(os.environ.get("RAG_ENTITY_ANCHOR_MIN_SIMILARITY", "0.30"))


async def resolve_anchor_nodes(
    entities: list[str],
    workspace_id: UUID | str | None,
    supabase: Any,
    *,
    blocklist: "EntityBlocklist | None" = None,
    kasten_node_count: int = 0,
) -> set[int]:
    """Map entity names to canonical kg_node_ids via fuzzy title/alias match.

    Phase 2.4.1: now calls ``kg.resolve_entity_anchors_v2`` (workspace-scoped,
    pg_trgm similarity over canonical_name + aliases).

    Returns a ``set[int]`` of kg_node_ids (bigint primary keys). Empty set on
    missing inputs / RPC errors / blocked entities.

    iter-11 Class C per-entity isolation: per-entity calls are gathered
    concurrently with a Semaphore-bounded fan-out so a single bad term does
    not poison the whole batch. iter-12 R5 logs ``matched_kind`` distribution
    so alias coverage is observable. iter-12 R6 records blocklist hit/miss
    per entity (fail-open).
    """
    if not entities or workspace_id is None:
        return set()
    request_sem = asyncio.Semaphore(_ENTITY_GATHER_SIZE)

    async def _resolve_one(entity: str) -> list[dict]:
        if not isinstance(entity, str):
            return []
        cleaned = entity.strip()
        if not cleaned:
            return []

        if blocklist is not None:
            try:
                if await blocklist.is_blocked(str(workspace_id), cleaned, node_count=kasten_node_count):
                    _log.debug("entity_anchor skip_blocked entity=%r", cleaned)
                    return []
            except Exception as exc:  # noqa: BLE001 — fail-open
                _log.warning("entity_anchor blocklist.is_blocked error entity=%r: %s", cleaned, exc)

        try:
            response = await rpc_call(
                supabase.schema("kg").rpc(
                    "resolve_entity_anchors_v2",
                    {
                        "p_workspace_id": str(workspace_id),
                        "p_terms": [cleaned],
                        "p_min_similarity": _MIN_SIMILARITY,
                    },
                ),
                request_sem=request_sem,
            )
            rows = [
                {
                    "kg_node_id": int(row["kg_node_id"]),
                    "matched_kind": row.get("matched_kind") or "canonical",
                }
                for row in (response.data or [])
                if row.get("kg_node_id") is not None
            ]
            node_ids = {r["kg_node_id"] for r in rows}

            if blocklist is not None:
                try:
                    if node_ids:
                        await blocklist.record_hit(str(workspace_id), cleaned)
                    else:
                        await blocklist.record_miss(str(workspace_id), cleaned, node_count=kasten_node_count)
                except Exception as exc:  # noqa: BLE001 — never let blocklist writes fail the request
                    _log.warning("entity_anchor blocklist record error entity=%r: %s", cleaned, exc)

            return rows
        except Exception as exc:  # noqa: BLE001 — best-effort, isolated
            _log.debug("entity_anchor rpc_error entity=%r exc=%s", cleaned, type(exc).__name__)
            return []

    per_entity_rows: list[list[dict]] = await asyncio.gather(*[_resolve_one(e) for e in entities])
    node_matched_via: dict[int, str] = {}
    for rows in per_entity_rows:
        for row in rows:
            node_matched_via[row["kg_node_id"]] = row["matched_kind"]

    resolved = set(node_matched_via)
    clean_entities = {e.strip() for e in entities if isinstance(e, str) and e.strip()}
    missing = clean_entities - {str(nid) for nid in resolved}
    missing_repr = sorted(missing)[:10]
    _log.info(
        "entity_anchor_resolve n_entities=%d resolved=%d matched_kind=%r missing=%r",
        len(entities),
        len(resolved),
        {nid: via for nid, via in node_matched_via.items()},
        missing_repr,
    )
    return resolved


async def get_one_hop_neighbours(
    anchor_nodes: set[int],
    workspace_id: UUID | str | None,
    supabase: Any,
) -> set[int]:
    """Return kg_node_ids 1-hop adjacent to any anchor in the workspace KG.

    Phase 2.4.2: calls ``kg.expand_subgraph(p_workspace_id, p_node_ids, depth=1)``
    which returns the ``(seed_set ∪ depth-1 neighbours)`` (the seeds themselves
    are included by the SQL CTE base case). Depth is fixed at 1 for back-compat
    with the legacy ``rag_one_hop_neighbours`` semantics.
    """
    if not anchor_nodes or workspace_id is None:
        return set()
    try:
        response = await rpc_call(supabase.schema("kg").rpc(
            "expand_subgraph",
            {
                "p_workspace_id": str(workspace_id),
                "p_node_ids": [int(n) for n in anchor_nodes],
                "p_depth": 1,
            },
        ))
        return {int(row["id"]) for row in (response.data or []) if row.get("id") is not None}
    except Exception:
        return set()


async def entities_to_anchor_chunks(
    kg_node_ids: set[int] | list[int],
    workspace_id: UUID | str | None,
    supabase: Any,
) -> list[dict]:
    """Bridge bigint kg_node_ids to canonical_chunk_ids via mention rows.

    Phase 2.4.2: thin wrapper around ``kg.entities_to_anchor_chunks``. Returns
    a list of ``{canonical_chunk_id, kg_node_id, mention_count}`` rows; the
    caller can rank by mention_count + distinct kg_node_id count for the
    GraphRAG-local graph-rank signal.

    Returns ``[]`` on missing inputs or RPC errors (best-effort, fail-open).
    """
    if not kg_node_ids or workspace_id is None:
        return []
    try:
        response = await rpc_call(supabase.schema("kg").rpc(
            "entities_to_anchor_chunks",
            {
                "p_workspace_id": str(workspace_id),
                "p_kg_node_ids": [int(n) for n in kg_node_ids],
            },
        ))
        rows = response.data or []
        return [
            {
                "canonical_chunk_id": str(row["canonical_chunk_id"]),
                "kg_node_id": int(row["kg_node_id"]),
                "mention_count": int(row.get("mention_count") or 1),
            }
            for row in rows
            if row.get("canonical_chunk_id") and row.get("kg_node_id") is not None
        ]
    except Exception as exc:  # noqa: BLE001 — best-effort
        _log.debug("entities_to_anchor_chunks rpc_error %s: %s", type(exc).__name__, exc)
        return []
