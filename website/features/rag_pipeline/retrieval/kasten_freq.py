"""RETIRED iter-12 (DB v2 purge, RES-2) — multiplicative-identity stub.

The frequency prior was a strict no-op for 6+ iters (floor=50, max ~48 hits
across iter-04 to iter-07; never crossed in production). hybrid.py:307
stopped consulting it in iter-08 P4.2; chunk_share.py replaced its
anti-magnet role.

Public symbols are preserved byte-for-byte for back-compat (HybridRetriever
still accepts a ``kasten_freq_store`` kwarg). All bodies now return the
multiplicative identity (1.0 / empty dict / no-op) with zero DB I/O.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

# Cold-start floor retained as a module-level constant because it appears in
# the public signature of ``compute_frequency_penalty`` as a default value.
# Its value is no longer consulted at runtime (the function returns 1.0
# unconditionally) but the symbol must exist for byte-for-byte signature
# preservation per spec RES-2.
_MIN_TOTAL_HITS_FOR_PENALTY = 50


class KastenFrequencyStore:
    """Retired iter-12 — no-op stub. Constructor stores no DB client."""

    def __init__(self, supabase: Any | None = None):
        # Retained-but-unused: callers (hybrid.py) still pass a Supabase
        # client positionally; we accept and discard it.
        self._supabase = None

    async def get_frequencies(self, kasten_id: UUID | str | None) -> dict[str, int]:
        # Identity for the downstream penalty computation: empty frequency
        # dict means every node gets ``1.0`` (no demotion).
        return {}

    async def record_hit(
        self,
        *,
        kasten_id: UUID | str | None,
        node_id: str | None,
    ) -> None:
        # No-op: hit counters are no longer maintained (the table that
        # backed them is being dropped in the v2 purge).
        return None


def compute_frequency_penalty(
    node_hit_count: int,
    *,
    total_hits_in_kasten: int,
    floor: int = _MIN_TOTAL_HITS_FOR_PENALTY,
) -> float:
    """Retired iter-12 — always returns the multiplicative identity ``1.0``."""
    return 1.0
