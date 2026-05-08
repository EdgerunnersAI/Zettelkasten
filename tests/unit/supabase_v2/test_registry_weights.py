from __future__ import annotations

from website.core.supabase_v2.models import ScorerConfig
from website.features.rag_pipeline.retrieval.hybrid import _weights_for_class
from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter
from website.features.rag_pipeline.types import QueryClass


def test_registry_weights_override_static_defaults() -> None:
    adapter = RegistryAdapter(environment="dev")
    adapter._configs = {
        "semantic": ScorerConfig(environment="dev", scorer_name="semantic", version_id="v1", enabled=True, weight=0.7),
        "fts": ScorerConfig(environment="dev", scorer_name="fts", version_id="v1", enabled=True, weight=0.2),
        "kg_graph": ScorerConfig(environment="dev", scorer_name="kg_graph", version_id="v1", enabled=True, weight=0.1),
    }

    assert _weights_for_class(QueryClass.LOOKUP, adapter) == (0.7, 0.2, 0.1)

