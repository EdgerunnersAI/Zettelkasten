"""DEPRECATED iter-08 — replaced by chunk_share.py per RES-2.

The frequency prior was a strict no-op for 6 iters (floor=50, max ~48 hits
across iter-04→07). hybrid.py:307 no longer consults it. Module retained
because orchestrator.py and HybridRetriever.__init__ still accept the
``kasten_freq_store`` kwarg for back-compat. Schedule full deletion in iter-09.

Historical context (iter-04): per-Kasten node-frequency prior (anti-magnet
penalty) for the q5-class failure mode where a single zettel won top-1
across many semantically unrelated queries.

* :class:`KastenFrequencyStore` — async accessor for ``kg_kasten_node_freq``,
  returning ``{node_id: hit_count}`` for a given Kasten, with a small
  in-process TTL cache (60s) so a hot Kasten doesn't hammer Supabase on
  every request.
* :func:`compute_frequency_penalty` — pure helper that turns a hit-count
  into a multiplicative score-damping factor in ``(0, 1]``.

The store is best-effort: any error (table missing, RPC missing, network
failure) returns an empty dict so the retriever degrades to no-penalty
behaviour rather than failing the request.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any
from uuid import UUID

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

logger = logging.getLogger(__name__)

# Cold-start floor: do not apply the penalty until the Kasten has at least
# this many recorded hits in total. Below the floor the per-node counts are
# too noisy to distinguish "magnet" from "genuinely-popular-because-relevant".
_MIN_TOTAL_HITS_FOR_PENALTY = 50

# Cache TTL — refresh per-Kasten frequency dicts every 60s. Hits are written
# asynchronously and small staleness is acceptable.
_CACHE_TTL_S = 60.0


class KastenFrequencyStore:
    """Async accessor for per-Kasten node-hit frequencies.

    The constructor accepts an optional Supabase client; if not provided we
    lazily resolve the project-default client. All read failures return an
    empty dict (best-effort degradation).
    """

    def __init__(self, supabase: Any | None = None):
        self._supabase = supabase
        self._cache: dict[str, tuple[float, dict[str, int]]] = {}

    async def get_frequencies(self, kasten_id: UUID | str | None) -> dict[str, int]:
        if kasten_id is None:
            return {}
        cache_key = str(kasten_id)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0] < _CACHE_TTL_S):
            return cached[1]
        client = self._resolve_client()
        if client is None:
            self._cache[cache_key] = (now, {})
            return {}
        try:
            response = await rpc_call(client.rpc(
                "rag_kasten_node_frequencies",
                {"p_kasten_id": cache_key},
            ))
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("kasten_freq fetch failed (%s); degrading to empty", exc)
            self._cache[cache_key] = (now, {})
            return {}
        rows = response.data or []
        freqs: dict[str, int] = {}
        for row in rows:
            node_id = row.get("node_id")
            hits = row.get("hit_count")
            if isinstance(node_id, str) and isinstance(hits, int):
                freqs[node_id] = hits
        self._cache[cache_key] = (now, freqs)
        return freqs

    async def record_hit(
        self,
        *,
        kasten_id: UUID | str | None,
        node_id: str | None,
    ) -> None:
        """Record a top-1 retrieval hit. Best-effort, never raises."""
        if not kasten_id or not node_id:
            return
        client = self._resolve_client()
        if client is None:
            return
        try:
            await rpc_call(client.rpc(
                "rag_kasten_record_node_hit",
                {"p_kasten_id": str(kasten_id), "p_node_id": node_id},
            ))
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("kasten_freq record failed (%s)", exc)
            return
        # Invalidate the cache so the next read picks up the increment.
        self._cache.pop(str(kasten_id), None)

    def _resolve_client(self):
        if self._supabase is not None:
            return self._supabase
        try:
            from website.core.supabase_kg.client import get_supabase_client
            self._supabase = get_supabase_client()
        except Exception as exc:  # noqa: BLE001
            logger.debug("kasten_freq supabase client init failed: %s", exc)
            self._supabase = None
        return self._supabase


def compute_frequency_penalty(
    node_hit_count: int,
    *,
    total_hits_in_kasten: int,
    floor: int = _MIN_TOTAL_HITS_FOR_PENALTY,
) -> float:
    """Return a multiplicative damping factor in ``(0, 1]``.

    Cold-start: when the Kasten has fewer than ``floor`` total hits, return
    ``1.0`` (no penalty). Otherwise apply ``1 / (1 + log(1 + freq))`` with
    smoothing, capped at a 50% maximum demotion so a magnet still ranks
    where genuine relevance + diversity put it.
    """
    if total_hits_in_kasten < floor:
        return 1.0
    if node_hit_count <= 0:
        return 1.0
    raw = 1.0 / (1.0 + math.log(1.0 + node_hit_count))
    return max(0.5, raw)
