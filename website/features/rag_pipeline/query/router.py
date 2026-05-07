"""Query classification into retrieval strategies."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import cachetools

from website.features.rag_pipeline.adapters.pool_factory import get_generation_pool
from website.features.rag_pipeline.types import QueryClass

_ROUTER_PROMPT = """\
Classify the user's query into one of: lookup, vague, thematic, multi_hop, step_back.

Definitions:
- lookup: precise question about a specific entity, fact, or concept named in the query.
- vague: under-specified discovery query ('anything about X', 'what's there on Y').
- thematic: cross-corpus synthesis on a topic ('the implicit theory of', 'how do these compare').
- multi_hop: chains 2+ sub-questions or requires reasoning across documents.
- step_back: meta-question that requires generalising before retrieving.

Examples:
"What does Steve Jobs mean by 'connecting the dots'?" => lookup
"Where does the Pragmatic Engineer post live?" => lookup
"Anything about commencement?" => vague
"Got something on personal wikis?" => vague
"What's the implicit theory of focus across these zettels?" => thematic
"How do the productivity zettels portray deep work?" => thematic
"Compare Walker on sleep loss with the programming-workflow zettel on debugging." => multi_hop
"Steve Jobs and Naval Ravikant both speak about meaningful work — compare." => multi_hop
"What general principle do these capture about creative work?" => step_back
"Why might these knowledge-management ideas converge across authors?" => step_back

Return strict JSON with a single key named class.

Query: {query}
Class:"""


# iter-09 RES-6: cache-key invalidator. Bump ON ANY rule change so prior
# router answers don't leak across iterations.
ROUTER_VERSION = "v4"  # iter-12: invalidate v3 cache; few-shot + regex override

# Process-level shared cache (TTLCache, 24h, 10k entries). Each query is
# normalised to lowercase + stripped before hashing; the key includes
# ROUTER_VERSION + kasten_id so multi-tenant deployments don't bleed
# classifications across Kastens.
_ROUTER_CACHE: cachetools.TTLCache[str, QueryClass] = cachetools.TTLCache(
    maxsize=10_000, ttl=86_400
)


# iter-04 vote-table heuristics. Applied as a post-LLM correction layer in
# ``apply_class_overrides`` after metadata extraction completes. The router
# LLM (flash-lite) drifted on q5 (designed thematic, classified multi_hop)
# and that drift propagated into wrong fusion weights and graph self-seeding.
# These deterministic patterns catch the high-precision misroute cases.

# "compare X and Y" / "X vs Y" / "difference between" => STEP_BACK (broad framing)
_COMPARE_PATTERN = re.compile(
    r"\b(compare|comparison|contrast|vs\.?|versus|difference between|how do .+ differ)\b",
    re.IGNORECASE,
)

# "list/all/every X" => THEMATIC (broad enumeration, paraphrase fan-out)
_ENUMERATE_PATTERN = re.compile(
    r"\b(list (all|every|the)|all (of )?the|every|enumerate|what (kinds|types) of)\b",
    re.IGNORECASE,
)

# "across these zettels / cite at least N / implicit theory / common theme" =>
# THEMATIC. These are synthesis-across-corpus markers — q5 fix. The router LLM
# tends to misclassify them as multi_hop because of the question complexity,
# but the right behaviour is paraphrase fan-out (thematic), not sub-question
# decomposition.
_SYNTHESIS_PATTERN = re.compile(
    r"\b("
    r"across (these|the|all) (zettels|notes|kasten|sources|materials|items)"
    r"|cite at least"
    r"|common (theme|thread|pattern|idea)"
    r"|implicit (theory|view|stance|assumption|framework)"
    r"|what do (these|the) (zettels|notes|sources|items) say"
    r"|recurring (theme|idea|pattern)"
    r")\b",
    re.IGNORECASE,
)

# iter-09 RES-6 new rules. Inserted BEFORE rule 5 (word-count) so a 22-word
# "summary of …" lookup goes THEMATIC by intent rather than getting
# upgraded to MULTI_HOP by length.
_RELATE_PATTERN = re.compile(r"\bhow does .+ relate to .+", re.IGNORECASE)
_SUMMARY_OF_PATTERN = re.compile(
    r"\b(summary|summarize|key ideas) of\b", re.IGNORECASE
)

# iter-12 Q7: vague-discovery regex + 3 guards (negation / length / proper-noun).
# Word-boundary anchored (not line-start) so "Got something on X" also fires;
# bare "\babout\b" is safe because 'about' must be preceded by a discovery word.
_VAGUE_DISCOVERY_PATTERN = re.compile(
    r"\b(anything|something|stuff|things|info|notes?)"
    r"\s+(about|on|regarding|re|around|related to)\b",
    re.IGNORECASE,
)
_NEGATION_PATTERN = re.compile(
    r"\b(not|isn'?t|except|excluding|without)\b", re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")


def _matches_vague_discovery(query: str) -> bool:
    """iter-12 Q7: 3-guarded regex for 'Anything about X?' shape."""
    if not _VAGUE_DISCOVERY_PATTERN.search(query):
        return False
    if _NEGATION_PATTERN.search(query):
        return False  # negation guard
    if len(query.split()) >= 25:
        return False  # length guard preserves long-query MULTI_HOP
    # Proper-noun guard: year or any capitalised non-leading word → named-entity LOOKUP.
    if _YEAR_PATTERN.search(query):
        return False
    words = query.split()
    if any(re.sub(r"[^A-Za-z]", "", w)[:1].isupper() for w in words[1:]):
        return False
    return True


def _cache_enabled() -> bool:
    return os.environ.get("ROUTER_CACHE_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


def _cache_key(version: str, kasten_id: str | None, query: str) -> str:
    raw = f"{version}|{kasten_id or ''}|{query.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class QueryRouter:
    """Classify queries into one of the five retrieval classes."""

    def __init__(self, pool: Any | None = None, kasten_id: str | None = None):
        self._pool = pool or get_generation_pool()
        self._kasten_id = kasten_id

    async def classify(self, query: str) -> QueryClass:
        # iter-09 RES-6: read ROUTER_VERSION at call-time so monkeypatched
        # bumps in tests invalidate the cache as expected. Re-importing
        # avoids stale module-level capture of the constant.
        from website.features.rag_pipeline.query import router as _router_mod
        version = getattr(_router_mod, "ROUTER_VERSION", ROUTER_VERSION)
        cache_active = _cache_enabled()
        key = _cache_key(version, self._kasten_id, query) if cache_active else None
        if cache_active and key in _ROUTER_CACHE:
            return _ROUTER_CACHE[key]

        prompt = _ROUTER_PROMPT.format(query=query)
        try:
            response = await self._pool.generate_content(
                prompt,
                config={
                    "temperature": 0.0,
                    "max_output_tokens": 50,
                    "response_mime_type": "application/json",
                },
                starting_model="gemini-2.5-flash-lite",
                label="RAG QueryRouter",
            )
            parsed = json.loads(_coerce_text(response))
            cls = _parse_query_class(parsed.get("class", "lookup"))
        except Exception:
            cls = QueryClass.LOOKUP

        if cache_active and key is not None:
            _ROUTER_CACHE[key] = cls
        return cls


def apply_class_overrides(
    query: str,
    llm_class: QueryClass,
    *,
    person_entities: list[str] | None = None,
) -> tuple[QueryClass, str | None]:
    """iter-04 vote-table override layer (with iter-09 narrowing).

    Returns ``(final_class, reason)``. ``reason`` is ``None`` when no override
    fires (the LLM's class is preserved). When an override fires, ``reason``
    is a short tag suitable for tracing / observability.

    Override rules, applied in priority order:

    1. ≥2 distinct person-entities → ``LOOKUP``.
    2. Synthesis-across-corpus pattern → ``THEMATIC``.
    3. "compare/vs/difference" pattern → ``STEP_BACK``.
    4. "list/all/every/enumerate" pattern → ``THEMATIC``.
    5a (iter-09). 2+ question marks → ``MULTI_HOP``.
    5b (iter-09). "how does X relate to Y" → ``MULTI_HOP``.
    5c (iter-09). "summary/summarize/key ideas of" → ``THEMATIC``.
    6. Word count ≥ 25 + LLM said ``LOOKUP`` AND no named author → upgrade
       to ``MULTI_HOP``. iter-09: threshold lifted from 18 to 25 so q13/q14
       short LOOKUP shapes stay LOOKUP.
    """
    persons = [p for p in (person_entities or []) if isinstance(p, str) and p.strip()]
    if len(persons) >= 2:
        return QueryClass.LOOKUP, "override_multi_person_entity"
    if _SYNTHESIS_PATTERN.search(query):
        return QueryClass.THEMATIC, "override_synthesis_pattern"
    if _COMPARE_PATTERN.search(query):
        return QueryClass.STEP_BACK, "override_compare_pattern"
    if _ENUMERATE_PATTERN.search(query):
        return QueryClass.THEMATIC, "override_enumerate_pattern"
    # iter-09 new rules
    if query.count("?") >= 2:
        return QueryClass.MULTI_HOP, "override_double_question"
    if _RELATE_PATTERN.search(query):
        return QueryClass.MULTI_HOP, "override_relate_pattern"
    if _SUMMARY_OF_PATTERN.search(query):
        return QueryClass.THEMATIC, "override_summary_of_pattern"
    # iter-12 Q7: vague-discovery override BEFORE word-count fallback.
    if _matches_vague_discovery(query):
        return (QueryClass.VAGUE, "override_vague_discovery_shape")
    word_count = len(query.split())
    if word_count >= 25 and llm_class is QueryClass.LOOKUP and not persons:
        return QueryClass.MULTI_HOP, "override_long_query_upgrade"
    return llm_class, None


def _parse_query_class(raw_value: str) -> QueryClass:
    normalized = str(raw_value or "lookup").strip().lower()
    for query_class in QueryClass:
        if query_class.value == normalized:
            return query_class
    return QueryClass.LOOKUP


def _coerce_text(response: Any) -> str:
    payload = response[0] if isinstance(response, tuple) else response
    if isinstance(payload, str):
        return payload
    text = getattr(payload, "text", None)
    if text is not None:
        return text
    return str(payload)
