"""Query expansion helpers for the retrieval pipeline."""

from __future__ import annotations

import os
from typing import Any, Iterable

from website.features.rag_pipeline.adapters.pool_factory import get_generation_pool
from website.features.rag_pipeline.query.vague_expander import expand_vague
from website.features.rag_pipeline.types import QueryClass


class QueryTransformer:
    """Generate retrieval variants based on the routed query class.

    iter-04: when ``entities`` is supplied (post-metadata extraction), the
    decomposition / multi-query prompts instruct the LLM to preserve every
    proper-noun entity verbatim in every variant, and a deterministic
    post-check appends the primary entity to any variant that lost it. This
    is the q10 fix: prevents proper-noun loss across thematic / multi-hop
    paraphrasing, which previously caused the 'Steve Jobs and Naval Ravikant'
    query to drop the Stanford zettel from retrieval.
    """

    def __init__(self, pool: Any | None = None):
        self._pool = pool

    async def transform(
        self,
        query: str,
        cls: QueryClass,
        *,
        entities: list[str] | None = None,
    ) -> list[str]:
        ents = _clean_entities(entities)
        if cls is QueryClass.LOOKUP:
            return [query]
        if cls is QueryClass.VAGUE:
            # iter-07 Fix D: gazetteer expansion (no new LLM call) for short
            # vague queries. Augments the HyDE variant with deterministic
            # synonym/morphology variants so BM25 can hit lexically distant
            # zettels (e.g. "commencement" -> stanford 2005). Fixes q7.
            gazetteer_variants = expand_vague(query)
            variants = [query, await self._hyde(query), *gazetteer_variants]
        elif cls is QueryClass.MULTI_HOP:
            variants = [query, *await self._decompose(query, n=3, entities=ents)]
        elif cls is QueryClass.THEMATIC:
            # iter-08 Phase 1: revert to n=3 (RES-3: n=5 added zero retrieval
            # delta on q5, accelerated quota burn). Knob retained for future
            # A/B once recall is fixed.
            _thematic_n = int(os.environ.get("RAG_THEMATIC_MULTIQUERY_N", "3"))
            base_variants = await self._multi_query(query, n=_thematic_n, entities=ents)
            # iter-12 Class D-out: short-THEMATIC routes to VAGUE via Q7 regex (router.py).
            # VAGUE branch still uses expand_vague; iter-13 K6 replaces with LLM.
            variants = [query, *base_variants]
        elif cls is QueryClass.STEP_BACK:
            variants = [query, await self._step_back(query)]
        else:
            return [query]
        anchored = _enforce_entity_anchoring(variants, ents)
        return _dedupe(anchored)

    async def _hyde(self, query: str) -> str:
        return await self._single_variant(
            "Write a short hypothetical answer passage that would likely contain the information needed to answer this query:\n"
            f"{query}"
        )

    async def _decompose(self, query: str, n: int, entities: list[str] | None = None) -> list[str]:
        if entities:
            ent_str = ", ".join(entities)
            prompt = (
                f"Break this question into {n} short sub-questions, one per line. "
                f"Every sub-question MUST mention at least one of these named entities verbatim: {ent_str}.\n"
                f"Query: {query}"
            )
        else:
            prompt = f"Break this question into {n} short sub-questions, one per line:\n{query}"
        return await self._multi_variant(prompt, n)

    async def _multi_query(self, query: str, n: int, entities: list[str] | None = None) -> list[str]:
        if entities:
            ent_str = ", ".join(entities)
            prompt = (
                f"Generate {n} alternative search reformulations for this question, one per line. "
                f"Every reformulation MUST keep these named entities verbatim: {ent_str}.\n"
                f"Query: {query}"
            )
        else:
            prompt = f"Generate {n} alternative search reformulations for this question, one per line:\n{query}"
        return await self._multi_variant(prompt, n)

    async def _step_back(self, query: str) -> str:
        return await self._single_variant(
            "Rewrite this question into a broader, more general framing that still preserves the user's intent:\n"
            f"{query}"
        )

    async def _single_variant(self, prompt: str) -> str:
        try:
            response = await self._get_pool().generate_content(
                prompt,
                config={"temperature": 0.2, "max_output_tokens": 200},
                starting_model="gemini-2.5-flash-lite",
                label="RAG QueryTransformer",
            )
            return _coerce_text(response).strip()
        except Exception:
            return ""

    async def _multi_variant(self, prompt: str, n: int) -> list[str]:
        try:
            response = await self._get_pool().generate_content(
                prompt,
                config={"temperature": 0.2, "max_output_tokens": 300},
                starting_model="gemini-2.5-flash-lite",
                label="RAG QueryTransformer",
            )
            lines = [line.strip(" -*\t") for line in _coerce_text(response).splitlines()]
            return [line for line in lines if line][:n]
        except Exception:
            return []

    def _get_pool(self) -> Any:
        if self._pool is None:
            self._pool = get_generation_pool()
        return self._pool


def _coerce_text(response: Any) -> str:
    payload = response[0] if isinstance(response, tuple) else response
    if isinstance(payload, str):
        return payload
    text = getattr(payload, "text", None)
    if text is not None:
        return text
    return str(payload)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped


def _clean_entities(entities: Iterable[str] | None) -> list[str]:
    if not entities:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ent in entities:
        if not isinstance(ent, str):
            continue
        cleaned = ent.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out[:4]


def _enforce_entity_anchoring(variants: list[str], entities: list[str]) -> list[str]:
    """Belt-and-suspenders: even when the LLM ignores the prompt instruction,
    deterministically append the primary entity to any variant that lost it.
    The first variant (verbatim user query) is left untouched.
    """
    if not entities or not variants:
        return variants
    primary = entities[0]
    out: list[str] = []
    entity_lower = [e.lower() for e in entities]
    for i, variant in enumerate(variants):
        v_lc = variant.lower()
        if i == 0 or any(e in v_lc for e in entity_lower):
            out.append(variant)
        else:
            out.append(f"{variant} (regarding {primary})")
    return out
