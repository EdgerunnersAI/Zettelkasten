"""Phase 8.0 H6 — kasten_stats no longer persists to dropped DB table.

Replaces the iter-12 "persisted to kg_kasten_metrics" promise with
OpenTelemetry counter + structured-log emissions. The in-memory rolling
threshold cache (KastenStats class) is preserved for backwards compat
with score_rag_eval.py and existing tests.
"""
from __future__ import annotations

import inspect
import logging


def test_kasten_stats_no_db_persistence():
    """Source must not reference the dropped v1 table or supabase_kg client."""
    from website.features.rag_pipeline.observability import kasten_stats
    src = inspect.getsource(kasten_stats)
    assert "kg_kasten_metrics" not in src, "v1 table reference must be removed"
    assert "from website.core.supabase_kg" not in src
    # OTel meter or structured log expected
    assert (
        "opentelemetry" in src.lower() or
        "logger." in src or
        "logging." in src
    ), "must emit OTel counters or structured logs"


def test_kasten_stats_record_emits_log(caplog):
    """KastenStats.record() must emit a structured log; no exception."""
    from website.features.rag_pipeline.observability.kasten_stats import KastenStats
    stats = KastenStats()
    with caplog.at_level(logging.DEBUG, logger="rag.kasten_stats"):
        stats.record("kasten-1", "node-a")
    # At least one structured-log record from the kasten_stats logger
    assert any(rec.name == "rag.kasten_stats" for rec in caplog.records), (
        "record() must emit a log on the rag.kasten_stats logger"
    )


def test_kasten_stats_otel_optional_import_does_not_break():
    """Module import must succeed even if opentelemetry is not installed."""
    # The actual import already happened at module load — if otel is missing
    # and the try/except is wrong, this import would have raised at collection.
    from website.features.rag_pipeline.observability import kasten_stats
    assert hasattr(kasten_stats, "KastenStats")
