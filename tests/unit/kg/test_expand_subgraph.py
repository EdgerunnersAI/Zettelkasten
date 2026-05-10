"""Tests for ``expand_subgraph`` in ``website.features.kg_features.retrieval``.

The function is a thin Python wrapper around the ``kg_expand_subgraph``
Supabase RPC (a recursive-CTE BFS walk).  These tests pin the wrapper's
contract: argument shape, RPC payload, return mapping, input-mutation
safety, and trivial edge cases.  The recursion / cycle / dedup logic
itself lives in SQL and is exercised by integration tests (T21).
"""

from __future__ import annotations

import pytest

# 8.0-H7: kg_features.retrieval.expand_subgraph was hard-deleted (v1 RPC
# kg_expand_subgraph against dropped public.kg_links). v2 equivalent lives in
# website/core/supabase_v2/repositories/kg_repository.py:90 and is covered by
# tests/unit/supabase_v2/.
pytest.skip(
    "v1 kg_features.retrieval.expand_subgraph retired in Phase 8.0 H7; "
    "v2 equivalent in KGRepository.expand_subgraph (see "
    "docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md)",
    allow_module_level=True,
)

from copy import deepcopy  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from website.features.kg_features.retrieval import expand_subgraph  # noqa: E402


def _client_returning(rows):
    """Build a MagicMock Supabase client whose .rpc(...).execute() returns ``rows``."""
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = rows
    return sb


def test_empty_seed_returns_empty_and_skips_rpc():
    sb = _client_returning([{"id": "anything"}])
    result = expand_subgraph(sb, user_id="u", node_ids=[], depth=2)
    assert result == []
    sb.rpc.assert_not_called()


def test_depth1_returns_neighbours():
    sb = _client_returning([{"id": "n1"}, {"id": "n2"}, {"id": "n3"}])
    result = expand_subgraph(sb, user_id="u", node_ids=["n0"], depth=1)
    assert set(result) == {"n1", "n2", "n3"}
    sb.rpc.assert_called_once()
    name, payload = sb.rpc.call_args.args
    assert name == "kg_expand_subgraph"
    assert payload == {
        "p_user_id": "u",
        "p_node_ids": ["n0"],
        "p_depth": 1,
    }


def test_depth2_returns_two_hop_union_deduped():
    # The SQL is responsible for dedup; we verify the wrapper passes rows
    # through faithfully and preserves whatever the RPC returned.
    sb = _client_returning([{"id": "n1"}, {"id": "n2"}, {"id": "n5"}, {"id": "n9"}])
    result = expand_subgraph(sb, user_id="u", node_ids=["n0"], depth=2)
    assert result == ["n1", "n2", "n5", "n9"]
    payload = sb.rpc.call_args.args[1]
    assert payload["p_depth"] == 2


def test_does_not_mutate_inputs():
    seeds = ["n0", "n1"]
    seeds_snapshot = deepcopy(seeds)
    sb = _client_returning([{"id": "n2"}])
    expand_subgraph(sb, user_id="u", node_ids=seeds, depth=1)
    assert seeds == seeds_snapshot


def test_handles_none_data_gracefully():
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value.data = None
    result = expand_subgraph(sb, user_id="u", node_ids=["n0"], depth=1)
    assert result == []


def test_user_id_coerced_to_string():
    """UUID objects should be stringified for JSON-serialisable RPC payload."""
    import uuid

    uid = uuid.uuid4()
    sb = _client_returning([])
    expand_subgraph(sb, user_id=uid, node_ids=["n0"], depth=1)
    payload = sb.rpc.call_args.args[1]
    assert payload["p_user_id"] == str(uid)
    assert isinstance(payload["p_user_id"], str)


def test_default_depth_is_one():
    sb = _client_returning([])
    expand_subgraph(sb, user_id="u", node_ids=["n0"])
    payload = sb.rpc.call_args.args[1]
    assert payload["p_depth"] == 1
