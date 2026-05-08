"""Scorer registry support."""

from .registry_adapter import RegistryAdapter
from .registry_init import validate_registry_completeness

__all__ = ["RegistryAdapter", "validate_registry_completeness"]

