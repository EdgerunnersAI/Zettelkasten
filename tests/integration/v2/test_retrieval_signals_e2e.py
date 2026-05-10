"""Phase 8.5.B-7 — end-to-end signal flow + cross-Kasten guard.

Verifies:
  1. Event POSTs land in rag.retrieval_feedback_events.
  2. After REFRESH MATERIALIZED VIEW, kasten_retrieval_edge_signals reflects
     positive_signal for cite/accept events on the right (workspace, kasten,
     source, target) cell.
  3. After REFRESH MATERIALIZED VIEW, kg_edge_viz_weights reflects all
     event-type aggregates at workspace scope.
  4. Cross-Kasten guard: an event tagged kasten_A does NOT contaminate the
     signal row scoped to kasten_B (locked spec § "scope discipline").
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


@pytest.fixture
def v2_app(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _create_kastens(asyncpg_pool, workspace_id: uuid.UUID, names: list[str]) -> list[uuid.UUID]:
    """Insert N Kastens via service-role; return their ids."""
    ids = [uuid.uuid4() for _ in names]
    async with asyncpg_pool.acquire() as conn:
        for kid, name in zip(ids, names, strict=True):
            await conn.execute(
                "INSERT INTO rag.kastens (id, workspace_id, name) VALUES ($1, $2, $3)",
                kid, workspace_id, name,
            )
    return ids


def test_signal_flow_and_cross_kasten_isolation(v2_app, mint_user, asyncpg_pool):
    import asyncio
    user = mint_user(workspace_count=1)
    ws = user.workspace_ids[0]

    kasten_a, kasten_b = asyncio.get_event_loop().run_until_complete(
        _create_kastens(asyncpg_pool, ws, [f"K-A-{uuid.uuid4().hex[:8]}", f"K-B-{uuid.uuid4().hex[:8]}"])
    )

    src = uuid.uuid4()
    tgt = uuid.uuid4()

    with TestClient(v2_app) as client:
        # 1) cite event in kasten_A
        resp_a = client.post(
            "/api/rag/feedback",
            headers=_auth(user.jwt),
            json={
                "event_type": "cite",
                "workspace_id": str(ws),
                "kasten_id": str(kasten_a),
                "source_node_id": str(src),
                "target_node_id": str(tgt),
                "weight_delta": 1.0,
            },
        )
        assert resp_a.status_code == 201, resp_a.text

        # 2) cite event in kasten_B with same (src, tgt) — must stay isolated
        resp_b = client.post(
            "/api/rag/feedback",
            headers=_auth(user.jwt),
            json={
                "event_type": "cite",
                "workspace_id": str(ws),
                "kasten_id": str(kasten_b),
                "source_node_id": str(src),
                "target_node_id": str(tgt),
                "weight_delta": 5.0,  # different magnitude on purpose
            },
        )
        assert resp_b.status_code == 201, resp_b.text

        # 3) impression (workspace-scope viz event, no kasten)
        resp_imp = client.post(
            "/api/rag/feedback",
            headers=_auth(user.jwt),
            json={
                "event_type": "impression",
                "workspace_id": str(ws),
                "source_node_id": str(src),
                "target_node_id": str(tgt),
                "rank_at_render": 1,
                "propensity_weight": 1.0,
            },
        )
        assert resp_imp.status_code == 201, resp_imp.text

    async def _verify():
        async with asyncpg_pool.acquire() as conn:
            await conn.execute("SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')")
            await conn.execute("SELECT kg.refresh_signal_mv('kg.kg_edge_viz_weights')")

            row_a = await conn.fetchrow(
                "SELECT positive_signal, event_count "
                "FROM rag.kasten_retrieval_edge_signals "
                "WHERE workspace_id=$1 AND kasten_id=$2 AND source_node_id=$3 AND target_node_id=$4",
                ws, kasten_a, src, tgt,
            )
            row_b = await conn.fetchrow(
                "SELECT positive_signal, event_count "
                "FROM rag.kasten_retrieval_edge_signals "
                "WHERE workspace_id=$1 AND kasten_id=$2 AND source_node_id=$3 AND target_node_id=$4",
                ws, kasten_b, src, tgt,
            )
            viz = await conn.fetchrow(
                "SELECT viz_weight, event_count FROM kg.kg_edge_viz_weights "
                "WHERE workspace_id=$1 AND source_node_id=$2 AND target_node_id=$3",
                ws, src, tgt,
            )
            return row_a, row_b, viz

    row_a, row_b, viz = asyncio.get_event_loop().run_until_complete(_verify())

    # Cross-Kasten isolation: A's row weight = 1.0; B's row weight = 5.0; never co-mingled
    assert row_a is not None, "kasten_A signal row missing"
    assert row_b is not None, "kasten_B signal row missing"
    assert float(row_a["positive_signal"]) == pytest.approx(1.0), (
        f"kasten_A positive_signal expected 1.0, got {row_a['positive_signal']}"
    )
    assert float(row_b["positive_signal"]) == pytest.approx(5.0), (
        f"kasten_B positive_signal expected 5.0, got {row_b['positive_signal']} "
        "(cross-Kasten contamination from A would push this != 5.0)"
    )

    # Viz MV (workspace-scope, all event types): aggregates impression + 2× cite = 7.0
    assert viz is not None, "viz row missing"
    # impression contributes weight_delta=1.0; cite events do NOT contribute to viz MV (cite/accept go to retrieval MV only)
    # Actually viz MV INCLUDES 'cite' per file 35 SELECT. So weight = 1.0 + 1.0 + 5.0 = 7.0.
    # Wait: viz MV WHERE event_type IN (impression, click, dwell, cite, expand, follow_up) — cite IS included.
    assert float(viz["viz_weight"]) == pytest.approx(7.0), (
        f"viz_weight expected 7.0 (impression 1 + cite-A 1 + cite-B 5), got {viz['viz_weight']}"
    )
    assert int(viz["event_count"]) == 3
