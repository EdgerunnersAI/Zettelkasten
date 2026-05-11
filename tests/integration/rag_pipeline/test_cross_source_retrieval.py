"""Cross-source Kasten retrieval test (live).

Verifies the retriever surfaces the correct source-type Zettel for a query
when a single Kasten mixes youtube + github + newsletter + reddit.

Run with: pytest --live tests/integration/rag_pipeline/test_cross_source_retrieval.py
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
from website.features.rag_pipeline.service import get_rag_runtime  # noqa: E402
from website.features.rag_pipeline.types import ChatQuery, ScopeFilter  # noqa: E402


NARUTO_KG_USER_ID = UUID("8842e563-ee10-4b8b-bbf2-8af4ba65888e")

# Targeted node ids — verified to exist in Naruto's KG per docs/rag_eval/_config/_naruto_baseline.json.
# Reddit node is picked at runtime since Naruto's reddit set is volatile.
TARGET_YOUTUBE = "yt-andrej-karpathy-s-llm-in"
TARGET_GITHUB_FALLBACK = "gh-pydantic-pydantic"
TARGET_NEWSLETTER = "nl-the-pragmatic-engineer-t"


def _pick_first_node_with_prefix(prefix: str) -> str | None:
    sb = get_supabase_client()
    res = (
        sb.table("kg_nodes")
        .select("id")
        .eq("user_id", str(NARUTO_KG_USER_ID))
        .like("id", f"{prefix}%")
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]["id"]


@pytest.mark.live
def test_cross_source_kasten_routes_to_correct_source_type(skip_live):
    """For 3 queries each targeting a different source type, retrieval+rerank
    must surface the right source-type Zettel as citations[0] in at least 2/3.
    """
    if not is_supabase_configured():
        pytest.skip("Supabase not configured")

    # --- Resolve mixed Kasten ----------------------------------------------
    yt_id = TARGET_YOUTUBE
    gh_id = _pick_first_node_with_prefix("gh-") or TARGET_GITHUB_FALLBACK
    nl_id = TARGET_NEWSLETTER
    rd_id = _pick_first_node_with_prefix("rd-")
    node_ids = [n for n in [yt_id, gh_id, nl_id, rd_id] if n]
    assert len(node_ids) >= 3, f"Expected ≥3 cross-source nodes, got {node_ids}"

    runtime = get_rag_runtime(None)
    # Override kg_user_id to Naruto's
    object.__setattr__(runtime, "kg_user_id", NARUTO_KG_USER_ID)

    cases = [
        ("What does the pydantic library do?", gh_id, "gh-"),
        ("What does The Pragmatic Engineer write about?", nl_id, "nl-"),
        ("What does Karpathy say about LLM training stages?", yt_id, "yt-"),
    ]

    correct = 0
    async def _run_one(content: str) -> list[str]:
        q = ChatQuery(
            content=content,
            scope_filter=ScopeFilter(node_ids=node_ids),
            quality="fast",
            stream=False,
        )
        turn = await runtime.orchestrator.answer(query=q, user_id=NARUTO_KG_USER_ID)
        return [c.node_id for c in (turn.citations or [])]

    for question, expected_id, expected_prefix in cases:
        cite_ids = asyncio.run(_run_one(question))
        if cite_ids and (
            cite_ids[0] == expected_id or cite_ids[0].startswith(expected_prefix)
        ):
            correct += 1

    assert correct >= 2, f"Cross-source routing too weak: {correct}/3 correct"
