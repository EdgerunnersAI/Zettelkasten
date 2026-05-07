"""iter-12 Class K4: per-Kasten rolling magnet-spotter threshold.

Replaces the static 25% top-1-share threshold (iter-09 magnet-spotter) with a
per-Kasten bootstrap. Threshold = mean + 2 * stdev of per-node top-1 frequencies
over the last N=50 queries. Below n_min=20 queries, falls back to the static
0.25 baseline.

Persisted to `kg_kasten_metrics` Supabase table for cross-restart durability;
in-memory cache for hot-path access. iter-12 ships in-memory + best-effort
flush; persistent read on restart is iter-13 carry-over.
"""
from __future__ import annotations

import statistics
from collections import Counter, deque


class KastenStats:
    def __init__(self, window: int = 50, n_min: int = 20):
        self._window = window
        self._n_min = n_min
        self._buffers: dict[str, deque[str]] = {}

    def record(self, sandbox_id: str, top1_node_id: str) -> None:
        if sandbox_id not in self._buffers:
            self._buffers[sandbox_id] = deque(maxlen=self._window)
        self._buffers[sandbox_id].append(top1_node_id)

    def bootstrap_threshold(self, sandbox_id: str) -> float:
        buf = self._buffers.get(sandbox_id)
        if not buf or len(buf) < self._n_min:
            return 0.25  # static fallback
        counts = Counter(buf)
        n = len(buf)
        freqs = [c / n for c in counts.values()]
        if len(freqs) < 2:
            return min(1.0, max(freqs))
        mean = statistics.mean(freqs)
        stdev = statistics.pstdev(freqs)
        return min(1.0, mean + 2 * stdev)
