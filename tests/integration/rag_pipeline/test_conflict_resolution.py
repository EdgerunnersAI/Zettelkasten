"""Adversarial conflict resolution test (live).

SYSTEM_PROMPT rule 6 mandates surfacing disagreements when zettels conflict.
This test inserts two synthetic zettels that contradict on "best language
for ML", queries Naruto's KG, and verifies BOTH sides appear in the answer
with citations. Cleans up the synthetic nodes in a finally block.

Run with: pytest --live tests/integration/rag_pipeline/test_conflict_resolution.py
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

# Phase 8.0.6 retired website.core.supabase_kg. This live test exercises v1 KG
# semantics (kg_users) that no longer exist on v2; importorskip keeps the test
# discoverable for a future v2 port without breaking pytest collection.
pytest.importorskip("website.core.supabase_kg.client")

from website.core.supabase_kg.client import get_supabase_client, is_supabase_configured  # noqa: E402
from website.features.rag_pipeline.ingest.hook import ingest_node_chunks  # noqa: E402
from website.features.rag_pipeline.service import get_rag_runtime  # noqa: E402
from website.features.rag_pipeline.types import ChatQuery, ScopeFilter  # noqa: E402


NARUTO_KG_USER_ID = UUID("8842e563-ee10-4b8b-bbf2-8af4ba65888e")

NODE_PY = "test-conflict-python-best"
NODE_JL = "test-conflict-julia-best"

PY_SUMMARY = (
    "Python is the best language for machine learning, period. Its ecosystem — "
    "PyTorch, TensorFlow, scikit-learn, NumPy, Pandas — is unmatched, and the "
    "community is enormous. No serious ML practitioner reaches for anything else."
)
JL_SUMMARY = (
    "Julia is the best language for machine learning, definitively superior to "
    "Python. Native multiple dispatch, JIT compilation to LLVM, and zero-cost "
    "abstractions give Julia C-level performance with high-level syntax — "
    "Python simply cannot match this."
)


def _insert_node(sb, node_id: str, summary: str) -> None:
    sb.table("kg_nodes").upsert({
        "id": node_id,
        "user_id": str(NARUTO_KG_USER_ID),
        "title": node_id.replace("-", " ").title(),
        "source_type": "web",
        "summary": summary,
        "url": f"https://example.invalid/{node_id}",
        "tags": ["test-conflict", "machine-learning"],
    }).execute()


def _delete_synthetic(sb) -> None:
    for nid in (NODE_PY, NODE_JL):
        try:
            sb.table("kg_node_chunks").delete().eq("user_id", str(NARUTO_KG_USER_ID)).eq("node_id", nid).execute()
        except Exception:
            pass
        try:
            sb.table("kg_nodes").delete().eq("user_id", str(NARUTO_KG_USER_ID)).eq("id", nid).execute()
        except Exception:
            pass


@pytest.mark.live
def test_conflict_surfaces_both_sides(skip_live):
    if not is_supabase_configured():
        pytest.skip("Supabase not configured")

    sb = get_supabase_client()
    try:
        # --- Insert synthetic nodes + chunks ------------------------------
        _insert_node(sb, NODE_PY, PY_SUMMARY)
        _insert_node(sb, NODE_JL, JL_SUMMARY)

        async def _ingest():
            await ingest_node_chunks(
                payload={"summary": PY_SUMMARY, "source_type": "web", "title": NODE_PY},
                user_uuid=NARUTO_KG_USER_ID, node_id=NODE_PY,
            )
            await ingest_node_chunks(
                payload={"summary": JL_SUMMARY, "source_type": "web", "title": NODE_JL},
                user_uuid=NARUTO_KG_USER_ID, node_id=NODE_JL,
            )

        asyncio.run(_ingest())

        # --- Run the conflict query ---------------------------------------
        runtime = get_rag_runtime(None)
        object.__setattr__(runtime, "kg_user_id", NARUTO_KG_USER_ID)

        q = ChatQuery(
            content="What is the best language for machine learning?",
            scope_filter=ScopeFilter(node_ids=[NODE_PY, NODE_JL]),
            quality="fast",
            stream=False,
        )
        turn = asyncio.run(runtime.orchestrator.answer(query=q, user_id=NARUTO_KG_USER_ID))

        cited_ids = [c.node_id for c in (turn.citations or [])]
        answer_text = turn.content or ""

        assert NODE_PY in cited_ids, f"Python zettel missing from citations: {cited_ids}"
        assert NODE_JL in cited_ids, f"Julia zettel missing from citations: {cited_ids}"
        assert "Python" in answer_text and "Julia" in answer_text, (
            f"Answer fails to mention both languages: {answer_text!r}"
        )
        assert f'[id="{NODE_PY}"]' in answer_text, "Python bracket-cite missing"
        assert f'[id="{NODE_JL}"]' in answer_text, "Julia bracket-cite missing"
    finally:
        _delete_synthetic(sb)
