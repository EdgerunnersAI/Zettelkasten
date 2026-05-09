"""Phase 3.6 — PageIndex_Rag.data_access retirement test.

The legacy file was a direct ``public.kg_users + public.kg_nodes`` data-access
layer that bypassed RLS. It is incompatible with the v2 workspace model and is
not on any live route per ``website/api/routes.py``. Phase 3.6 retires the
module: import succeeds, but any attribute access raises NotImplementedError.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_data_access_module_imports_clean():
    """The module must import without side-effects."""
    import website.experimental_features.PageIndex_Rag.data_access as data_access

    assert data_access is not None


def test_attribute_access_raises_not_implemented():
    import website.experimental_features.PageIndex_Rag.data_access as data_access

    with pytest.raises(NotImplementedError, match="retired pending v2 redesign"):
        _ = data_access.fetch_zettels_for_scope


def test_no_supabase_kg_import():
    src = Path(
        "website/experimental_features/PageIndex_Rag/data_access.py"
    ).read_text(encoding="utf-8")
    assert "supabase_kg" not in src
    assert "kg_users" not in src
    assert "kg_nodes" not in src
