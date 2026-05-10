"""M4 — Natural-Language Graph Query tests.

Covers:
- Safety check rejects mutation keywords.
- Safety check rejects multi-statement SQL.
- Artifact stripper removes markdown fences.
- Happy-path: mock Gemini + mock Supabase → NLQueryResult.
- Retry on first DB error.
"""

from __future__ import annotations

import pytest

# 8.0-H7: kg_features.nl_query was hard-deleted (v1 NL->SQL translator
# referenced dropped public.kg_* tables). Skip the whole module — the public
# /api/graph/query surface is also retired (410 Gone per 8.5.C-defer).
pytest.skip(
    "v1 kg_features.nl_query retired in Phase 8.0 H7 (hybrid cleanup); "
    "see plan docs/superpowers/plans/2026-05-10-phase-8-v2-purge-closeout.md",
    allow_module_level=True,
)

from unittest.mock import MagicMock, patch  # noqa: E402

from website.features.kg_features import nl_query as nl_mod  # noqa: E402
from website.features.kg_features.nl_query import (  # noqa: E402
    NLGraphQuery,
    NLQueryError,
    NLQueryResult,
    _safety_check,
    _strip_sql_artifacts,
)


# ── Test 1 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "DELETE FROM kg_nodes WHERE user_id = 'x'",
    "INSERT INTO kg_nodes VALUES (1)",
    "UPDATE kg_nodes SET name='x'",
    "DROP TABLE kg_nodes",
])
def test_safety_check_blocks_mutation_keywords(sql):
    """All mutation SQL statements must raise NLQueryError(400)."""
    with pytest.raises(NLQueryError) as exc_info:
        _safety_check(sql)
    assert exc_info.value.status_code == 400


# ── Test 2 ───────────────────────────────────────────────────────────────────

def test_safety_check_blocks_multiple_statements():
    """Multi-statement SQL must raise NLQueryError(400)."""
    with pytest.raises(NLQueryError) as exc_info:
        _safety_check("SELECT 1; SELECT 2")
    assert exc_info.value.status_code == 400


def test_safety_check_requires_public_user_scope_and_limit():
    valid_sql = "SELECT name FROM public.kg_nodes WHERE user_id = 'user-123' LIMIT 50"
    _safety_check(valid_sql, user_id="user-123")

    with pytest.raises(NLQueryError, match="public"):
        _safety_check("SELECT name FROM kg_nodes WHERE user_id = 'user-123' LIMIT 50", user_id="user-123")

    with pytest.raises(NLQueryError, match="current user"):
        _safety_check("SELECT name FROM public.kg_nodes LIMIT 50", user_id="user-123")

    with pytest.raises(NLQueryError, match="50 rows"):
        _safety_check(
            "SELECT name FROM public.kg_nodes WHERE user_id = 'user-123' LIMIT 51",
            user_id="user-123",
        )


# ── Test 3 ───────────────────────────────────────────────────────────────────

def test_strip_sql_artifacts_removes_markdown_fences():
    """Markdown code fences with or without 'sql' lang tag must be stripped."""
    wrapped = "```sql\nSELECT * FROM public.kg_nodes LIMIT 50\n```"
    assert _strip_sql_artifacts(wrapped) == "SELECT * FROM public.kg_nodes LIMIT 50"

    wrapped_no_lang = "```\nSELECT 1\n```"
    assert _strip_sql_artifacts(wrapped_no_lang) == "SELECT 1"

    plain = "SELECT * FROM public.kg_nodes"
    assert _strip_sql_artifacts(plain) == "SELECT * FROM public.kg_nodes"


# ── Test 4 ───────────────────────────────────────────────────────────────────

async def test_nl_query_happy_path(stub_settings, mock_supabase_client):
    """Valid SELECT → SQL executes → result formatted → NLQueryResult returned."""
    # Gemini returns: (1) the SQL, (2) the formatted answer.
    sql_resp = MagicMock()
    sql_resp.text = "SELECT name FROM public.kg_nodes WHERE user_id='u' LIMIT 50"
    answer_resp = MagicMock()
    answer_resp.text = "You have 2 articles: Alpha, Beta."

    # pool.generate_content is async and returns (response, model, key_idx)
    async def fake_generate(*args, **kwargs):
        return fake_generate._responses.pop(0)
    fake_generate._responses = [
        (sql_resp, "gemini-2.5-flash", 0),
        (answer_resp, "gemini-2.5-flash", 0),
    ]

    fake_pool = MagicMock()
    fake_pool.generate_content = fake_generate

    # EXPLAIN returns NULL (success), execute returns rows.
    explain_resp = MagicMock()
    explain_resp.data = None
    exec_resp = MagicMock()
    exec_resp.data = [{"name": "Alpha"}, {"name": "Beta"}]
    execute_mock = MagicMock(side_effect=[explain_resp, exec_resp])
    rpc_handle = MagicMock()
    rpc_handle.execute = execute_mock
    mock_supabase_client.rpc.return_value = rpc_handle

    with patch.object(nl_mod, "get_key_pool", return_value=fake_pool):
        engine = NLGraphQuery(mock_supabase_client, user_id="u")
        result = await engine.ask("How many articles do I have?", user_id="u")

    assert isinstance(result, NLQueryResult)
    assert "SELECT" in result.sql.upper()
    assert len(result.raw_result) == 2
    assert result.retries == 0
    assert result.latency_ms >= 0  # timing present (may round to 0 with mocks)
    assert result.answer  # non-empty


# ── Test 5 ───────────────────────────────────────────────────────────────────

async def test_nl_query_retry_on_db_error(stub_settings):
    """First RPC raises, retry succeeds → retries == 1."""
    sql_resp_1 = MagicMock()
    sql_resp_1.text = "SELECT bad_column FROM public.kg_nodes WHERE user_id='u' LIMIT 50"
    sql_resp_2 = MagicMock()
    sql_resp_2.text = "SELECT name FROM public.kg_nodes WHERE user_id='u' LIMIT 50"
    answer_resp = MagicMock()
    answer_resp.text = "Here are your nodes."

    # pool.generate_content is async and returns (response, model, key_idx)
    async def fake_generate(*args, **kwargs):
        return fake_generate._responses.pop(0)
    fake_generate._responses = [
        (sql_resp_1, "gemini-2.5-flash", 0),
        (sql_resp_2, "gemini-2.5-flash", 0),
        (answer_resp, "gemini-2.5-flash", 0),
    ]

    fake_pool = MagicMock()
    fake_pool.generate_content = fake_generate

    # First rpc execute raises; second returns data.
    good_resp = MagicMock()
    good_resp.data = [{"name": "Alpha"}]

    sb = MagicMock()
    # Each .rpc() call creates a new chain call, so we can configure them
    # sequentially via side_effect on execute.
    execute_mock = MagicMock()
    execute_mock.side_effect = [Exception("column bad_column does not exist"), good_resp]
    rpc_handle = MagicMock()
    rpc_handle.execute = execute_mock
    sb.rpc.return_value = rpc_handle

    with patch.object(nl_mod, "get_key_pool", return_value=fake_pool):
        engine = NLGraphQuery(sb, user_id="u")
        result = await engine.ask("list my notes", user_id="u")

    assert result.retries == 1
    assert len(result.raw_result) == 1
