"""iter-12 Task 29 R6: confidence floor + cap-3 tests."""
import pytest


def test_filter_drops_low_confidence():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [
        {"text": "Steve Jobs", "confidence": 0.95},
        {"text": "burst probe", "confidence": 0.4},
        {"text": "command-line tool", "confidence": 0.3},
    ]
    out = _filter_and_cap(items)
    assert out == ["Steve Jobs"]


def test_filter_caps_at_top_n():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [{"text": f"e{i}", "confidence": 0.9} for i in range(10)]
    out = _filter_and_cap(items)
    assert len(out) == 3


def test_filter_sorts_by_confidence_desc():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [
        {"text": "a", "confidence": 0.75},
        {"text": "b", "confidence": 0.95},
        {"text": "c", "confidence": 0.85},
    ]
    out = _filter_and_cap(items)
    assert out == ["b", "c", "a"]


def test_filter_fallback_top1_when_all_below_floor():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [{"text": "a", "confidence": 0.3}, {"text": "b", "confidence": 0.5}]
    out = _filter_and_cap(items)
    assert out == ["b"]


def test_filter_empty():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    assert _filter_and_cap([]) == []


def test_filter_clamps_out_of_range_confidence():
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [{"text": "a", "confidence": 1.5}, {"text": "b", "confidence": -0.1}]
    out = _filter_and_cap(items)
    # 1.5 clamped to 1.0 (>= 0.7 floor); -0.1 clamped to 0.0 (< 0.7 floor)
    assert "a" in out


def test_filter_handles_string_confidence():
    """Tolerates `confidence: "0.9"` (LLM returning string)."""
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [{"text": "a", "confidence": "0.9"}]
    out = _filter_and_cap(items)
    assert "a" in out


def test_filter_skips_non_dict_items():
    """Non-dict items in the list are silently skipped."""
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = ["bare_string", None, {"text": "valid", "confidence": 0.8}]
    out = _filter_and_cap(items)
    assert out == ["valid"]


def test_filter_skips_empty_text():
    """Items with empty or whitespace-only text are skipped."""
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [
        {"text": "", "confidence": 0.9},
        {"text": "   ", "confidence": 0.9},
        {"text": "real entity", "confidence": 0.8},
    ]
    out = _filter_and_cap(items)
    assert out == ["real entity"]


def test_filter_malformed_confidence_defaults_to_0_5():
    """Malformed confidence string defaults to 0.5 (below 0.7 floor)."""
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [
        {"text": "bad", "confidence": "not_a_number"},
        {"text": "good", "confidence": 0.9},
    ]
    out = _filter_and_cap(items)
    # "bad" defaults to 0.5, below floor; "good" passes
    assert out == ["good"]


def test_filter_tie_break_longer_text_first():
    """Same confidence: longer text sorts first."""
    from website.features.rag_pipeline.query.metadata import _filter_and_cap
    items = [
        {"text": "ab", "confidence": 0.8},
        {"text": "abcde", "confidence": 0.8},
        {"text": "abc", "confidence": 0.8},
    ]
    out = _filter_and_cap(items)
    assert out[0] == "abcde"
