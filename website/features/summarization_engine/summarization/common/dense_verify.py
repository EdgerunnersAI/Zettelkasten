"""Consolidated dense-verify phase for the 3-call engine refactor.

Fuses the prior Chain-of-Density / self-check / patch triad into a single
Pro-tier Gemini call that returns:

  - ``dense_text``:   a maximally-faithful dense summary of the source,
  - ``missing_facts``: claims the model judges important but unable to ground
                       (drives a conditional patch fall-back in the caller),
  - ``stance``:        newsletter stance label (None for other sources),
  - ``archetype``:     GitHub archetype hint (None for other sources),
  - ``format_label``:  YouTube video format label (None for other sources),
  - ``core_argument``: one-sentence thesis for the generic Core Argument block,
  - ``closing_hook``:  one-sentence payoff / takeaway for Closing Remarks.

Per the Quality First guardrail, this module does NOT remove any faithfulness
signal — it requests the same evaluations the old pipeline ran (density +
missing-fact detection) in a single prompt, preserving every downstream
consumer's semantics while collapsing 3+ Pro-tier round-trips into one. The
``missing_facts`` output feeds a conditional repair call in the summarizer,
so the self-check "fail open" / patch path stays behaviorally equivalent.

A single transient-error retry is built in: transient upstream failures
(5xx / DeadlineExceeded / Timeout) get one replay; permanent 4xx errors raise.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from website.features.summarization_engine.core.budget import get_budget
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.summarization.common.json_utils import (
    parse_json_object,
)
from website.features.summarization_engine.summarization.common.prompts import (
    SYSTEM_PROMPT,
    source_context,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - google-api-core is a runtime dependency
    from google.api_core import exceptions as _gapi_exc
except Exception:  # pragma: no cover
    _gapi_exc = None  # type: ignore[assignment]


_RETRY_DELAY_SEC = 2.0


class DenseVerifyResult(BaseModel):
    """Structured output of a DenseVerifier call.

    ``missing_facts`` is non-empty only when the model judges the dense text
    omitted claims it considers important; an empty list signals the dense
    text already covers everything worth preserving.
    """

    dense_text: str = Field(..., description="Maximally-faithful dense summary of the source.")
    missing_facts: list[str] = Field(
        default_factory=list,
        description="Important source claims the dense text did NOT include.",
    )
    stance: str | None = Field(
        default=None,
        description="Newsletter stance label or None when source_type != newsletter.",
    )
    archetype: str | None = Field(
        default=None,
        description="GitHub archetype label or None when source_type != github.",
    )
    format_label: str | None = Field(
        default=None,
        description="YouTube format label or None when source_type != youtube.",
    )
    core_argument: str = Field(
        default="",
        description="One-sentence thesis for the Core Argument block.",
    )
    closing_hook: str = Field(
        default="",
        description="One-sentence payoff for the Closing Remarks block.",
    )
    # Per-call Gemini telemetry so the summarizer can surface
    # ``SummaryMetadata.model_used[..role=dense_verify]``. None on cache hits
    # (we only collect on the compute path — a cached DV result is not a new
    # Gemini call).
    model_used: str | None = Field(default=None)
    starting_model: str | None = Field(default=None)
    fallback_reason: str | None = Field(default=None)


_VALID_STANCES = {"optimistic", "skeptical", "cautionary", "neutral", "mixed"}
_VALID_ARCHETYPES = {
    "framework_api",
    "cli_tool",
    "library_thin",
    "docs_heavy",
    "app_example",
    "unknown",
}
_VALID_YT_FORMATS = {"lecture", "interview", "tutorial", "panel", "talk", "vlog", "unknown"}


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if _gapi_exc is not None:
        if isinstance(exc, _gapi_exc.ServerError):
            return True
        if isinstance(exc, _gapi_exc.ClientError):
            return False
        if isinstance(exc, _gapi_exc.GoogleAPICallError):
            code = getattr(exc, "code", None)
            try:
                code_int = int(code) if code is not None else None
            except Exception:  # noqa: BLE001
                code_int = None
            if code_int is not None and code_int >= 500:
                return True
            return False
    msg = str(exc).lower()
    return any(tok in msg for tok in ("500", "502", "503", "504", "timeout", "deadline", "unavailable"))


_PROMPT_TEMPLATE = """\
You are densifying and verifying the SOURCE below.

TASK A (density): write a maximally-faithful dense summary that preserves every
entity, number, constraint, and caveat from the source. Do not invent anything.

TASK B (verify): list any IMPORTANT claims from the source your dense summary
left out. Return an empty list when the dense summary already covers everything
worth preserving.

TASK C (source-specific hint):
{source_hint}

TASK D (framing): emit ONE sentence summarizing the core argument / thesis of
the source, and ONE sentence serving as a closing takeaway.

Return raw JSON only, matching this schema — no markdown, no commentary:
{schema_json}

SOURCE CONTEXT:
{source_ctx}

