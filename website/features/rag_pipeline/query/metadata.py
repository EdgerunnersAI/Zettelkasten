"""Query metadata extraction (cheap C-pass + optional Gemini A-pass).

The C-pass is rule-based and runs synchronously inside an async wrapper. It
extracts time expressions (via dateparser), domains (via tldextract), source-
type hints, and known-author hints from a free-form user query. The A-pass
slot is reserved for a Gemini-backed entity extraction step (added in a
follow-up task); when no key pool is supplied or the C-pass already covers
the high-signal fields, the A-pass is skipped entirely.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import dateparser
from dateparser.search import search_dates
import tldextract
from cachetools import TTLCache

from website.features.rag_pipeline.types import QueryClass, SourceType

logger = logging.getLogger(__name__)

_QUERY_ENTITY_PROMPT = """\
Extract named entities, authors, channels mentioned in the user query.

Return strict JSON: {
  "entities": [{"text": str, "confidence": float}],
  "authors":  [{"text": str, "confidence": float}],
  "channels": [{"text": str, "confidence": float}]
}

- entities: max 5; each with confidence in [0,1] reflecting likelihood
  this is a *grounded named concept* (proper noun, multi-token tech name,
  organization, person). NOT generic words.
  - "Steve Jobs", "Stanford 2005" -> 0.9+
  - "verbal punctuation" (descriptor not a named concept) -> 0.4
  - "burst probe", "command-line tool", "programming language" -> < 0.5
  - Single-token capitalized -> 0.7+
- authors / channels: same shape; confidence reflects "this is the named
  author/channel of a referenced work".

Query: {query}
"""

_A_PASS_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["text", "confidence"],
        }, "maxItems": 5},
        "authors": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["text", "confidence"],
        }, "maxItems": 3},
        "channels": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["text", "confidence"],
        }, "maxItems": 3},
    },
}

# iter-12 R6: confidence floor + cap-N for entity extraction.
# Env-tunable so rollback is a single redeploy with no code change.
_CONFIDENCE_FLOOR = float(os.environ.get("RAG_ENTITY_CONFIDENCE_FLOOR", "0.7"))
_ENTITY_TOP_N = int(os.environ.get("RAG_ENTITY_TOP_N", "3"))


def _coerce_confidence(raw) -> float:
    """Coerce confidence to float in [0, 1]; default 0.5 on failure."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning("metadata: malformed confidence %r — defaulting to 0.5", raw)
        return 0.5
    if val < 0.0 or val > 1.0:
        # clamp; log once at debug level (over-range is a model quirk, not an error)
        val = max(0.0, min(1.0, val))
    return val


def _filter_and_cap(
    items: list[dict],
    blocklist=None,
    sandbox_id: str | None = None,
) -> list[str]:
    """iter-12 R6: confidence floor + sort + cap-N + optional blocklist gate.

    Args:
        items: list of {"text": str, "confidence": float|str} dicts from LLM.
        blocklist: optional synchronous EntityBlocklist (for in-process checks);
            the async variant is handled separately in entity_anchor.py.
        sandbox_id: required when blocklist is provided.

    Returns:
        Ordered list of entity text strings (at most _ENTITY_TOP_N).
    """
    if not items:
        return []

    # Normalise: coerce confidence, skip non-dicts and entries without text
    normalised = []
    for e in items:
        if not isinstance(e, dict):
            continue
        text = e.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        conf = _coerce_confidence(e.get("confidence", 0.5))
        normalised.append({"text": text, "confidence": conf})

    # 1. Drop below floor
    kept = [e for e in normalised if e["confidence"] >= _CONFIDENCE_FLOOR]

    # 2. Sort DESC by confidence; tie-break: longer text first, then istitle()
    kept.sort(key=lambda e: (
        -e["confidence"],
        -len(e["text"]),
        not e["text"].istitle(),
    ))

    # 3. Cap-N
    kept = kept[:_ENTITY_TOP_N]

    # 4. Optional synchronous blocklist filter (async path handled in entity_anchor)
    if blocklist is not None and sandbox_id is not None:
        kept = [e for e in kept if not blocklist.is_blocked_sync(sandbox_id, e["text"])]

    # 5. Fallback: if all dropped, keep top-1 by confidence from original normalised list
    if not kept and normalised:
        top = max(normalised, key=lambda e: e["confidence"])
        kept = [top]

    return [e["text"] for e in kept]

# Static keyword map for source-type hints
_SOURCE_KEYWORDS = {
    SourceType.YOUTUBE: ("youtube", "yt", "video", "talk", "lecture", "podcast"),
    SourceType.REDDIT: ("reddit", "subreddit", "r/", "thread", "comment"),
    SourceType.GITHUB: ("github", "repo", "repository", "pull request", "issue"),
    SourceType.SUBSTACK: ("substack", "newsletter"),
    SourceType.WEB: ("article", "blog", "post"),
}

# Top-author seed list (extend from existing graph as discovered)
_KNOWN_AUTHORS = ("karpathy", "lecun", "hinton", "bengio", "ng", "vaswani")


@dataclass
class QueryMetadata:
    start_date: datetime | None = None
    end_date: datetime | None = None
    authors: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    preferred_sources: list[SourceType] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.0  # raised when A-pass confirms


