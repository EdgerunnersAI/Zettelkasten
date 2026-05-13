from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from website.features.summarization_engine.evaluator.atomic_facts import (
    extract_atomic_facts,
)
from website.features.summarization_engine.evaluator.prompts import PROMPT_VERSION


@pytest.mark.asyncio
async def test_extract_atomic_facts_returns_list(tmp_path: Path):
    client = MagicMock()
    fake_result = MagicMock(
        text='[{"claim": "X is Y", "importance": 5}]',
        input_tokens=10,
        output_tokens=5,
    )
    client.generate = AsyncMock(return_value=fake_result)

    facts = await extract_atomic_facts(
        client=client,
        source_text="...",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )

    assert facts == [{"claim": "X is Y", "importance": 5}]


@pytest.mark.asyncio
async def test_extract_atomic_facts_cache_hit(tmp_path: Path):
    client = MagicMock()
    client.generate = AsyncMock()

    from website.features.summarization_engine.core.cache import FsContentCache

    cache = FsContentCache(root=tmp_path, namespace="atomic_facts")

    cache.put(
        ("https://a.com", "1.0.0", PROMPT_VERSION),
        {"facts": [{"claim": "cached", "importance": 3}]},
    )

    facts = await extract_atomic_facts(
        client=client,
        source_text="...",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )

    assert facts == [{"claim": "cached", "importance": 3}]
    client.generate.assert_not_called()


@pytest.mark.asyncio
async def test_fenced_array_response_is_parsed(tmp_path: Path):
    client = MagicMock()
    fake_result = MagicMock(
        text='```json\n[{"claim": "fenced array", "importance": 4}]\n```',
    )
    client.generate = AsyncMock(return_value=fake_result)

    facts = await extract_atomic_facts(
        client=client,
        source_text="src",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )

    assert facts == [{"claim": "fenced array", "importance": 4}]


@pytest.mark.asyncio
async def test_fenced_object_with_facts_is_parsed(tmp_path: Path):
    client = MagicMock()
    fake_result = MagicMock(
        text='```json\n{"facts": [{"claim": "fenced obj", "importance": 2}]}\n```',
    )
    client.generate = AsyncMock(return_value=fake_result)

    facts = await extract_atomic_facts(
        client=client,
        source_text="src",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )

    assert facts == [{"claim": "fenced obj", "importance": 2}]


@pytest.mark.asyncio
async def test_malformed_json_raises_not_silent_empty(tmp_path: Path):
    client = MagicMock()
    fake_result = MagicMock(text="this is not json at all <<<>>>")
    client.generate = AsyncMock(return_value=fake_result)

    with pytest.raises(Exception):
        await extract_atomic_facts(
            client=client,
            source_text="src",
            cache_root=tmp_path,
            url="https://a.com",
            ingestor_version="1.0.0",
        )


@pytest.mark.asyncio
async def test_empty_result_is_not_cached(tmp_path: Path):
    client = MagicMock()
    # Both calls return an empty array.
    empty_result = MagicMock(text="[]")
    good_result = MagicMock(text='[{"claim": "ok", "importance": 1}]')
    client.generate = AsyncMock(side_effect=[empty_result, good_result])

    facts1 = await extract_atomic_facts(
        client=client,
        source_text="src",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )
    assert facts1 == []

    # Second call must hit the LLM again because the empty result wasn't cached.
    facts2 = await extract_atomic_facts(
        client=client,
        source_text="src",
        cache_root=tmp_path,
        url="https://a.com",
        ingestor_version="1.0.0",
    )
    assert facts2 == [{"claim": "ok", "importance": 1}]
    assert client.generate.await_count == 2


def test_prompt_version_is_v7():
    # Bumped from v6 -> v7 in CF-3 (R3): verbatim-verify-before-flagging clause
    # for invented_number / contradicted_sentence — paired with the deterministic
    # post-judge FP filter in ops.scripts.lib.phases.filter_judge_false_positives.
    assert PROMPT_VERSION == "evaluator.v7"
