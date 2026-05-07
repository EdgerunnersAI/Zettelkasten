"""iter-12 Task 28 R5: entity canonicalizer tests."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_canonicalize_returns_aliases():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node
    pool = AsyncMock()
    pool.generate_structured = AsyncMock(return_value={
        "canonical": "Steve Jobs",
        # "Jobs" is a substring of the title so it is filtered out correctly;
        # "Steven Paul Jobs" is NOT a substring so it survives.
        "aliases": ["Jobs", "Steven Jobs", "Steven Paul Jobs"],
    })
    result = await canonicalize_node(title="Steve Jobs Stanford 2005", summary="commencement", key_pool=pool)
    assert result["canonical"] == "Steve Jobs"
    # "Jobs" and "Steven Jobs" are substrings of the title — filtered out.
    assert "Jobs" not in result["aliases"]
    assert "Steven Paul Jobs" in result["aliases"]


@pytest.mark.asyncio
async def test_canonicalize_drops_substrings_of_title():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node
    pool = AsyncMock()
    pool.generate_structured = AsyncMock(return_value={
        "canonical": "x",
        "aliases": ["AI agents", "agents"],  # both substrings of title
    })
    result = await canonicalize_node(title="AI agents in 2026", summary="...", key_pool=pool)
    assert "AI agents" not in result["aliases"]
    assert "agents" not in result["aliases"]


@pytest.mark.asyncio
async def test_canonicalize_returns_empty_on_failure():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node
    pool = AsyncMock()
    pool.generate_structured = AsyncMock(side_effect=RuntimeError("LLM down"))
    result = await canonicalize_node(title="X", summary="...", key_pool=pool)
    assert result == {"canonical": "X", "aliases": []}


@pytest.mark.asyncio
async def test_canonicalize_caps_at_8_aliases():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node
    pool = AsyncMock()
    pool.generate_structured = AsyncMock(return_value={
        "canonical": "Z",
        "aliases": [f"alias{i}" for i in range(20)],
    })
    result = await canonicalize_node(title="Z", summary="...", key_pool=pool)
    assert len(result["aliases"]) <= 8


@pytest.mark.asyncio
async def test_canonicalize_drops_punctuation_only():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node
    pool = AsyncMock()
    pool.generate_structured = AsyncMock(return_value={
        "canonical": "Z",
        "aliases": ["...", "!?", "Real Alias"],
    })
    result = await canonicalize_node(title="Z", summary="...", key_pool=pool)
    assert result["aliases"] == ["Real Alias"]


def test_summary_hash_deterministic():
    from website.features.rag_pipeline.ingest.entity_canonicalizer import summary_hash
    h1 = summary_hash("hello world")
    h2 = summary_hash("hello world")
    assert h1 == h2
    assert len(h1) == 16  # SHA-256 first 16 hex chars
