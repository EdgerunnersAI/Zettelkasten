"""iter-12 Task 28 R5: LLM-driven entity canonicalization for kg.kg_nodes.

Given a node title and summary, asks Gemini flash-lite to:
  - identify the primary named entity the node is about
  - produce a compact list of alternative name forms (aliases)

Aliases are written to ``kg.kg_node_aliases`` (Phase 1.D.4a — replaces the
v1 ``public.kg_nodes.aliases`` array dropped in Phase 6) and indexed for
fuzzy matching by ``rag_resolve_entity_anchors`` so entity-anchor recall
improves without hand-coded synonym lists.

Guard rails enforced here:
  - aliases capped at 8 (avoid polluting the index)
  - aliases that are pure substrings of the title are dropped (redundant — the
    existing ILIKE '%entity%' path on ``kg.kg_nodes.canonical_name`` already
    covers these)
  - aliases that consist only of punctuation / whitespace are dropped
  - on any LLM failure: returns {"canonical": title, "aliases": []} so the
    caller always gets a safe dict it can persist into ``kg.kg_nodes`` +
    ``kg.kg_node_aliases``
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

_MAX_ALIASES = 8

_PROMPT = """\
You are a knowledge-graph entity canonicalization assistant.

Given the TITLE and SUMMARY of a note, identify:
1. The single primary named entity the note is ABOUT (person, project, concept,
   organisation, paper, tool, etc.).  Use the most complete / unambiguous form.
2. Up to {max_aliases} alternative name forms for that entity that a user might
   type when searching — abbreviations, nicknames, former names, initials, etc.
   Do NOT include forms that are already substrings of the title (those are
   covered by the existing title-search index).

TITLE: {title}
SUMMARY: {summary}

Return JSON only — no prose.
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "canonical": {"type": "string"},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["canonical", "aliases"],
}


def summary_hash(summary: str) -> str:
    """Return the first 16 hex characters of the SHA-256 hash of *summary*.

    Used as a cheap change-detector: if the stored hash on ``kg.kg_nodes``
    matches the incoming summary's hash, canonicalize_node can be skipped
    entirely.
    """
    return hashlib.sha256(summary.encode("utf-8", errors="replace")).hexdigest()[:16]


async def canonicalize_node(
    *,
    title: str,
    summary: str,
    key_pool: Any,
) -> dict[str, Any]:
    """Ask Gemini to produce a canonical name + alias list for a node.

    Parameters
    ----------
    title:
        The node's display name (``kg.kg_nodes.canonical_name``).
    summary:
        The node's stored summary text used to give context to the LLM.
    key_pool:
        A ``GeminiKeyPool`` instance (or any duck-typed object with an async
        ``generate_structured(*, prompt, response_schema, model_preference)``
        method).

    Returns
    -------
    dict with keys:
        ``canonical`` — str, best entity name
        ``aliases``   — list[str], filtered + capped alternative forms

    Never raises — returns ``{"canonical": title, "aliases": []}`` on any
    failure so callers can safely persist the result into ``kg.kg_nodes`` +
    ``kg.kg_node_aliases``.
    """
    prompt = _PROMPT.format(
        title=title,
        summary=summary[:2000],  # cap to avoid inflating token cost on long summaries
        max_aliases=_MAX_ALIASES,
    )
    try:
        raw = await key_pool.generate_structured(
            prompt=prompt,
            response_schema=_SCHEMA,
            model_preference="flash-lite",
            label="entity_canonicalizer",
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("canonicalize_node LLM failure title=%r exc=%s", title, exc)
        return {"canonical": title, "aliases": []}

    if not isinstance(raw, dict):
        _log.warning("canonicalize_node unexpected response type=%s title=%r", type(raw).__name__, title)
        return {"canonical": title, "aliases": []}

    canonical = str(raw.get("canonical") or title).strip() or title
    raw_aliases: list[Any] = raw.get("aliases") or []

    filtered = _filter_aliases(raw_aliases, title=title)
    return {"canonical": canonical, "aliases": filtered}


# ── Internal helpers ──────────────────────────────────────────────────────────

_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


def _filter_aliases(raw: list[Any], *, title: str) -> list[str]:
    """Validate, deduplicate, and cap the LLM alias list.

    Rules (applied in order):
    1. Cast to str, strip whitespace.
    2. Drop empty strings and punctuation-only strings.
    3. Drop aliases that are case-insensitive substrings of *title* (the
       existing ILIKE path already covers these; keeping them wastes index space
       and inflates alias arrays with low-value entries).
    4. Deduplicate (preserve first-seen order).
    5. Cap at _MAX_ALIASES.
    """
    title_lower = title.lower()
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        alias = str(item).strip()
        if not alias:
            continue
        if _PUNCT_ONLY.match(alias):
            continue
        if alias.lower() in title_lower:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(alias)
        if len(result) >= _MAX_ALIASES:
            break
    return result
