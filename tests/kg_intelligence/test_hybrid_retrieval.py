"""M6 — Hybrid Retrieval tests.

Covers:
- RPC is called with parameter names matching the SQL function signature
  (catches the ``p_query_text`` vs ``query_text`` P1 bug).
- Weight normalisation when callers pass non-normalised weights.
- Fallback behaviour when embedding generation fails: sem_w=0.0 and the
  remaining weights are renormalised to sum to 1.0.
"""

from __future__ import annotations

import pytest

# 8.0-H7: kg_features.retrieval was hard-deleted (v1 hybrid_kg_search RPC
# referenced dropped public.kg_nodes/kg_links). Skip the whole module rather
# than rewriting against v2; v2 hybrid retrieval is covered by
# tests/integration/v2/test_hybrid_pipeline_e2e.py.
pytest.skip(
    "v1 kg_features.retrieval retired in Phase 8.0 H7 (hybrid cleanup); "
    "see plan docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md",
    allow_module_level=True,
)

from unittest.mock import patch  # noqa: E402

from website.features.kg_features import retrieval as retr_mod  # noqa: E402
from website.features.kg_features.retrieval import hybrid_search  # noqa: E402


USER_ID = "11111111-1111-1111-1111-111111111111"


# ── Test 1 ───────────────────────────────────────────────────────────────────

def test_hybrid_search_passes_correct_rpc_params(mock_supabase_client):
    """hybrid_search must call rpc('hybrid_kg_search', ...) with parameter
    names matching the SQL function signature.  The SQL declares:
      query_text, query_embedding, semantic_weight, fulltext_weight,
      graph_weight, match_count (and user_id scoping param).
    The code sends ``p_`` prefixed versions which DO NOT match SQL
    (P1 bug #1 from verification report).
    """
    mock_supabase_client.rpc.return_value.execute.return_value.data = []
    fake_embedding = [0.1] * 768
    with patch.object(retr_mod, "generate_embedding", return_value=fake_embedding):
        hybrid_search(mock_supabase_client, user_id=USER_ID, query="react hooks")

    call = mock_supabase_client.rpc.call_args
    assert call.args[0] == "hybrid_kg_search"
    params = call.args[1]

    # EXPECTED FAILURE: documents P1 bug — code currently passes the
    # p_-prefixed variants. SQL expects non-prefixed names.
    assert "query_text" in params, (
        "RPC param 'query_text' missing. "
        "EXPECTED FAILURE: documents P1 bug (code uses 'p_query_text')."
    )
    assert "query_embedding" in params
    assert "semantic_weight" in params
    assert "fulltext_weight" in params
    assert "graph_weight" in params


# ── Test 2 ───────────────────────────────────────────────────────────────────

def test_hybrid_search_normalizes_weights(mock_supabase_client):
    """Non-normalised weights (1.0, 1.0, 1.0) → normalised to (1/3, 1/3, 1/3)."""
    mock_supabase_client.rpc.return_value.execute.return_value.data = []
    fake_embedding = [0.1] * 768
    with patch.object(retr_mod, "generate_embedding", return_value=fake_embedding):
        hybrid_search(
            mock_supabase_client,
            user_id=USER_ID,
            query="q",
            semantic_weight=1.0,
            fulltext_weight=1.0,
            graph_weight=1.0,
        )

    params = mock_supabase_client.rpc.call_args.args[1]
    # Support either param naming convention for robustness, but assert
    # that whatever was sent, weights were normalised.
    sem = params.get("semantic_weight", params.get("p_semantic_weight"))
    ft = params.get("fulltext_weight", params.get("p_fulltext_weight"))
    gr = params.get("graph_weight", params.get("p_graph_weight"))

    assert sem == pytest.approx(1 / 3, abs=1e-6)
    assert ft == pytest.approx(1 / 3, abs=1e-6)
    assert gr == pytest.approx(1 / 3, abs=1e-6)
    # And the sum must be 1.0.
    assert sem + ft + gr == pytest.approx(1.0, abs=1e-6)


# ── Test 3 ───────────────────────────────────────────────────────────────────

def test_hybrid_search_fallback_when_embedding_fails(mock_supabase_client):
    """If generate_embedding returns [], semantic weight is zeroed out and
    fulltext + graph weights are renormalised to sum to 1.0.
    """
    mock_supabase_client.rpc.return_value.execute.return_value.data = []
    with patch.object(retr_mod, "generate_embedding", return_value=[]):
        hybrid_search(
            mock_supabase_client,
            user_id=USER_ID,
            query="q",
            semantic_weight=0.5,
            fulltext_weight=0.3,
            graph_weight=0.2,
        )

    params = mock_supabase_client.rpc.call_args.args[1]
    sem = params.get("semantic_weight", params.get("p_semantic_weight"))
    ft = params.get("fulltext_weight", params.get("p_fulltext_weight"))
    gr = params.get("graph_weight", params.get("p_graph_weight"))

    assert sem == pytest.approx(0.0, abs=1e-6)
    assert ft + gr == pytest.approx(1.0, abs=1e-6)
    # Relative ratio between ft and gr should be preserved: 0.3 : 0.2 = 0.6 : 0.4.
    assert ft == pytest.approx(0.6, abs=1e-6)
    assert gr == pytest.approx(0.4, abs=1e-6)
