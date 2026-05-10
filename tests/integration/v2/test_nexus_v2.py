"""Inspect-only fitness tests for nexus service modules under v2.

Phase 8.0-H8: ratchets out v1 table references (``public.kg_users``,
``public.nexus_provider_accounts``, ``public.nexus_ingest_runs``,
``public.nexus_ingested_artifacts``) from header docstrings and inline
comments in ``website.experimental_features.nexus.service.*``. These
modules now exclusively target the ``pipelines.*`` schema; any
re-introduction of v1 names would indicate accidental drift.

The test imports modules and inspects their source — no live DB calls.
"""
from __future__ import annotations

import inspect

from website.experimental_features.nexus.service import (
    bulk_import,
    persist,
    token_store,
)


def test_nexus_modules_target_pipelines_schema():
    """No v1 references remain in nexus service module headers/code."""
    for mod in (token_store, bulk_import, persist):
        src = inspect.getsource(mod)
        assert "nexus_provider_accounts" not in src, (
            f"{mod.__name__}: v1 table reference (nexus_provider_accounts) remains"
        )
        assert "nexus_ingested_artifacts" not in src, (
            f"{mod.__name__}: v1 table reference (nexus_ingested_artifacts) remains"
        )
        # nexus_ingest_runs may only appear when fully qualified with pipelines.* schema
        if "nexus_ingest_runs" in src:
            assert "pipelines.nexus_ingest_runs" in src, (
                f"{mod.__name__}: bare v1 nexus_ingest_runs reference (use pipelines.* prefix)"
            )
        # No imports from the dropped supabase_kg legacy module
        assert "from website.core.supabase_kg" not in src, (
            f"{mod.__name__}: legacy supabase_kg import remains"
        )
        # No bare public.kg_users references
        assert "public.kg_users" not in src, (
            f"{mod.__name__}: legacy public.kg_users reference remains"
        )
