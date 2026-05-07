"""iter-12 R6: per-Kasten negative-resolution cache (DNS-style).

Tracks entities that consecutively fail to resolve to any anchor node.
After _MISS_THRESHOLD misses the entity is blocked for _BLOCK_TTL_DAYS days,
preventing redundant Supabase RPC calls for known-dead entities.

Cold-start guard: skip all block logic when Kasten has < 50 nodes, since
resolution miss rate is artificially high before the graph is populated.

Fail-open: any DB error returns False (not blocked) + WARN log so a transient
Supabase outage never silences a valid entity.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

_log = logging.getLogger(__name__)

_MISS_THRESHOLD = int(os.environ.get("RAG_BLOCKLIST_MISS_THRESHOLD", "2"))
_BLOCK_TTL_DAYS = int(os.environ.get("RAG_BLOCKLIST_TTL_DAYS", "7"))
_COLD_START_NODE_FLOOR = int(os.environ.get("RAG_BLOCKLIST_COLD_START_NODES", "50"))


def _norm(entity: str) -> str:
    """Normalise entity text for blocklist key: lower + strip."""
    return entity.lower().strip()


class EntityBlocklist:
    """Async per-Kasten negative-resolution cache backed by Supabase.

    All public methods are fail-open: DB errors are caught and logged at WARN
    level; the caller sees False / None and continues as if no blocklist exists.
    """

    def __init__(self, supabase: Any):
        self._sb = supabase

    async def is_blocked(self, sandbox_id: str, entity: str, *, node_count: int = _COLD_START_NODE_FLOOR) -> bool:
        """Return True if entity is currently blocked for this sandbox.

        Args:
            sandbox_id: Kasten UUID string.
            entity: raw entity text (will be normalised).
            node_count: current Kasten node count; skip block if below cold-start floor.
        """
        # Cold-start guard: never block on small Kastens
        if node_count < _COLD_START_NODE_FLOOR:
            return False
        entity_norm = _norm(entity)
        if not entity_norm:
            return False
        try:
            response = await rpc_call(
                self._sb.table("kg_extraction_blocklist")
                .select("blocked_until")
                .eq("sandbox_id", sandbox_id)
                .eq("entity_text_norm", entity_norm)
                .limit(1)
            )
            rows = response.data or []
            if not rows:
                return False
            row = rows[0]
            blocked_until = row.get("blocked_until")
            if blocked_until is None:
                # NULL blocked_until means permanent active block
                return True
            # Parse ISO timestamp and compare to now
            try:
                if isinstance(blocked_until, str):
                    # Supabase returns ISO 8601 with timezone offset
                    dt = datetime.fromisoformat(blocked_until.replace("Z", "+00:00"))
                else:
                    dt = blocked_until
                return dt > datetime.now(tz=timezone.utc)
            except (ValueError, TypeError):
                return False
        except Exception as exc:  # noqa: BLE001 — fail-open
            _log.warning("blocklist.is_blocked DB error sandbox=%s entity=%r: %s", sandbox_id, entity_norm, exc)
            return False

    async def record_miss(self, sandbox_id: str, entity: str, *, node_count: int = _COLD_START_NODE_FLOOR) -> None:
        """Increment consecutive_misses; set blocked_until when threshold reached.

        Args:
            sandbox_id: Kasten UUID string.
            entity: raw entity text.
            node_count: current Kasten node count; skip if below cold-start floor.
        """
        if node_count < _COLD_START_NODE_FLOOR:
            return
        entity_norm = _norm(entity)
        if not entity_norm:
            return
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        try:
            # Fetch existing row
            response = await rpc_call(
                self._sb.table("kg_extraction_blocklist")
                .select("consecutive_misses, blocked_until")
                .eq("sandbox_id", sandbox_id)
                .eq("entity_text_norm", entity_norm)
                .limit(1)
            )
            rows = response.data or []
            new_misses = (rows[0]["consecutive_misses"] + 1) if rows else 1
            blocked_until = None
            if new_misses >= _MISS_THRESHOLD:
                blocked_until = (
                    datetime.now(tz=timezone.utc) + timedelta(days=_BLOCK_TTL_DAYS)
                ).isoformat()
            payload = {
                "sandbox_id": sandbox_id,
                "entity_text_norm": entity_norm,
                "consecutive_misses": new_misses,
                "last_seen_at": now_iso,
                "blocked_until": blocked_until,
            }
            await rpc_call(
                self._sb.table("kg_extraction_blocklist")
                .upsert(payload, on_conflict="sandbox_id,entity_text_norm")
            )
        except Exception as exc:  # noqa: BLE001 — fail-open, never block a request
            _log.warning("blocklist.record_miss DB error sandbox=%s entity=%r: %s", sandbox_id, entity_norm, exc)

    async def record_hit(self, sandbox_id: str, entity: str) -> None:
        """Delete blocklist row — successful resolution evicts the block.

        Args:
            sandbox_id: Kasten UUID string.
            entity: raw entity text.
        """
        entity_norm = _norm(entity)
        if not entity_norm:
            return
        try:
            await rpc_call(
                self._sb.table("kg_extraction_blocklist")
                .delete()
                .eq("sandbox_id", sandbox_id)
                .eq("entity_text_norm", entity_norm)
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            _log.warning("blocklist.record_hit DB error sandbox=%s entity=%r: %s", sandbox_id, entity_norm, exc)
