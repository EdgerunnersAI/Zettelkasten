"""The consolidated Gemini-Pro evaluation call."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import yaml

from website.features.summarization_engine.evaluator.models import EvalResult
from website.features.summarization_engine.evaluator.numeric_grounding import (
    extract_numeric_tokens,
    numeric_validator,
)
from website.features.summarization_engine.evaluator.prompts import (
    CONSOLIDATED_SYSTEM,
    CONSOLIDATED_USER_TEMPLATE,
    PROMPT_VERSION,
)
from website.features.summarization_engine.summarization.common.json_utils import (
    parse_json_object,
)

# Cap on number of unsupported numeric tokens reported, to keep the evaluator
# payload compact in eval-loop artifacts.
_UNSUPPORTED_NUMERIC_CAP = 5

# CF-2 R2: shape mask for which editorialization_flags must be zeroed.
# Keeps the LLM judge prompt drift from re-triggering hallucination_cap when
# a newsletter shape legitimately uses evaluative-sounding paper-fact language.
_NO_STANCE_PENALTY_SHAPES = frozenset({
    "academic_roundup",
    "link_digest",
    "news_aggregator",
    "product_announcement",
})


def compute_numeric_grounding_signal(
    summary_json: dict | None, source_text: str | None
) -> dict:
    """Faithfulness sub-signal: ratio of grounded numeric tokens to extracted ones.

    Returns a dict with two stable keys:
      - ``numeric_grounding_score`` (float in [0.0, 1.0]); 1.0 when summary has
        zero numeric tokens (vacuously grounded).
      - ``unsupported_numeric_claims`` (list[str]) capped at
        ``_UNSUPPORTED_NUMERIC_CAP`` entries.

    Raises ``TypeError`` for malformed input, with the offending key surfaced,
    so callers fail loudly instead of silently falling back to a default.
    """
    if summary_json is None:
        raise TypeError(
            "compute_numeric_grounding_signal: summary_json must not be None"
        )
    if not isinstance(summary_json, dict):
        raise TypeError(
            "compute_numeric_grounding_signal: summary_json must be dict, "
            f"got {type(summary_json).__name__}"
        )
    if source_text is None:
        raise TypeError(
            "compute_numeric_grounding_signal: source_text must not be None"
        )
    if not isinstance(source_text, str):
        raise TypeError(
            "compute_numeric_grounding_signal: source_text must be str, "
            f"got {type(source_text).__name__}"
        )

    summary_text = _flatten_summary_text(summary_json)
    tokens = extract_numeric_tokens(summary_text)
    if not tokens:
        return {
            "numeric_grounding_score": 1.0,
            "unsupported_numeric_claims": [],
        }
    result = numeric_validator(summary_text, source_text)
    ungrounded: list[str] = list(result.get("ungrounded", []))
    return {
        "numeric_grounding_score": float(result.get("ratio", 0.0)),
        "unsupported_numeric_claims": ungrounded[:_UNSUPPORTED_NUMERIC_CAP],
    }


def _flatten_summary_text(summary_json: dict) -> str:
    """Concatenate every string leaf in the summary payload.

    The summarizer's JSON shape varies per archetype (mini_title / brief /
    detailed / sections / tags / etc.). Rather than hard-code keys, we walk
    the structure and collect every string for grounding inspection.
    """
    parts: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v)

    _walk(summary_json)
    return "\n".join(parts)


class ConsolidatedEvaluator:
    def __init__(self, gemini_client: Any) -> None:
        self._client = gemini_client

    def _tier(self) -> str:
        cfg = getattr(self._client, "_config", None)
        if cfg is None:
            return "pro"
        return getattr(cfg.gemini, "phase_tiers", {}).get("evaluator", "pro")

    async def evaluate(
        self,
        *,
        rubric_yaml: dict,
        atomic_facts: list[dict],
        source_text: str,
        summary_json: dict,
    ) -> EvalResult:
        # CF-2 R2: surface newsletter content shape to the judge so it can
        # apply shape-aware rubric overrides (academic_roundup etc.).
        shape = str(summary_json.get("_shape", "general")) if isinstance(
            summary_json, dict
        ) else "general"
        prompt = CONSOLIDATED_USER_TEMPLATE.format(
            _shape=shape,
            rubric_yaml=yaml.safe_dump(rubric_yaml, sort_keys=False),
            atomic_facts=json.dumps(atomic_facts, indent=2),
            source_text=source_text[:30000],
            summary_json=json.dumps(summary_json, indent=2),
        )
        import asyncio as _asyncio

        t0 = time.perf_counter()
        last_text = ""
        result = None
        last_exc: Exception | None = None
        payload: dict | None = None
        for attempt in range(3):
            try:
                result = await self._client.generate(
                    prompt,
                    tier=self._tier(),
                    system_instruction=CONSOLIDATED_SYSTEM,
                    temperature=0.0,
                    max_output_tokens=32768,
                    role="rubric_evaluator",
                )
                last_text = (result.text or "").strip()
                if last_text:
                    try:
                        payload = parse_json_object(last_text)
                        last_exc = None
                        break
                    except Exception as exc:  # noqa: BLE001 - retry malformed JSON
                        last_exc = exc
            except Exception as exc:  # noqa: BLE001 — retry on any transient failure
                last_exc = exc
            if attempt < 2:
                await _asyncio.sleep(2 * (attempt + 1))
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if last_exc is not None and not last_text:
            raise RuntimeError(
                f"Evaluator failed after 3 attempts: {type(last_exc).__name__}: {last_exc}"
            ) from last_exc

        if not last_text:
            raise RuntimeError(
                "Evaluator returned empty text after 3 attempts "
                f"(model={getattr(result, 'model_used', '?')}, "
                f"in={getattr(result, 'input_tokens', 0)}, "
                f"out={getattr(result, 'output_tokens', 0)})"
            )

        if payload is None:
            preview = last_text[:200].replace("\n", " ")
            raise RuntimeError(
                f"Evaluator returned non-JSON after 3 attempts: {last_exc} | preview={preview!r}"
            ) from last_exc

        payload.setdefault("evaluator_metadata", {})
        payload["evaluator_metadata"].setdefault("prompt_version", PROMPT_VERSION)
        payload["evaluator_metadata"].setdefault(
            "rubric_version", rubric_yaml.get("version", "unknown")
        )
        payload["evaluator_metadata"]["implementation_fingerprint"] = (
            evaluator_implementation_fingerprint()
        )
        payload["evaluator_metadata"]["rubric_sha256"] = rubric_sha256(rubric_yaml)
        payload["evaluator_metadata"]["model_used"] = getattr(
            result, "model_used", payload["evaluator_metadata"].get("model_used")
        )
        payload["evaluator_metadata"]["total_tokens_in"] = getattr(
            result, "input_tokens", 0
        )
        payload["evaluator_metadata"]["total_tokens_out"] = getattr(
            result, "output_tokens", 0
        )
        payload["evaluator_metadata"]["latency_ms"] = latency_ms

        # Faithfulness sub-signal: deterministic numeric grounding check
        # against the source text. Surfaced inside ``evaluator_metadata`` to
        # preserve the existing ``EvalResult`` schema (backward compatible).
        numeric_signal = compute_numeric_grounding_signal(summary_json, source_text)
        payload["evaluator_metadata"]["numeric_grounding_score"] = numeric_signal[
            "numeric_grounding_score"
        ]
        payload["evaluator_metadata"]["unsupported_numeric_claims"] = numeric_signal[
            "unsupported_numeric_claims"
        ]
        # CF-2 R2 belt-and-braces: zero editorialization_flags for shape-exempt
        # newsletters so prompt drift cannot regress to hallucination_cap.
        if shape in _NO_STANCE_PENALTY_SHAPES:
            payload["editorialization_flags"] = []
            payload["evaluator_metadata"]["editorialization_zeroed_by_shape"] = shape
        return EvalResult(**payload)


def evaluator_implementation_fingerprint() -> str:
    digest = hashlib.sha256()
    evaluator_dir = Path(__file__).resolve().parent
    for path in sorted(evaluator_dir.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def rubric_sha256(rubric_yaml: dict) -> str:
    payload = yaml.safe_dump(rubric_yaml, sort_keys=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
