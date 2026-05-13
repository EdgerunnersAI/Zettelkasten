"""Extract importance-ranked source-grounded atomic facts, cached per URL and version."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from website.features.summarization_engine.core.cache import FsContentCache
from website.features.summarization_engine.evaluator.prompts import (
    ATOMIC_FACTS_PROMPT,
    PROMPT_VERSION,
)
from website.features.summarization_engine.summarization.common.json_utils import (
    parse_json_object,
)

logger = logging.getLogger(__name__)


def _strip_fences(text: str) -> str:
    """Strip leading/trailing ```json fences from an LLM response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_facts(text: str) -> list | dict:
    """Fence-tolerant parse for either a JSON array or an object."""
    cleaned = _strip_fences(text)
    if cleaned.startswith("{"):
        return parse_json_object(cleaned)
    return json.loads(cleaned)


async def extract_atomic_facts(
    *,
    client: Any,
    source_text: str,
    cache_root: Path,
    url: str,
    ingestor_version: str,
) -> list[dict]:
    cache = FsContentCache(root=cache_root, namespace="atomic_facts")
    key = (url, ingestor_version, PROMPT_VERSION)
    hit = cache.get(key)
    if hit and "facts" in hit and hit["facts"]:
        return hit["facts"]

    prompt = ATOMIC_FACTS_PROMPT.format(source_text=source_text[:30000])
    # ``role="atomic_facts"`` tags this call as an evaluator-side
    # (atomic-fact extraction) call in the telemetry split. Without the tag
    # it defaults to the tier ("flash") which the prod/eval classifier would
    # misattribute to the summarizer bucket.

    async def _call(force_json_mime: bool = False) -> Any:
        kwargs: dict[str, Any] = {"tier": "flash", "role": "atomic_facts"}
        if force_json_mime:
            # Best-effort: pass through to the underlying client if supported.
            kwargs["response_mime_type"] = "application/json"
        try:
            return await client.generate(prompt, **kwargs)
        except TypeError:
            # Client does not accept response_mime_type; retry without it.
            return await client.generate(prompt, tier="flash", role="atomic_facts")

    result = await _call(force_json_mime=False)

    try:
        raw = _parse_facts(result.text)
    except Exception as err:
        head = (result.text or "")[:200]
        logger.warning(
            "atomic_facts.parse_failed url=%s err=%s head=%r", url, err, head
        )
        # One retry with structured-JSON hint.
        try:
            result = await _call(force_json_mime=True)
            raw = _parse_facts(result.text)
        except Exception as err2:
            head2 = (result.text or "")[:200]
            logger.warning(
                "atomic_facts.parse_failed url=%s err=%s head=%r (retry)",
                url,
                err2,
                head2,
            )
            raise

    if isinstance(raw, dict) and "facts" in raw:
        facts = raw["facts"]
    elif isinstance(raw, list):
        facts = raw
    else:
        facts = []

    facts = facts[:30]

    expected_min = max(3, len(source_text) // 2000)
    if len(facts) < expected_min:
        logger.warning(
            "atomic_facts.underpopulated url=%s got=%d expected>=%d",
            url,
            len(facts),
            expected_min,
        )

    # Do NOT cache empty results — they're almost certainly parse/upstream failures.
    if facts:
        cache.put(key, {"facts": facts})
    return facts
