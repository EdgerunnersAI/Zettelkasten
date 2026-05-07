"""iter-12 Task 30 / R3 Tier-1 — runtime citation drift guard.

Asserts that the synth's primary_citation is among the retrieved_node_ids.
A drift indicates citation hallucination — flag, don't fail.
"""
from __future__ import annotations

import logging
from typing import Iterable

_log = logging.getLogger("rag.citation_guard")


def check_cited_in_context(
    *, primary_citation: str | None, retrieved_node_ids: Iterable[str], qid: str | None = None,
) -> bool:
    """Return True if cited primary is in the retrieved set; False indicates drift.

    Logs WARN on drift; never raises. Caller can attach `_citation_drift: True`
    to the response metadata for downstream eval scoring.
    """
    if not primary_citation:
        return True
    retrieved = set(retrieved_node_ids or [])
    if primary_citation not in retrieved:
        _log.warning("citation_drift qid=%s primary=%s n_retrieved=%d",
                     qid, primary_citation, len(retrieved))
        return False
    return True
