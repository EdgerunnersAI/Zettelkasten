from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
V2_DIR = ROOT / "supabase" / "website" / "_v2"


def _sql(name: str) -> str:
    return (V2_DIR / name).read_text(encoding="utf-8")


def test_all_v2_schema_files_exist_in_apply_order() -> None:
    names = [p.name for p in sorted(V2_DIR.glob("*.sql"))]
    assert names == [
        "00_extensions.sql",
        "01_core_schema.sql",
        "02_content_schema.sql",
        "03_kg_schema.sql",
        "04_rag_schema.sql",
        "05_pipelines_schema.sql",
        "06_billing_schema.sql",
        "07_partman_setup.sql",
        "08_rls_policies.sql",
        "09_seed_scorer_registry.sql",
        "10_hnsw_indexes.sql",
        "11_post_install.sql",
        "12_revert_unauthorized_pricing.sql",
        "13_v2_kasten_rpcs.sql",
        "16_nexus_tokens.sql",
        "17_content_rpcs.sql",
        "19_enriched_search_rpc.sql",
        "20_hybrid_search_rpc.sql",
    ]


def test_v2_schema_declares_39_tables() -> None:
    combined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(V2_DIR.glob("0*.sql")))
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS ([a-z_]+\.[a-z_]+)", combined)
    assert len(set(tables)) == 39


def test_jwt_workspace_ids_uses_safe_jsonb_array_cast() -> None:
    sql = _sql("01_core_schema.sql")
    assert "jsonb_array_elements_text" in sql
    assert "::text::uuid[]" not in sql


def test_hnsw_is_only_in_post_backfill_file() -> None:
    pre_backfill = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(V2_DIR.glob("0*.sql"))
        if p.name != "10_hnsw_indexes.sql"
    )
    assert "USING hnsw" not in pre_backfill
    assert "USING hnsw" in _sql("10_hnsw_indexes.sql")


def test_search_chunks_and_quota_are_typed_rpcs() -> None:
    content_sql = _sql("02_content_schema.sql")
    core_sql = _sql("01_core_schema.sql")
    kg_sql = _sql("03_kg_schema.sql")
    assert "CREATE OR REPLACE FUNCTION content.search_chunks" in content_sql
    assert "p_query_embedding halfvec(768)" in content_sql
    assert "CREATE OR REPLACE FUNCTION core.consume_quota" in core_sql
    assert "CREATE OR REPLACE FUNCTION core.is_service_role()" in core_sql
    assert "CREATE OR REPLACE FUNCTION core.jwt_has_workspace_role" in core_sql
    assert "core.is_service_role() OR p_workspace_id = ANY" in core_sql
    assert "core.is_service_role() OR p_workspace_id = ANY" in content_sql
    assert "core.is_service_role() OR p_workspace_id = ANY" in kg_sql
    assert "exec_sql_returning" not in core_sql


def test_search_chunks_excludes_null_embeddings() -> None:
    sql = _sql("02_content_schema.sql")
    assert "AND cc.embedding IS NOT NULL" in sql


def test_citation_reaper_ignores_malformed_citation_ids() -> None:
    sql = _sql("02_content_schema.sql")
    assert "c ? 'canonical_chunk_id'" in sql
    assert "(c ->> 'canonical_chunk_id') ~*" in sql
    assert "(c ->> 'canonical_chunk_id')::uuid" in sql


def test_citation_reaper_skips_chat_message_citations() -> None:
    sql = _sql("02_content_schema.sql")
    assert "rag.chat_messages" in sql
    assert "canonical_chunk_id" in sql


def test_rls_keeps_canonical_chunks_service_role_only() -> None:
    sql = _sql("08_rls_policies.sql")
    assert "canonical_chunks_service_all" in sql
    assert "FOR SELECT TO authenticated" not in sql.split("canonical_chunks_service_all")[0]


def test_rls_uses_roles_for_workspace_writes() -> None:
    sql = _sql("08_rls_policies.sql")
    assert "core.jwt_has_workspace_role(workspace_id, ARRAY['owner', 'editor'])" in sql
    assert "core.jwt_has_workspace_role(workspace_id, ARRAY['owner'])" in sql
    assert "kasten_members_workspace_insert" in sql
    assert "chat_messages_workspace_insert" in sql
