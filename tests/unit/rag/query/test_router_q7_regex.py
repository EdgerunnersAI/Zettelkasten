"""iter-12 Phase 6 / Task 11: Q7 vague-discovery regex with 3 guards."""
from website.features.rag_pipeline.query.router import (
    apply_class_overrides, ROUTER_VERSION,
)
from website.features.rag_pipeline.types import QueryClass


def test_router_version_bumped_to_v4():
    assert ROUTER_VERSION == "v4"


def test_anything_about_x_routes_to_vague():
    cls, reason = apply_class_overrides(
        "Anything about commencement?", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.VAGUE
    assert "vague_discovery" in reason


def test_something_on_x_routes_to_vague():
    cls, _ = apply_class_overrides(
        "Got something on personal wikis?", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.VAGUE


def test_anything_NOT_about_x_does_not_match_negation_guard():
    """Negation guard prevents the regex firing on negated discovery shapes."""
    cls, _ = apply_class_overrides(
        "Anything NOT about climate", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP


def test_anything_isnt_about_x_does_not_match_negation():
    cls, _ = apply_class_overrides(
        "Anything isn't about productivity",
        QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP


def test_long_anything_about_x_falls_through_to_multi_hop():
    """Length guard: >=25 words -> fall through to LLM/word-count rule."""
    long_query = (
        "Anything about how Steve Jobs framed mortality across his speeches "
        "and interviews and writings and the rest of his life and career and "
        "personal philosophy and family relationships"
    )
    cls, _ = apply_class_overrides(long_query, QueryClass.LOOKUP, person_entities=None)
    assert cls != QueryClass.VAGUE


def test_anything_about_year_falls_through():
    """Proper-noun guard: year token preserves named-entity LOOKUP."""
    cls, _ = apply_class_overrides(
        "Anything about Stanford 2005", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP


def test_anything_about_capitalised_noun_falls_through():
    """Proper-noun guard: capitalised non-leading token preserves precision."""
    cls, _ = apply_class_overrides(
        "Anything about Patrick Winston?", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP


def test_real_lookup_unaffected():
    """Sanity: existing LOOKUP queries are not regex'd to VAGUE."""
    cls, _ = apply_class_overrides(
        "What did Naval say about happiness?", QueryClass.LOOKUP, person_entities=["Naval"],
    )
    assert cls == QueryClass.LOOKUP
