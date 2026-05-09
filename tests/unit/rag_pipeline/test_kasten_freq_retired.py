"""iter-12 DB v2 purge (RES-2) — kasten_freq retirement guard.

Asserts the retired module is a pure multiplicative-identity stub with
zero DB surface: no ``supabase_kg`` import, no reference to the legacy
``public.kg_kasten_node_freq`` table.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from website.features.rag_pipeline.retrieval import kasten_freq
from website.features.rag_pipeline.retrieval.kasten_freq import (
    KastenFrequencyStore,
    compute_frequency_penalty,
)


def test_compute_frequency_penalty_returns_identity():
    """Synchronous public helper: every input maps to 1.0."""
    assert compute_frequency_penalty(0, total_hits_in_kasten=0) == 1.0
    assert compute_frequency_penalty(0, total_hits_in_kasten=10_000) == 1.0
    assert compute_frequency_penalty(99_999, total_hits_in_kasten=99_999) == 1.0
    assert compute_frequency_penalty(1, total_hits_in_kasten=1, floor=0) == 1.0


@pytest.mark.asyncio
async def test_get_frequencies_returns_empty_for_every_kasten():
    """Async store method: empty dict means downstream penalty is identity."""
    store = KastenFrequencyStore()
    assert await store.get_frequencies(None) == {}
    assert await store.get_frequencies("any-kasten-id") == {}
    assert await store.get_frequencies("ffffffff-ffff-ffff-ffff-ffffffffffff") == {}


@pytest.mark.asyncio
async def test_record_hit_is_noop():
    """record_hit returns None and never raises, for any input."""
    store = KastenFrequencyStore()
    assert await store.record_hit(kasten_id=None, node_id=None) is None
    assert await store.record_hit(kasten_id="k", node_id="n") is None


def test_kasten_freq_class_initialises_without_db():
    """Constructor accepts the legacy positional supabase arg but performs no DB I/O."""
    # No-arg.
    store_a = KastenFrequencyStore()
    assert store_a is not None
    # Legacy call-site shape (hybrid.py passes self._supabase positionally).
    sentinel = object()
    store_b = KastenFrequencyStore(sentinel)
    assert store_b is not None
    # The stub must not retain a live DB handle: stored client is None.
    assert store_b._supabase is None


def test_kasten_freq_module_imports_no_supabase():
    """Module source contains no reference to legacy DB surface."""
    src_path = Path(kasten_freq.__file__)
    src = src_path.read_text(encoding="utf-8")
    assert "supabase_kg" not in src, "kasten_freq still references supabase_kg"
    assert "kg_kasten_node_freq" not in src, "kasten_freq still references retired table"
    assert "rpc_call" not in src, "kasten_freq still imports rpc_call"


def test_public_signatures_preserved():
    """RES-2 byte-for-byte signature preservation guard."""
    # KastenFrequencyStore.__init__(self, supabase=None)
    init_sig = inspect.signature(KastenFrequencyStore.__init__)
    init_params = list(init_sig.parameters.values())
    assert [p.name for p in init_params] == ["self", "supabase"]
    assert init_params[1].default is None

    # get_frequencies(self, kasten_id) -> dict
    gf_sig = inspect.signature(KastenFrequencyStore.get_frequencies)
    assert [p.name for p in gf_sig.parameters.values()] == ["self", "kasten_id"]

    # record_hit(self, *, kasten_id, node_id)
    rh_sig = inspect.signature(KastenFrequencyStore.record_hit)
    rh_params = list(rh_sig.parameters.values())
    assert [p.name for p in rh_params] == ["self", "kasten_id", "node_id"]
    assert rh_params[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert rh_params[2].kind == inspect.Parameter.KEYWORD_ONLY

    # compute_frequency_penalty(node_hit_count, *, total_hits_in_kasten, floor=50)
    cfp_sig = inspect.signature(compute_frequency_penalty)
    cfp_params = list(cfp_sig.parameters.values())
    assert [p.name for p in cfp_params] == ["node_hit_count", "total_hits_in_kasten", "floor"]
    assert cfp_params[1].kind == inspect.Parameter.KEYWORD_ONLY
    assert cfp_params[2].kind == inspect.Parameter.KEYWORD_ONLY
    assert cfp_params[2].default == 50
