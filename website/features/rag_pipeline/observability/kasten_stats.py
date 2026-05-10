"""Per-Kasten rolling magnet-spotter threshold (in-memory only).

Replaces the static 25% top-1-share threshold (iter-09 magnet-spotter) with a
per-Kasten bootstrap. Threshold = mean + 2 * stdev of per-node top-1 frequencies
over the last N=50 queries. Below n_min=20 queries, falls back to the static
0.25 baseline.

Phase 8.0 H6 (2026-05-10): the original iter-12 design earmarked a Supabase
metrics table for cross-restart durability. That table never materialised and
was formally dropped in the v2 purge. Per Research P, app metrics in an OLTP
DB is an anti-pattern (write amplification, no aggregation, no retention).
We now emit OpenTelemetry counters + structured logs instead;
when an OTLP exporter is wired (Grafana / Honeycomb / Datadog) no code change
is required — set OTEL_EXPORTER_OTLP_ENDPOINT and the meter routes there.
The rolling-threshold cache itself remains in-process (per-worker) by design.
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter, deque

logger = logging.getLogger("rag.kasten_stats")

# Optional OTel meter — falls back to no-op if opentelemetry-api isn't installed.
# Keeps observability optional so prod containers without the SDK still boot.
try:
    from opentelemetry import metrics as _otel_metrics

    _meter = _otel_metrics.get_meter("zettelkasten.rag.kasten_stats")
    _record_counter = _meter.create_counter(
        name="kasten.top1.record",
        description="Top-1 node observations per Kasten",
        unit="1",
    )
except Exception:  # noqa: BLE001 — telemetry is strictly optional
    _record_counter = None


class KastenStats:
    def __init__(self, window: int = 50, n_min: int = 20):
        self._window = window
        self._n_min = n_min
        self._buffers: dict[str, deque[str]] = {}

    def record(self, sandbox_id: str, top1_node_id: str) -> None:
        if sandbox_id not in self._buffers:
            self._buffers[sandbox_id] = deque(maxlen=self._window)
        self._buffers[sandbox_id].append(top1_node_id)
        # Phase 8.0 H6: emit observability instead of DB persistence.
        logger.debug(
            "kasten.top1.record",
            extra={"sandbox_id": sandbox_id, "top1_node_id": top1_node_id},
        )
        if _record_counter is not None:
            _record_counter.add(1, {"sandbox_id": sandbox_id})

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
