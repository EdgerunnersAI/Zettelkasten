"""Backward-compatible re-exports for the canonical persist module.

The canonical implementation now lives at :mod:`website.core.persist` so every
ingest path (Add Zettel, eval register scripts, future callers)
goes through a single function. This shim keeps existing imports working unchanged.
"""

from website.core.persist import (  # noqa: F401
    PersistenceOutcome,
    _drop_unterminated_tail,
    _encode_summary_payload,
    _file_graph_contains_url,
    _normalize_summary_text,
    _SENTINEL_TEXT_RE,
    _strip_sentinel_text,
    extract_summary_parts,
    persist_summarized_result,
)
# Phase 8.0.3 B+: ``get_supabase_scope`` was retired with the v1 KGRepository
# fallback path. The legacy re-export is no longer offered from this shim.
