"""iter-08 Phase 4: per-Kasten chunk-share anti-magnet.

Replaces the dead kasten_freq prior (RES-2: floor=50, never crossed).
Normalisation: rrf_score *= 1/sqrt(chunk_count_per_node). Punishes
chunk-count-rich magnets (yt-effective-public-speakin = 16 chunks)
while leaving small zettels untouched.
"""
from __future__ import annotations

import logging
import math
import os
import statistics
from typing import Any
from uuid import UUID

from cachetools import TTLCache

from website.features.rag_pipeline.types import QueryClass
from website.features.rag_pipeline.retrieval._async_helpers import rpc_call


_log = logging.getLogger("rag.chunk_share")


# iter-09 RES-2: classes for which chunk-share normalisation is permitted.
# LOOKUP / VAGUE / STEP_BACK excluded — see RES-2 rationale.
_GATED_CLASSES = {QueryClass.THEMATIC, QueryClass.MULTI_HOP}
# Cold-start floor: chunk-count distribution under 5 nodes is too noisy for a
# reliable median, skip damping rather than over-penalise small Kastens.
_COLD_START_MIN = 5


def should_apply_chunk_share(
    query_class: QueryClass,
    chunk_counts: dict[str, int],
) -> tuple[bool, str]:
    """iter-09 RES-2: gate chunk-share normalization on class + per-query magnet ratio.

    Returns ``(apply, reason)``. Reasons: ``class_gate_off`` (env disabled →
    legacy iter-08 always-on behaviour), ``class_excluded`` (LOOKUP / VAGUE /
    STEP_BACK), ``cold_start`` (Kasten too small to compute a stable median),
    ``no_magnet`` (max/median ratio under threshold), ``magnet_detected``.
    """
    enabled = os.environ.get(
        "RAG_CHUNK_SHARE_CLASS_GATE_ENABLED", "true"
    ).lower() not in ("false", "0", "no", "off")
    if not enabled:
        return True, "class_gate_off"
    if query_class not in _GATED_CLASSES:
        return False, "class_excluded"
    if not chunk_counts or len(chunk_counts) < _COLD_START_MIN:
        return False, "cold_start"
    counts = list(chunk_counts.values())
    median = statistics.median(counts)
    if median <= 0:
        return False, "cold_start"
    ratio = max(counts) / median
    threshold = float(os.environ.get("RAG_CHUNK_SHARE_MAGNET_RATIO", "2.0"))
    if ratio < threshold:
        return False, "no_magnet"
    return True, "magnet_detected"


class ChunkShareStore:
    def __init__(
        self,
        supabase: Any | None = None,
        *,
        ttl_seconds: float = 60.0,
    ):
        if supabase is None:
            from website.core.supabase_kg.client import get_supabase_client
            supabase = get_supabase_client()
        self._supabase = supabase
        # iter-08 G4: TTLCache so stale chunk-counts auto-recover within ttl.
        # Mirrors cachetools usage in query/metadata.py:71.
        self._cache: TTLCache = TTLCache(maxsize=128, ttl=ttl_seconds)

    async def get_chunk_counts(self, sandbox_id: UUID | str | None) -> dict[str, int]:
        if sandbox_id is None:
            return {}
        key = str(sandbox_id)
        if key in self._cache:
            _log.debug("chunk_counts cache_hit sandbox=%s", key)
            return self._cache[key]
        # iter-10 P12: surface RPC errors and empty results so q5-class 500s
        # have actionable forensic context (iter-09 lost the q5 500 traceback
        # when the deploy restart purged the worker logs).
        try:
            _log.debug("chunk_counts cache_miss sandbox=%s rpc=rag_kasten_chunk_counts", key)
            response = await rpc_call(self._supabase.rpc(
                "rag_kasten_chunk_counts",
                {"p_sandbox_id": key},
            ))
            data = response.data or []
        except Exception as exc:
            _log.warning(
                "chunk_counts rpc_error sandbox=%s exc=%s", key, type(exc).__name__,
            )
            data = []
        counts = {row["node_id"]: int(row.get("chunk_count", 0)) for row in data}
        if not counts:
            _log.warning(
                "chunk_counts empty sandbox=%s (suspect member-coverage hole or RPC empty)",
                key,
            )
        self._cache[key] = counts
        return counts


def compute_chunk_share_penalty(chunk_count: int) -> float:
    """Multiplicative damping factor in (0, 1].

    1-chunk node → 1.0 (no penalty)
    16-chunk node → 0.25
    """
    if chunk_count <= 1:
        return 1.0
    return 1.0 / math.sqrt(chunk_count)
