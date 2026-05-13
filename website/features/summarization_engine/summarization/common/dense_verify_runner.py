"""Shared runner for the DenseVerify phase.

Each per-source summarizer needs the same DV lifecycle: compute-or-cache the
``DenseVerifyResult``, feed ``missing_facts`` into the structured extractor,
then conditionally emit a flash patch when the structured payload still omits
a DV-flagged fact. This module centralizes that logic so every summarizer
stays at the 3-call budget without re-implementing it.

This module never calls CoD / SelfCheck / Patch — those modules are being
retired in favor of DenseVerify. The optional post-structured "patch" here
is a single flash call that rewrites one field (brief + any missing bullets)
with the unincluded facts appended; it is NOT the old Pro-tier SummaryPatcher.
"""
from __future__ import annotations

import logging

from website.features.summarization_engine.core.budget import get_budget
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import IngestResult
from website.features.summarization_engine.summarization.common.dense_cache import (
    LRUCache,
    cache_key_for_url,
)
from website.features.summarization_engine.summarization.common.dense_verify import (
    DenseVerifier,
    DenseVerifyResult,
)
from website.features.summarization_engine.summarization.common.prompts import (
    SYSTEM_PROMPT,
)

_log = logging.getLogger(__name__)

# Process-wide LRU for DV payloads. Bounded + TTL'd inside LRUCache so this
# never grows without bound in long-lived web processes.
_DV_CACHE: LRUCache[DenseVerifyResult] = LRUCache()


async def run_dense_verify(
    *,
    client: TieredGeminiClient,
    ingest: IngestResult,
    precomputed_dense: str | None = None,
    cache: LRUCache[DenseVerifyResult] | None = None,
) -> DenseVerifyResult:
    """Run a DenseVerify call for ``ingest``, consulting the per-URL cache.

    When ``precomputed_dense`` is provided (e.g. YouTube-no-transcript paths
    where the video-understanding call already produced a faithful dense
    text), we still issue DV so we get ``missing_facts`` + source-specific
    hint fields — the density half is free because the input is already
    dense. This keeps the downstream contract uniform across sources.
    """
    effective_cache = cache or _DV_CACHE
    key = cache_key_for_url(ingest.url or "")

    async def _compute() -> DenseVerifyResult:
        dv = DenseVerifier(client)
        content = precomputed_dense if precomputed_dense is not None else (
            ingest.raw_text or ""
        )
        return await dv.run(source_type=ingest.source_type, content=content)

    return await effective_cache.get_or_compute(key, _compute)


async def maybe_patch_structured_brief(
    *,
    client: TieredGeminiClient,
    current_brief: str,
    dv: DenseVerifyResult,
    extracted_payload_json: str | None,
    telemetry_sink: list | None = None,
) -> tuple[str, bool, int]:
    """Emit a single flash repair call ONLY when the structured payload omits
    DV-flagged facts.

    Returns ``(new_brief, patch_applied, flash_tokens_used)``. When DV had no
    missing_facts, or when the payload already covers them (substring-present
    in ``extracted_payload_json``), this is a no-op returning the original
    brief and zero tokens.

    The "omit detection" is a pragmatic substring scan: any DV-flagged fact
    whose first 48 visible chars do NOT appear verbatim in the serialized
    payload is considered omitted. This errs on the side of patching when the
    model paraphrased heavily, which is cheap (flash) and faithfulness-positive.
    """
    if not dv.missing_facts:
        return current_brief, False, 0
    haystack = (extracted_payload_json or "").lower()
    omitted = []
    for fact in dv.missing_facts:
        probe = (fact or "").strip().lower()[:48]
        if probe and probe not in haystack:
            omitted.append(fact)
    if not omitted:
        return current_brief, False, 0
    missing_block = "\n".join(f"- {f}" for f in omitted[:6])
    prompt = (
        "Revise BRIEF to also cover the missing FACTS. Keep it concise and "
        "factual, preserve the existing structure, and return plain prose only "
        "(no markdown, no bullet list).\n\n"
        f"BRIEF:\n{current_brief}\n\nFACTS:\n{missing_block}"
    )
    try:
        # C6: budget the optional patch call.
        get_budget().consume(role="patch")
        result = await client.generate(
            prompt,
            tier="flash",
            system_instruction=SYSTEM_PROMPT,
            role="patch",
        )
    except Exception as exc:  # noqa: BLE001 — fail-open on patch, never 500
        _log.info(
            "dense_verify_runner.patch_fail_open err_class=%s", exc.__class__.__name__
        )
        return current_brief, False, 0
    if telemetry_sink is not None:
        from website.features.summarization_engine.summarization.common.model_trace import (
            make_call_entry,
        )
        telemetry_sink.append(make_call_entry(role="patch", result=result))
    new_brief = (result.text or "").strip() or current_brief
    tokens = int(result.input_tokens) + int(result.output_tokens)
    return new_brief, True, tokens


__all__ = [
    "run_dense_verify",
    "maybe_patch_structured_brief",
]
