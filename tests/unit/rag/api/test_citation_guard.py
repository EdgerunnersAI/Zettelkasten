"""iter-12 Task 30 R3 Tier-1: citation guard tests."""


def test_drift_when_primary_not_in_retrieved():
    from website.api._citation_guard import check_cited_in_context
    assert check_cited_in_context(
        primary_citation="hallucinated",
        retrieved_node_ids={"a", "b", "c"},
        qid="q-test",
    ) is False


def test_no_drift_when_primary_in_retrieved():
    from website.api._citation_guard import check_cited_in_context
    assert check_cited_in_context(
        primary_citation="a",
        retrieved_node_ids={"a", "b", "c"},
    ) is True


def test_no_drift_when_no_primary():
    """Refusal path: no primary → no drift."""
    from website.api._citation_guard import check_cited_in_context
    assert check_cited_in_context(
        primary_citation=None,
        retrieved_node_ids={"a", "b", "c"},
    ) is True
