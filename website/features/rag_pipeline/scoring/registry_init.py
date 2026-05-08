"""Boot-time validation for the DB v2 scorer registry."""

from __future__ import annotations

from collections.abc import Iterable

from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter


DEFAULT_REQUIRED_SCORERS = {
    "semantic",
    "fts",
    "kg_graph",
    "chunk_share",
    "kasten_frequency",
    "recency",
    "entity_anchor",
    "anchor_bandit",
}


def validate_registry_completeness(
    adapter: RegistryAdapter,
    required: Iterable[str] = DEFAULT_REQUIRED_SCORERS,
) -> None:
    missing = [name for name in required if name not in adapter._configs]
    if missing:
        raise RuntimeError(f"retrieval scorer registry missing: {', '.join(sorted(missing))}")

