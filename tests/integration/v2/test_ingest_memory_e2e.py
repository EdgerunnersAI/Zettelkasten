"""Phase 2.7 cumulative e2e: ingest -> kasten -> chat session+messages.

Drives the full v2 memory stack against the live Supabase project:

  1. Ingest — service-role asyncpg seeds a fake zettel directly into
     ``content.canonical_zettels``, ``content.canonical_chunks`` and
     ``content.workspace_chunk_membership`` (mimicking what the upsert
     pipeline lands).
  2. Sandbox/Kasten — ``SandboxStore.create_sandbox`` + ``add_members`` +
     ``list_members`` exercise ``rag.kastens`` / ``rag.kasten_zettels``
     and the ``rag.list_kasten_zettels`` SECURITY DEFINER RPC.
  3. Session — ``ChatSessionStore.create_session`` opens a row in
     ``rag.chat_sessions``; two messages (user + assistant) round-trip
     through ``rag.chat_messages``; both inserts MUST carry workspace_id
     (NOT NULL constraint + workspace-match trigger).

Cleanup: every artefact CASCADEs from ``auth.users`` via the
``mint_user`` fixture's teardown.

Marked ``@pytest.mark.live``.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from website.core.supabase_v2.repositories.core_repository import CoreRepository
from website.core.supabase_v2.repositories.rag_repository import RAGRepository
from website.features.rag_pipeline.memory.sandbox_store import SandboxStore
from website.features.rag_pipeline.memory.session_store import ChatSessionStore
from website.features.rag_pipeline.types import (
    AnswerTurn,
    Citation,
    QueryClass,
    SourceType,
)


pytestmark = pytest.mark.live


def _embedding_literal(seed: float = 0.0) -> str:
    base = 0.001 + seed
    vals = [round(base + i * 1e-5, 6) for i in range(768)]
    return "[" + ",".join(f"{v:.6f}" for v in vals) + "]"


async def _seed_zettel(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    title: str,
    chunk_contents: list[str],
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Mimics the upsert pipeline: canonical_zettel + canonical_chunks +
    workspace_zettel + workspace_chunk_membership rows. Returns (cz_id, wz_id, chunk_ids).
    """
    async with pool.acquire() as conn:
        cz_id = uuid.uuid4()
        norm_url = f"https://example.test/{uuid.uuid4().hex}"
        ch = uuid.uuid4().bytes + uuid.uuid4().bytes
        await conn.execute(
            """
            INSERT INTO content.canonical_zettels
                (id, normalized_url, content_hash, source_type, title,
                 body_md, publication_date)
            VALUES ($1, $2, $3, 'web', $4, $5, '2026-04-01'::date)
            """,
            cz_id, norm_url, ch, title, "body",
        )
        chunk_ids: list[uuid.UUID] = []
        for i, body in enumerate(chunk_contents):
            cc_id = uuid.uuid4()
            chunk_hash = uuid.uuid4().bytes + uuid.uuid4().bytes
            emb = _embedding_literal(0.0 + i * 0.001)
            await conn.execute(
                f"""
                INSERT INTO content.canonical_chunks
                    (id, canonical_zettel_id, chunk_idx, content,
                     content_hash, chunk_type, embedding)
                VALUES ($1, $2, $3, $4, $5, 'atomic', '{emb}'::halfvec(768))
                """,
                cc_id, cz_id, i, body, chunk_hash,
            )
            chunk_ids.append(cc_id)
        wz_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO content.workspace_zettels
                (id, workspace_id, canonical_zettel_id, added_via)
            VALUES ($1, $2, $3, 'website')
            """,
            wz_id, workspace_id, cz_id,
        )
        for cc_id in chunk_ids:
            await conn.execute(
                """
                INSERT INTO content.workspace_chunk_membership
                    (workspace_id, canonical_chunk_id, workspace_zettel_id)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
                """,
                workspace_id, cc_id, wz_id,
            )
    return cz_id, wz_id, chunk_ids


async def _verify_seed_landed(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    cz_id: uuid.UUID,
    wz_id: uuid.UUID,
    chunk_ids: list[uuid.UUID],
) -> None:
    async with pool.acquire() as conn:
        cz_count = await conn.fetchval(
            "SELECT count(*) FROM content.canonical_zettels WHERE id = $1", cz_id,
        )
        assert cz_count == 1, "canonical_zettel did not land"
        cc_count = await conn.fetchval(
            "SELECT count(*) FROM content.canonical_chunks WHERE canonical_zettel_id = $1",
            cz_id,
        )
        assert cc_count == len(chunk_ids), (
            f"expected {len(chunk_ids)} canonical_chunks, got {cc_count}"
        )
        wz_count = await conn.fetchval(
            "SELECT count(*) FROM content.workspace_zettels "
            "WHERE id = $1 AND workspace_id = $2",
            wz_id, workspace_id,
        )
        assert wz_count == 1, "workspace_zettel did not land"
        wcm_count = await conn.fetchval(
            "SELECT count(*) FROM content.workspace_chunk_membership "
            "WHERE workspace_zettel_id = $1 AND workspace_id = $2",
            wz_id, workspace_id,
        )
        assert wcm_count == len(chunk_ids), (
            f"expected {len(chunk_ids)} membership rows, got {wcm_count}"
        )


@pytest.mark.asyncio
async def test_ingest_memory_e2e(mint_user, asyncpg_pool):
    """Cumulative ingest + memory pipeline — every layer touches the live v2 stack."""
    user = mint_user(workspace_count=1)
    ws_id = user.workspace_ids[0]

    # ── Step 1: Ingest a zettel via the service-role pool. ──────────────
    cz_id, wz_id, chunk_ids = await _seed_zettel(
        asyncpg_pool,
        workspace_id=ws_id,
        title="V2 e2e seed zettel",
        chunk_contents=[
            "lorem ipsum dolor sit amet",
            "consectetur adipiscing elit",
        ],
    )
    await _verify_seed_landed(
        asyncpg_pool, workspace_id=ws_id, cz_id=cz_id, wz_id=wz_id,
        chunk_ids=chunk_ids,
    )

    # ── Step 2: Kasten — create + add zettel + list members. ───────────
    sandbox_store = SandboxStore()
    kasten = await sandbox_store.create_sandbox(
        workspace_id=ws_id,
        name=f"e2e-kasten-{uuid.uuid4().hex[:6]}",
        description="phase 2.7 cumulative e2e",
    )
    assert kasten and kasten.get("id"), f"create_sandbox returned no id: {kasten!r}"
    kasten_id = uuid.UUID(str(kasten["id"]))

    added = await sandbox_store.add_members(
        sandbox_id=kasten_id,
        workspace_id=ws_id,
        workspace_zettel_ids=[wz_id],
    )
    assert added == 1, f"expected 1 membership row, got {added}"

    members = await sandbox_store.list_members(kasten_id, ws_id)
    assert members, "list_members returned empty after add_members"
    seeded_titles = {row.get("title") for row in members}
    assert "V2 e2e seed zettel" in seeded_titles, (
        f"expected seeded zettel title in kasten members; got {seeded_titles!r}"
    )

    # ── Step 3: Chat session + 2 messages — workspace_id propagation. ──
    # ChatSessionStore derives workspace_id from the profile via
    # core.workspace_members. Pre-populate cache so the test does not
    # depend on JWT/RLS — service-role CoreRepository can read any row.
    core_repo = CoreRepository()
    rag_repo = RAGRepository()
    session_store = ChatSessionStore(repo=rag_repo, core_repo=core_repo)

    session_id = await session_store.create_session(
        user_id=user.profile_id,
        sandbox_id=kasten_id,
        title="phase 2.7 e2e chat",
    )
    assert session_id, "create_session returned falsy id"
    session_uuid = uuid.UUID(str(session_id))

    # Verify workspace_id propagated on the session row.
    async with asyncpg_pool.acquire() as conn:
        sess_ws = await conn.fetchval(
            "SELECT workspace_id FROM rag.chat_sessions WHERE id = $1", session_uuid,
        )
        assert sess_ws == ws_id, (
            f"chat_sessions.workspace_id mismatch: {sess_ws!r} != {ws_id!r}"
        )

    user_msg = await session_store.append_user_message(
        session_id=session_uuid,
        user_id=user.profile_id,
        content="What is in the seed zettel?",
    )
    assert user_msg and user_msg.get("id")

    assistant_turn = AnswerTurn(
        content="The seed zettel covers lorem ipsum content.",
        citations=[
            Citation(
                id="c1",
                node_id=str(cz_id),
                title="V2 e2e seed zettel",
                source_type=SourceType.WEB,
                url="https://example.test/seed",
                snippet="lorem ipsum",
                rerank_score=0.87,
            )
        ],
        query_class=QueryClass.LOOKUP,
        critic_verdict="supported",
        critic_notes=None,
        trace_id=f"trace-{uuid.uuid4().hex[:6]}",
        latency_ms=123,
        token_counts={"input": 50, "output": 25},
        llm_model="gemini-2.5-flash",
        retrieved_node_ids=[str(cz_id)],
        retrieved_chunk_ids=[chunk_ids[0]],
    )
    asst_msg = await session_store.append_assistant_message(
        session_id=session_uuid,
        user_id=user.profile_id,
        turn=assistant_turn,
    )
    assert asst_msg and asst_msg.get("id")

    # Retrieve messages back, in order, with workspace_id populated on every row.
    msgs = await session_store.list_messages(session_uuid, user.profile_id, limit=10)
    assert len(msgs) == 2, f"expected 2 messages back, got {len(msgs)}"
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[0]["content"] == "What is in the seed zettel?"
    assert msgs[1]["content"].startswith("The seed zettel covers")
    for m in msgs:
        assert m["workspace_id"] == str(ws_id), (
            f"chat_messages.workspace_id missing/mismatched: {m.get('workspace_id')!r}"
        )

    # Final invariant: the session_store derived workspace_id correctly via
    # the profile_id -> workspace_members lookup (no NULL inserts hit the DB).
    async with asyncpg_pool.acquire() as conn:
        msg_ws = await conn.fetchval(
            "SELECT count(*) FROM rag.chat_messages "
            "WHERE session_id = $1 AND workspace_id = $2",
            session_uuid, ws_id,
        )
        assert msg_ws == 2, f"expected 2 chat_messages with workspace_id={ws_id}; got {msg_ws}"
