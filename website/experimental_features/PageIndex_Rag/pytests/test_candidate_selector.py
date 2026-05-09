from pathlib import Path

import pytest

# Phase 3.6: PageIndex_Rag.data_access was retired pending v2 redesign.
# Skip these tests at collection time so the retirement doesn't break CI.
pytest.skip(
    "PageIndex_Rag.data_access retired in Phase 3.6; "
    "tests will be re-enabled when the module is rewritten against the v2 schema",
    allow_module_level=True,
)

from website.experimental_features.PageIndex_Rag.candidate_selector import select_candidates  # noqa: E402
from website.experimental_features.PageIndex_Rag.data_access import _content_from_row, scope_from_fixture  # noqa: E402
from website.experimental_features.PageIndex_Rag.types import PageIndexDocument, ZettelRecord  # noqa: E402


def test_scope_from_knowledge_management_fixture():
    meta = {
        "kasten_slug": "knowledge-management",
        "kasten_name": "Knowledge Management & Personal Productivity",
        "members_node_ids": ["a", "b", "c"],
    }
    scope = scope_from_fixture(meta, user_id="user-1")
    assert scope.scope_id == "knowledge-management:iter-01"
    assert scope.node_ids == ("a", "b", "c")
    assert scope.membership_hash == "a|b|c"


def test_candidate_selector_prefers_matching_zettel():
    zettels = [
        ZettelRecord("u", "sleep", "Sleep deprivation", "working memory attention", "", "youtube", None, ("sleep",), {}),
        ZettelRecord("u", "zk", "zk personal wiki", "markdown notes", "", "github", None, ("zettelkasten",), {}),
    ]
    docs = {
        "sleep": PageIndexDocument("u", "sleep", "h1", "d1", Path("a.md"), Path("a.json")),
        "zk": PageIndexDocument("u", "zk", "h2", "d2", Path("b.md"), Path("b.json")),
    }
    result = select_candidates(query="install personal wiki", zettels=zettels, documents=docs, limit=1)
    assert [item.node_id for item in result] == ["zk"]


def test_candidate_selector_boosts_exact_title_phrase_over_broad_overlap():
    zettels = [
        ZettelRecord("u", "heat", "Urban Heat Islands Explained", "urban heat planning", "", "youtube", None, (), {}),
        ZettelRecord("u", "roofs", "Cool Roofs and Reflective Surfaces", "building heat mitigation", "", "web", None, (), {}),
    ]
    docs = {
        "heat": PageIndexDocument("u", "heat", "h1", "d1", Path("a.md"), Path("a.json")),
        "roofs": PageIndexDocument("u", "roofs", "h2", "d2", Path("b.md"), Path("b.json")),
    }
    result = select_candidates(query="Jane Jacobs and cool roofs in urban planning", zettels=zettels, documents=docs, limit=1)
    assert [item.node_id for item in result] == ["roofs"]


def test_content_from_row_uses_summary_v2_without_content_column():
    row = {
        "summary": "brief",
        "summary_v2": {"detailed_summary": "detailed"},
        "metadata": {},
    }
    assert _content_from_row(row) == "detailed"
