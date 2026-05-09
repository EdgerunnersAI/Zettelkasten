"""v2 unit tests for `chunk_share.py` (Phase 2.2 — supabase_kg purge).

Verifies the refactored `ChunkShareStore` calls the v2 RPC
`rag.chunk_share_for_kasten` via the supabase-py `.schema("rag").rpc(...)`
form, returns a `dict[str, int]` keyed by canonical_chunk_id, and degrades to
`{}` (identity contract from the v1 implementation) on RPC error.

Mocks follow the `_Client` / `_Schema` / `_Table` idiom from
`tests/unit/supabase_v2/test_repositories.py`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from website.features.rag_pipeline.retrieval import chunk_share as chunk_share_module
from website.features.rag_pipeline.retrieval.chunk_share import (
    ChunkShareStore,
    compute_chunk_share_penalty,
)


# ---------------------------------------------------------------------------
# Fake supabase-py client (sync) — only the surface the store exercises.
# ---------------------------------------------------------------------------


class _Execute:
    def __init__(self, data, raise_exc: BaseException | None = None):
        self._data = data
        self._raise = raise_exc

    def execute(self):
        if self._raise is not None:
            raise self._raise
        return type("Resp", (), {"data": self._data})()


class _Schema:
    def __init__(self, calls, schema, data, raise_exc):
        self.calls = calls
        self.schema = schema
        self._data = data
        self._raise = raise_exc

    def rpc(self, name, params):
        self.calls.append(("rpc", self.schema, name, params))
        return _Execute(self._data, self._raise)


class _Client:
    """Mimics the bits of `supabase.Client` that ChunkShareStore touches."""

    def __init__(self, *, data=None, raise_exc: BaseException | None = None):
        self.calls: list = []
        self._data = data if data is not None else []
        self._raise = raise_exc

    def schema(self, name):
        self.calls.append(("schema", name))
        return _Schema(self.calls, name, self._data, self._raise)

    # Should never be called — v2 contract is schema-scoped.
    def rpc(self, name, params):  # pragma: no cover
        raise AssertionError(
            f"ChunkShareStore must use schema('rag').rpc(...), got unscoped rpc({name!r})"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chunk_share_returns_canonical_chunk_id_keyed_counts():
    """Happy path: v2 RPC rows {canonical_chunk_id, chunk_count} → dict[str, int]."""
    fake = _Client(
        data=[
            {"canonical_chunk_id": "11111111-1111-1111-1111-111111111111", "chunk_count": 16},
            {"canonical_chunk_id": "22222222-2222-2222-2222-222222222222", "chunk_count": 6},
            {"canonical_chunk_id": "33333333-3333-3333-3333-333333333333", "chunk_count": 2},
        ]
    )
    store = ChunkShareStore(supabase=fake)
    result = asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    assert result == {
        "11111111-1111-1111-1111-111111111111": 16,
        "22222222-2222-2222-2222-222222222222": 6,
        "33333333-3333-3333-3333-333333333333": 2,
    }


def test_chunk_share_calls_v2_rpc_with_p_kasten_id():
    """Delegates to schema('rag').rpc('chunk_share_for_kasten', {p_kasten_id: ...})."""
    fake = _Client(data=[])
    store = ChunkShareStore(supabase=fake)
    asyncio.run(store.get_chunk_counts(sandbox_id="abc-kasten-uuid"))
    # First call must be schema('rag'), then rpc('chunk_share_for_kasten', {...}).
    assert ("schema", "rag") in fake.calls
    rpc_calls = [c for c in fake.calls if c[0] == "rpc"]
    assert len(rpc_calls) == 1
    _kind, schema_name, rpc_name, params = rpc_calls[0]
    assert schema_name == "rag"
    assert rpc_name == "chunk_share_for_kasten"
    assert params == {"p_kasten_id": "abc-kasten-uuid"}


def test_chunk_share_none_sandbox_returns_empty_no_rpc():
    """sandbox_id=None short-circuits to {} without touching the client."""
    fake = _Client(data=[{"canonical_chunk_id": "x", "chunk_count": 1}])
    store = ChunkShareStore(supabase=fake)
    result = asyncio.run(store.get_chunk_counts(sandbox_id=None))
    assert result == {}
    assert fake.calls == []  # no schema / rpc / execute calls


def test_chunk_share_rpc_error_returns_empty_dict():
    """Identity contract from v1: RPC raises → returns {} (does not propagate)."""
    boom = RuntimeError("simulated postgrest 5xx")
    fake = _Client(raise_exc=boom)
    store = ChunkShareStore(supabase=fake)
    result = asyncio.run(store.get_chunk_counts(sandbox_id="kasten-explode"))
    assert result == {}


def test_chunk_share_penalty_factor_inverse_sqrt():
    """Damping math is unchanged from iter-08: 1/sqrt(chunk_count)."""
    assert abs(compute_chunk_share_penalty(16) - 0.25) < 1e-3
    assert abs(compute_chunk_share_penalty(4) - 0.5) < 1e-3
    assert compute_chunk_share_penalty(1) == 1.0
    assert compute_chunk_share_penalty(0) == 1.0


def test_chunk_share_module_does_not_import_supabase_kg():
    """File-level grep: `supabase_kg` must not appear in the refactored module."""
    src_path = Path(chunk_share_module.__file__)
    text = src_path.read_text(encoding="utf-8")
    assert "supabase_kg" not in text, (
        "chunk_share.py must not reference supabase_kg after v2 purge"
    )


def test_chunk_share_caches_within_ttl():
    """TTLCache: two reads within ttl → 1 RPC."""
    fake = _Client(data=[{"canonical_chunk_id": "a", "chunk_count": 5}])
    store = ChunkShareStore(supabase=fake, ttl_seconds=60.0)
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    asyncio.run(store.get_chunk_counts(sandbox_id="kasten1"))
    rpc_calls = [c for c in fake.calls if c[0] == "rpc"]
    assert len(rpc_calls) == 1, f"expected single RPC under TTL, got {fake.calls!r}"