SOURCE:
{source_text}
"""


def _source_hint(source_type: Any) -> str:
    name = getattr(source_type, "value", str(source_type or "")).lower()
    if name == "newsletter":
        return (
            "Classify the newsletter stance as one of "
            "[optimistic, skeptical, cautionary, neutral, mixed]. "
            "Set ``archetype`` and ``format_label`` to null."
        )
    if name == "github":
        return (
            "Classify the repo archetype as one of "
            "[framework_api, cli_tool, library_thin, docs_heavy, app_example, unknown]. "
            "Set ``stance`` and ``format_label`` to null."
        )
    if name == "youtube":
        return (
            "Classify the video format as one of "
            "[lecture, interview, tutorial, panel, talk, vlog, unknown]. "
            "Set ``stance`` and ``archetype`` to null."
        )
    return "Set ``stance``, ``archetype``, and ``format_label`` to null."


class DenseVerifier:
    """Issue one Pro-tier Gemini call that returns a ``DenseVerifyResult``.

    The call is retried at most once on transient upstream failures (5xx,
    DeadlineExceeded, timeout). Permanent 4xx errors propagate. Pydantic
    validation failures raise immediately — callers are expected to downgrade
    gracefully (the structured extractor already has schema-fallback paths
    that cover this).
    """

    def __init__(self, client: TieredGeminiClient):
        self._client = client

    async def run(
        self,
        source_type: Any,
        content: str,
        context: str | None = None,
    ) -> DenseVerifyResult:
        prompt = _build_prompt(source_type, content, context)

        last_exc: BaseException | None = None
        for attempt in range(2):
            try:
                # C6: enforce 3-call LLM budget at the call site (only on the
                # first attempt — retries are upstream-transient and must not
                # double-charge).
                if attempt == 0:
                    get_budget().consume(role="dense_verify")
                result = await self._client.generate(
                    prompt,
                    tier="pro",
                    response_schema=DenseVerifyResult,
                    system_instruction=SYSTEM_PROMPT,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 1 or not _is_transient_error(exc):
                    raise
                logger.info(
                    "dense_verify.retry attempt=%d error_class=%s",
                    attempt + 1,
                    exc.__class__.__name__,
                )
                await asyncio.sleep(_RETRY_DELAY_SEC)
        else:  # pragma: no cover - loop always breaks or raises
            assert last_exc is not None
            raise last_exc

        raw = result.text or ""
        try:
            parsed = parse_json_object(raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"dense_verify: could not parse Gemini JSON ({type(exc).__name__}): "
                f"{raw[:240]!r}"
            ) from exc

        parsed = _coerce_raw(parsed, source_type)
        # Stash call telemetry so the summarizer can surface the DV call's
        # model_used/fallback_reason in SummaryMetadata.model_used.
        parsed["model_used"] = getattr(result, "model_used", None)
        parsed["starting_model"] = getattr(result, "starting_model", None)
        parsed["fallback_reason"] = getattr(result, "fallback_reason", None)
        try:
            return DenseVerifyResult(**parsed)
        except ValidationError as exc:
            logger.warning(
                "dense_verify.schema_invalid err=%s raw=%r",
                exc.errors()[:2],
                raw[:240],
            )
            raise


def _build_prompt(source_type: Any, content: str, context: str | None) -> str:
    schema_json = json.dumps(
        DenseVerifyResult.model_json_schema(), separators=(",", ":")
    )[:3000]
    hint = _source_hint(source_type)
    try:
        src_ctx = context or source_context(source_type)
    except Exception:  # noqa: BLE001 - source_context is defensive already
        src_ctx = context or ""
    return _PROMPT_TEMPLATE.format(
        source_hint=hint,
        schema_json=schema_json,
        source_ctx=src_ctx,
        source_text=content or "",
    )


def _coerce_raw(raw: Any, source_type: Any) -> dict[str, Any]:
    """Normalize the model's raw JSON into something pydantic accepts.

    - Non-dict payloads are rejected.
    - ``missing_facts`` defaults to [] when absent or wrong-typed.
    - Enum-like labels that disagree with the source type are set to None so
      pydantic doesn't block on a soft classifier disagreement.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"dense_verify: expected dict, got {type(raw).__name__}")
    out = dict(raw)

    mf = out.get("missing_facts")
    if not isinstance(mf, list):
        out["missing_facts"] = []
    else:
        out["missing_facts"] = [str(f) for f in mf if isinstance(f, str)]

    name = getattr(source_type, "value", str(source_type or "")).lower()
    stance = out.get("stance")
    if isinstance(stance, str):
        stance_low = stance.strip().lower()
        out["stance"] = stance_low if stance_low in _VALID_STANCES else None
    else:
        out["stance"] = None
    archetype = out.get("archetype")
    if isinstance(archetype, str):
        arch_low = archetype.strip().lower()
        out["archetype"] = arch_low if arch_low in _VALID_ARCHETYPES else None
    else:
        out["archetype"] = None
    fmt = out.get("format_label")
    if isinstance(fmt, str):
        fmt_low = fmt.strip().lower()
        out["format_label"] = fmt_low if fmt_low in _VALID_YT_FORMATS else None
    else:
        out["format_label"] = None

    # Scrub cross-source leakage: only the matching source_type may carry its
    # hint. Forces every other to None so downstream consumers aren't
    # surprised by a stance on a GitHub payload.
    if name != "newsletter":
        out["stance"] = None
    if name != "github":
        out["archetype"] = None
    if name != "youtube":
        out["format_label"] = None

    out["core_argument"] = str(out.get("core_argument") or "").strip()
    out["closing_hook"] = str(out.get("closing_hook") or "").strip()
    out["dense_text"] = str(out.get("dense_text") or "").strip()

    return out