class QueryMetadataExtractor:
    def __init__(self, *, key_pool, cache: TTLCache | None = None):
        self._key_pool = key_pool
        self._cache = cache if cache is not None else TTLCache(maxsize=1024, ttl=3600)

    async def extract(self, text: str, *, query_class: QueryClass) -> QueryMetadata:
        key = self._normalize(text)
        if key in self._cache:
            return self._cache[key]
        meta = self._c_pass(text)
        if self._key_pool and self._needs_a_pass(meta):
            meta = await self._a_pass(text, meta)
        self._cache[key] = meta
        return meta

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()

    def _c_pass(self, text: str) -> QueryMetadata:
        meta = QueryMetadata()
        # Time expressions: try whole-text parse first (cheap, handles bare
        # phrases like "yesterday"), then fall back to substring search on a
        # possessive-stripped copy so phrases embedded in a longer query still
        # match (e.g., "Last year's youtube talk ..." -> "Last year").
        parsed = dateparser.parse(text, settings={"RETURN_AS_TIMEZONE_AWARE": True})
        if not parsed:
            cleaned = re.sub(r"'s\b", "", text)
            hits = search_dates(
                cleaned, settings={"RETURN_AS_TIMEZONE_AWARE": True}
            )
            if hits:
                parsed = hits[0][1]
        if parsed:
            meta.start_date = parsed
            meta.end_date = parsed
        # Domains
        for token in re.findall(r"\b[\w\-]+\.[a-z]{2,}\b", text.lower()):
            ext = tldextract.extract(token)
            if ext.domain and ext.suffix:
                meta.domains.append(f"{ext.domain}.{ext.suffix}")
        # Source-type keywords
        text_lower = text.lower()
        for src, keywords in _SOURCE_KEYWORDS.items():
            if any(k in text_lower for k in keywords):
                meta.preferred_sources.append(src)
        # Known authors (cheap)
        for author in _KNOWN_AUTHORS:
            if author in text_lower:
                meta.authors.append(author)
        return meta

    def _needs_a_pass(self, meta: QueryMetadata) -> bool:
        # Skip A-pass if C-pass already filled author AND domain AND date
        return not (meta.authors and meta.domains and meta.start_date)

    async def _a_pass(self, text: str, meta: QueryMetadata) -> QueryMetadata:
        """Gemini-backed entity enrichment.

        Uses ``key_pool.generate_structured`` (the structured-JSON helper on the
        shared GeminiKeyPool). The base ``GeminiKeyPool`` in
        ``website/features/api_key_switching/key_pool.py`` exposes
        ``generate_content`` for free-form output and ``embed_content`` for
        embeddings; ``generate_structured`` is the structured-output variant
        used here for strict-schema entity extraction. If the method is missing
        or any error occurs (network, schema-violation, JSON parse, quota),
        we swallow the exception and return the C-pass meta unchanged so the
        request never fails on best-effort enrichment.

        iter-12 R6: response now returns objects {text, confidence}. Uses
        _filter_and_cap to apply confidence floor + cap-3 before merging.
        On schema rejection (Gemini API depth limit), falls back to legacy
        flat-string parsing with WARN log.
        """
        try:
            response = await self._key_pool.generate_structured(
                prompt=_QUERY_ENTITY_PROMPT.replace("{query}", text),
                response_schema=_A_PASS_SCHEMA,
                model_preference="flash-lite",
            )
            if isinstance(response, str):
                response = json.loads(response)
            if not isinstance(response, dict):
                return meta

            raw_entities = response.get("entities", []) or []
            raw_authors = response.get("authors", []) or []
            raw_channels = response.get("channels", []) or []

            # Detect legacy flat-string schema (schema rejection fallback)
            if raw_entities and isinstance(raw_entities[0], str):
                logger.warning(
                    "metadata A-pass: Gemini returned flat strings (schema rejected?) "
                    "— using legacy path, no confidence filter this query"
                )
                filtered_entities = [e for e in raw_entities if isinstance(e, str) and e]
                filtered_authors = [a for a in raw_authors if isinstance(a, str) and a]
                filtered_channels = [c for c in raw_channels if isinstance(c, str) and c]
            else:
                # iter-12 R6: apply confidence floor + cap-3
                filtered_entities = _filter_and_cap(raw_entities)
                # Authors and channels: use same filter but keep all (cap-3 already in schema)
                filtered_authors = _filter_and_cap(raw_authors)
                filtered_channels = _filter_and_cap(raw_channels)

            # Merge entities (dedup case-insensitive while preserving casing)
            existing_entities_lower = {e.lower() for e in meta.entities}
            for ent in filtered_entities:
                if ent and ent.lower() not in existing_entities_lower:
                    meta.entities.append(ent)
                    existing_entities_lower.add(ent.lower())
            # Merge authors (dedup case-insensitive)
            existing_authors_lower = {a.lower() for a in meta.authors}
            for author in filtered_authors:
                if author and author.lower() not in existing_authors_lower:
                    meta.authors.append(author)
                    existing_authors_lower.add(author.lower())
            # Merge channels
            existing_channels_lower = {c.lower() for c in meta.channels}
            for ch in filtered_channels:
                if ch and ch.lower() not in existing_channels_lower:
                    meta.channels.append(ch)
                    existing_channels_lower.add(ch.lower())
            meta.confidence = 1.0
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment
            logger.warning("Query metadata A-pass failed; degrading to C-pass result: %s", exc)
        return meta
