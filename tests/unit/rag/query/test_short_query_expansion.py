"""iter-12 Class D-out: gazetteer removed from THEMATIC branch.

iter-11 added gazetteer + HyDE to short-THEMATIC queries. iter-12 removes it:
short-THEMATIC now routes to VAGUE via Q7 regex (router.py) where the
gazetteer fires naturally. The THEMATIC branch uses only multi-query paraphrases.
VAGUE branch keeps expand_vague; iter-13 K6 replaces it with LLM.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from website.features.rag_pipeline.query.transformer import QueryTransformer
from website.features.rag_pipeline.types import QueryClass


def _stub_pool(text: str):
    pool = AsyncMock()

    async def _gen(*args, **kwargs):
        return text

    pool.generate_content = _gen
    return pool


@pytest.mark.asyncio
async def test_short_thematic_no_longer_invokes_gazetteer():
    """iter-12 Class D-out: short-THEMATIC routes to VAGUE via router; the
    THEMATIC branch itself NO longer calls expand_vague. A short query like
    'Anything about commencement?' must NOT include gazetteer expansions
    (graduation/stanford/valedictory) when processed as THEMATIC class."""
    pool = _stub_pool("alt: paraphrase 1\nalt: paraphrase 2\nalt: paraphrase 3")
    qt = QueryTransformer(pool=pool)
    variants = await qt.transform("Anything about commencement?", QueryClass.THEMATIC)
    joined = " ".join(variants).lower()
    # Gazetteer keys MUST NOT appear in THEMATIC variants (Class D-out removed the call).
    assert "graduation" not in joined
    assert "stanford" not in joined
    assert "valedictory" not in joined


@pytest.mark.asyncio
async def test_long_thematic_query_multiquery_only():
    """All THEMATIC queries use only iter-08 multi-query: no gazetteer, no HyDE.
    Only the original + paraphrases remain (iter-12 Class D-out removed the
    conditional gazetteer path)."""
    pool = _stub_pool("alt: paraphrase 1\nalt: paraphrase 2\nalt: paraphrase 3")
    qt = QueryTransformer(pool=pool)
    long_q = (
        "How does the programming workflow zettel characterise the day-to-day "
        "skill of programming?"
    )
    variants = await qt.transform(long_q, QueryClass.THEMATIC)
    # Must include the original query + at least one paraphrase.
    assert variants[0] == long_q
    assert len(variants) >= 2


@pytest.mark.asyncio
async def test_lookup_class_unaffected():
    """LOOKUP keeps the iter-08 single-variant path; no expansion regardless
    of length."""
    pool = _stub_pool("ignored")
    qt = QueryTransformer(pool=pool)
    variants = await qt.transform("naval ravikant", QueryClass.LOOKUP)
    assert variants == ["naval ravikant"]
