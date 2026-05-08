from __future__ import annotations

import pytest

from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter
from website.features.rag_pipeline.scoring.registry_init import validate_registry_completeness


def test_registry_weight_defaults_when_missing() -> None:
    adapter = RegistryAdapter(environment="dev")
    assert adapter.get_weight("semantic", default=0.5) == 0.5


def test_registry_completeness_fails_for_missing_scorers() -> None:
    adapter = RegistryAdapter(environment="dev")
    with pytest.raises(RuntimeError, match="retrieval scorer registry missing"):
        validate_registry_completeness(adapter, required=["semantic"])

