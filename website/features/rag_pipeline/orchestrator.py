"""Top-level RAG orchestration."""

from __future__ import annotations

import asyncio
import enum
import hashlib
import logging
import os
import re
import time
import traceback
from collections import namedtuple
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import uuid4

logger = logging.getLogger(__name__)

try:
    from langfuse import get_client, observe
except Exception:  # pragma: no cover - optional dependency fallback
    def observe(*args, **kwargs):
        def _decorator(func):
            return func

        return _decorator

    class _DummyLangfuse:
        def get_current_trace_id(self):
            return None

    def get_client():
        return _DummyLangfuse()

from website.features.rag_pipeline.errors import EmptyScopeError
from website.features.rag_pipeline.generation.prompts import (
    NO_CONTEXT_MARKER,
    REFUSAL_PHRASE,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
)
from website.features.rag_pipeline.generation.sanitize import (
    has_valid_citation,
    sanitize_answer,
    strip_invalid_citations,
)
from website.features.rag_pipeline.observability import record_generation_cost, trace_stage, track_latency
from website.features.rag_pipeline.query.metadata import QueryMetadata, QueryMetadataExtractor
from website.features.rag_pipeline.query.router import apply_class_overrides
from website.features.rag_pipeline.retrieval.planner import RetrievalPlanner
from website.features.rag_pipeline.types import AnswerTurn, Citation, QueryClass
from website.features.rag_pipeline.retrieval.hybrid import _top1_top2_gap

# T20: env-gated KG-first planner. Defaults on so prod gets the new path,
# but operators can disable via ``RAG_KG_FIRST_ENABLED=false`` for incident
# rollback without redeploying.
_KG_FIRST_ENABLED = os.environ.get("RAG_KG_FIRST_ENABLED", "true").lower() == "true"

# iter-04: critic-retry hardening (the q9 fix — first-pass refusal scored
# unsupported re-ran the whole pipeline for 46.7s and produced an identical
# refusal). See research findings — this block implements:
#   * refusal-regex short-circuit (CRAG-style "Incorrect" termination)
#   * top-rerank-score floor short-circuit
#   * deja-vu hash to prevent identical retry
#   * 12s wall-clock budget cap (asyncio.wait_for)
#   * mutation matrix so retry materially differs from first attempt
# Tunable via env: RAG_RETRY_BUDGET_S=12.0
_RETRY_BUDGET_S = float(os.environ.get("RAG_RETRY_BUDGET_S", "12.0"))
_RETRY_TOP_SCORE_FLOOR = float(os.environ.get("RAG_RETRY_TOP_SCORE_FLOOR", "0.10"))
# iter-07 Fix A: refusal-regex semantic gate. When retrieval is strong
# (top rerank ≥ floor) skip the refusal-regex short-circuit so the
# critic-retry path runs. Fixes q3 (correct retrieval, model said
# "cannot find specific quotes" → unsupported_no_retry blocked retry).
_REFUSAL_SEMANTIC_GATE_ENABLED = os.environ.get(
    "RAG_REFUSAL_SEMANTIC_GATE_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
_REFUSAL_SEMANTIC_GATE_FLOOR = float(
    os.environ.get("RAG_REFUSAL_SEMANTIC_GATE_FLOOR", "0.5")
)

# iter-08 Fix A: when the first-pass critic verdict is ``partial`` AND we
# already have a gold-tier candidate (top rerank score >= 0.5), short-circuit
# the unsupported→retry path. iter-07 q4 regressed from ``partial`` (good) to
# ``unsupported_no_retry`` (bad) because the partial→unsupported escalation
# triggered retry, retry produced a refusal-shaped answer, and the original
# partial draft was overwritten. Default on; flip RAG_PARTIAL_NO_RETRY_ENABLED
# to false for incident rollback.
_PARTIAL_NO_RETRY_ENABLED = os.environ.get(
    "RAG_PARTIAL_NO_RETRY_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
_PARTIAL_NO_RETRY_FLOOR = 0.5

# iter-09 RES-1: parallel skip gate for unsupported-with-gold (LOOKUP only).
# Critic flagged unsupported but retrieval surfaced a gold-tier chunk; the
# first-pass draft is materially better than burning ~12s on retry. Stricter
# floor (0.7 vs 0.5) since the verdict is harsher than ``partial``.
_UNSUPPORTED_WITH_GOLD_SKIP_ENABLED = os.environ.get(
    "RAG_UNSUPPORTED_WITH_GOLD_SKIP_ENABLED", "true"
).lower() not in ("false", "0", "no", "off")
_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR = float(
    os.environ.get("RAG_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR", "0.7")
)

# iter-11 Class F (operator-approved): per-class additive offset on the
# critic-skip floors. Cross-corpus THEMATIC / STEP_BACK queries lack single-
# chunk grounding even when 4-5 zettels collectively support the answer, so
# the LOOKUP-calibrated 0.5 / 0.7 literals refuse legitimately-grounded
# cross-corpus answers (q5 root cause). The offset LOWERS the effective
# floor for THEMATIC / STEP_BACK only — LOOKUP / VAGUE keep the locked
# literals (CLAUDE.md guardrail). Hard-clamped at _CRITIC_FLOOR_HARD_MIN
# so an extreme env value cannot disable the gate outright.
_CRITIC_FLOOR_HARD_MIN = 0.3

# iter-12 Class K3: confidence-gap bypass for retry-skip. When top1/top2 >=
# threshold the retrieved ordering is already decisive; absolute floors are moot.
_RETRY_GAP_BYPASS = float(os.environ.get("RAG_RETRY_GAP_BYPASS", "1.5"))


def _retry_gap_bypass_threshold() -> float:
    return _RETRY_GAP_BYPASS


# iter-12 Task 35: per-query retry outcome label for iter-13 empirical gate.
class RetryOutcomeClass(str, enum.Enum):
    SUCCESS = "success"
    EMPTY_POOL = "empty_pool"
    TIMEOUT = "timeout"
    FLOOR_FAILED = "floor_failed"
    STILL_UNSUPPORTED = "still_unsupported"


def classify_retry_outcome(
    *, retrieved_count: int, retry_fired: bool, timed_out: bool,
    critic_verdict: str,
) -> RetryOutcomeClass:
    """Map retrieval/retry state to a telemetry label. Pure function, no side-effects."""
    if retrieved_count == 0:
        return RetryOutcomeClass.EMPTY_POOL
    if timed_out:
        return RetryOutcomeClass.TIMEOUT
    if critic_verdict in ("unsupported_with_gold_skip",):
        return RetryOutcomeClass.FLOOR_FAILED
    if critic_verdict in ("unsupported_no_retry",) and retry_fired:
        return RetryOutcomeClass.STILL_UNSUPPORTED
    return RetryOutcomeClass.SUCCESS


def _offset_for_class(query_class: "QueryClass | None", env_key: str) -> float:
    if query_class in (QueryClass.THEMATIC, QueryClass.STEP_BACK):
        return float(os.environ.get(env_key, "0.0"))
    return 0.0


def _effective_partial_floor(query_class: "QueryClass | None") -> float:
    offset = _offset_for_class(
        query_class, "RAG_PARTIAL_NO_RETRY_FLOOR_OFFSET_THEMATIC"
    )
    return max(_CRITIC_FLOOR_HARD_MIN, _PARTIAL_NO_RETRY_FLOOR + offset)


def _effective_unsupported_with_gold_skip_floor(
    query_class: "QueryClass | None",
) -> float:
    offset = _offset_for_class(
        query_class, "RAG_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR_OFFSET_THEMATIC"
    )
    return max(_CRITIC_FLOOR_HARD_MIN, _UNSUPPORTED_WITH_GOLD_SKIP_FLOOR + offset)

# iter-08 Fix B: drop the citations list when the final verdict signals a
# refusal (``unsupported_no_retry``) or the answer text is the canonical
# refusal phrase. Prevents the iter-02-era stray citation chip on refused
# answers. Default on; flip RAG_SUPPRESS_CITATIONS_ON_REFUSAL to false for
# rollback.
_SUPPRESS_CITATIONS_ON_REFUSAL = os.environ.get(
    "RAG_SUPPRESS_CITATIONS_ON_REFUSAL", "true"
).lower() not in ("false", "0", "no", "off")

# iter-08 Phase 5: cite hygiene. Filter _build_citations to ids the LLM
# actually emitted inline. RES-5: a["contexts"] in eval flows from citations
# (rag_eval_loop.py:122), so this directly tightens RAGAS context_precision.
# Default OFF for dark canary deploy.
_CITE_HYGIENE_ENABLED = os.environ.get(
    "RAG_CITE_HYGIENE_ENABLED", "false"
).lower() not in ("false", "0", "no", "off")
_CITE_HYGIENE_MIN_KEEP = int(os.environ.get("RAG_CITE_HYGIENE_MIN_KEEP", "1"))
_CITE_HYGIENE_FALLBACK_TOPK = int(os.environ.get("RAG_CITE_HYGIENE_FALLBACK_TOPK", "3"))

_CITED_ID_RE = re.compile(r'\[id\s*=\s*["\']?([a-zA-Z0-9_\-]+)["\']?\]')


def _extract_cited_ids(answer: str) -> set[str]:
    """iter-08 Phase 5: parse LLM-emitted citation tags from answer text."""
    if not answer:
        return set()
    return {m.group(1).strip() for m in _CITED_ID_RE.finditer(answer) if m.group(1).strip()}


# Refusal regex — matches the canonical no-context phrases the synth model
# produces when grounding is missing. Drawn from REFUSAL_PHRASE plus the
# observed q5/q9/q10 refusal templates. Case-insensitive.
_REFUSAL_RE = re.compile(
    r"(?i)("
    r"\bno relevant\b"
    r"|\bno information\b"
    r"|\bcannot find\b"
    r"|\binsufficient context\b"
    r"|\bi don'?t have\b"
    r"|\bdo not have\b"
    r"|\bunable to (?:find|locate|answer)\b"
    r"|\bnot covered in\b"
    r"|\bnot mentioned in\b"
    r"|\bcan'?t find that\b"
    r"|\bnot found in your zettels\b"
    r")"
)


class _RetryDejaVu(Exception):
    """Raised when a retry would replay identical params; caller short-circuits."""
    pass


def _retry_param_hash(qc: "QueryClass", variants: list[str]) -> str:
    payload = f"{getattr(qc, 'value', qc)}|{'||'.join(sorted(variants or []))}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _has_refusal_phrase(text: str) -> bool:
    if not text:
        return False
    return bool(_REFUSAL_RE.search(text))


def _top_candidate_score(used_candidates) -> float:
    best = 0.0
    for candidate in used_candidates or []:
        score = candidate.rerank_score if candidate.rerank_score is not None else (candidate.rrf_score or 0.0)
        if score > best:
            best = float(score)
    return best


def _should_skip_retry(
    *,
    answer_text: str,
    used_candidates,
    query_class: "QueryClass",
    metadata: QueryMetadata,
    first_verdict: str | None = None,
) -> tuple[bool, str | None]:
    """Decide whether to short-circuit the unsupported→retry path.

    Returns ``(skip, reason)``. ``reason`` is suitable for the critic verdict
    tag and for tracing.
    """
    top_score = _top_candidate_score(used_candidates)
    # iter-12 Class K3: clear-winner bypass — absolute floors are moot when gap >= 1.5.
    gap = _top1_top2_gap(used_candidates)
    if gap is not None and gap >= _RETRY_GAP_BYPASS:
        logger.info("retry_skip bypass=clear_winner gap=%.3f", gap)
        return True, "skip_clear_winner"
    # iter-08 Fix A: protect partial-with-gold first-pass answers. When the
    # critic returned ``partial`` AND retrieval surfaced a gold-tier chunk
    # (rerank >= 0.5), the partial draft is materially better than any retry
    # the model has produced in eval. Skip retry to preserve it.
    # iter-11 Class F: effective floor is class-conditional. LOOKUP / VAGUE
    # keep the locked 0.5 literal; THEMATIC / STEP_BACK can take an additive
    # negative offset down to the 0.3 hard floor.
    _partial_floor = _effective_partial_floor(query_class)
    if (
        _PARTIAL_NO_RETRY_ENABLED
        and first_verdict == "partial"
        and used_candidates
        and top_score >= _partial_floor
    ):
        return True, "partial_with_gold_skip"
    # iter-09 RES-1: LOOKUP unsupported with gold-tier rerank — surface
    # first-pass draft with neutral "reflects retrieved sources" tag rather
    # than burning a 12s retry budget for a verbatim-phrasing miss. Env
    # checked at call-time so operators can toggle without container restart.
    _gate_on = os.environ.get(
        "RAG_UNSUPPORTED_WITH_GOLD_SKIP_ENABLED", "true"
    ).lower() not in ("false", "0", "no", "off")
    # iter-11 Class F: effective unsupported-skip floor is class-conditional
    # via the same offset family. The query-class gate stays LOOKUP-only here
    # by iter-09 design (RES-1: harsher verdict, only safe for single-zettel
    # lookups). The Class F path provides headroom for future broadening
    # without changing today's class scope.
    _skip_floor = _effective_unsupported_with_gold_skip_floor(query_class)
    if (
        _gate_on
        and first_verdict == "unsupported"
        and query_class is QueryClass.LOOKUP
        and used_candidates
        and top_score >= _skip_floor
    ):
        return True, "unsupported_with_gold_skip"
    if _has_refusal_phrase(answer_text):
        # iter-07 Fix A: bypass when retrieval is strong — let retry run.
        if not (
            _REFUSAL_SEMANTIC_GATE_ENABLED
            and used_candidates
            and top_score >= _REFUSAL_SEMANTIC_GATE_FLOOR
        ):
            return True, "refusal_regex"
    if not used_candidates:
        return True, "no_candidates"
    if top_score < _RETRY_TOP_SCORE_FLOOR:
        return True, "evaluator_low_score"
    if query_class is QueryClass.VAGUE:
        ent_count = len(metadata.entities or []) + len(metadata.authors or [])
        if ent_count < 2:
            # iter-07 Fix D: when retrieval is strong (gazetteer expansion
            # surfaced a high-confidence chunk), let retry run instead of
            # short-circuiting on entity count alone. Mirrors Fix A's gate.
            if not (
                _REFUSAL_SEMANTIC_GATE_ENABLED
                and used_candidates
                and top_score >= _REFUSAL_SEMANTIC_GATE_FLOOR
            ):
                return True, "vague_low_entity"
    return False, None


# iter-04 retry-mutation matrix. Original class -> retry class. Guarantees the
# retry produces a non-trivially-different candidate set (vs the historical
# `_retry_with_thematic_context` that re-ran the same class for half the
# original classes, producing q9-style identical refusals).
_RETRY_MUTATION = {
    QueryClass.LOOKUP: QueryClass.THEMATIC,
    QueryClass.MULTI_HOP: QueryClass.STEP_BACK,
    QueryClass.STEP_BACK: QueryClass.THEMATIC,
    QueryClass.THEMATIC: QueryClass.MULTI_HOP,
    QueryClass.VAGUE: QueryClass.STEP_BACK,
}

langfuse = get_client()


# T17: map quality tier -> expected default Gemini model. The assembler uses
# this as a hint only (unknown values fall back to its quality budget), so
# divergence between this hint and the key pool's actual selection at
# generation time is non-fatal.
_DEFAULT_MODEL_BY_QUALITY: dict[str, str] = {
    "fast": "gemini-2.5-flash",
    "high": "gemini-2.5-pro",
}


def _default_model_for_quality(quality: str) -> str | None:
    return _DEFAULT_MODEL_BY_QUALITY.get(quality)


# Spec 2A.2: low-confidence inline tag (HTML <details>) returned alongside the
# 2nd-pass draft answer when the critic still flags it as unsupported. The
# canned-refusal path was removed because spec §3.6 prong 2 requires the user
# to see the model's best draft annotated as low-confidence rather than a
# blanket "I can't find" message that q3/q8 surfaced as a smoking gun.
_LOW_CONFIDENCE_DETAILS_TAG = (
    "\n\n<details>"
    "<summary>How sure am I?</summary>"
    "Citations don't fully cover this claim. The answer is the model's best draft."
    "</details>"
)

# iter-09 RES-1: distinct neutral tag for the gold-retrieved + critic-
# unsupported skip path. The answer IS grounded in retrieved sources; the
# critic flagged it for missing literal phrasing rather than missing evidence.
_GOLD_RETRIEVED_DETAILS_TAG = (
    "\n\n<details>"
    "<summary>How sure am I?</summary>"
    "Answer reflects retrieved sources; some details may be paraphrased rather than quoted verbatim."
    "</details>"
)

# iter-12 Q3 gate — return type for _decide_gold_skip_action.
GoldSkipDecision = namedtuple("GoldSkipDecision", ["verdict", "apply_gold_tag"])


def _decide_gold_skip_action(
    *,
    answer_text: str,
    raw_content: str,
    valid_ids: set,
) -> GoldSkipDecision:
    """iter-12 Q3 gate: honor gold-skip only when synth produced a real cited draft.

    Returns unsupported_no_retry when answer_text==REFUSAL_PHRASE (upstream
    citation-validation substituted it) so the user sees honest low-conf labeling
    instead of a masked "reflects retrieved sources" tag (q3 root cause).
    """
    raw = (raw_content or "").strip()
    has_real_draft = (
        answer_text != REFUSAL_PHRASE
        and bool(raw)
        and has_valid_citation(raw, valid_ids)
    )
    if has_real_draft:
        return GoldSkipDecision(verdict="unsupported_with_gold_skip", apply_gold_tag=True)
    return GoldSkipDecision(verdict="unsupported_no_retry", apply_gold_tag=False)


def _dedupe_anchor_entities(values: list[str]) -> list[str]:
    """Build the entity-anchor list passed to the transformer.

    Combines authors + entities from metadata, dedupes case-insensitively
    while preserving casing, drops empties, and caps at 4. Capping is
    important: too many anchors over-constrain the LLM prompt and the
    paraphrases collapse onto a single phrasing.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out[:4]


def _clear_exc_locals(exc: BaseException) -> None:
    """iter-05: clear traceback frame locals after logger.error(..., exc_info=True).
    CPython retains frame-local refs (turn/query/retry_context) until next gc; thrash root cause."""
    tb = getattr(exc, "__traceback__", None)
    if tb is not None:
        try:
            traceback.clear_frames(tb)
        except Exception:  # noqa: BLE001 - best-effort
            pass


def _wrap_with_low_confidence_tag(draft_answer: str) -> str:
    """Append the spec-§3.6 low-confidence inline tag to a draft answer.

    Pure helper — never raises. Idempotent: a draft that already contains the
    tag is returned unchanged so retries (or future re-wrapping) cannot
    double-stamp the marker.
    """
    text = draft_answer or ""
    if "<summary>How sure am I?</summary>" in text:
        return text
    return text + _LOW_CONFIDENCE_DETAILS_TAG


@dataclass(slots=True)
class _PreparedQuery:
    session_id: object
    trace_id: str
    standalone: str
    query_class: QueryClass
    variants: list[str]
    metadata: QueryMetadata = field(default_factory=QueryMetadata)


@dataclass(slots=True)
class _RetrievedContext:
    context_xml: str
    used_candidates: list
    # iter-10 P17: per-stage timing for iter-11 mid-flight abort design.
    # Captured in _retrieve_context; surfaced via turn.token_counts.stage_timings.
    t_retrieval_ms: int = 0
    t_rerank_ms: int = 0


@dataclass(slots=True)
class _GeneratedAnswer:
    content: str
    model: str
    token_counts: dict
    finish_reason: str


def _empty_context_refusal() -> "_GeneratedAnswer":
    """Canonical no-context response emitted by the generate short-circuit."""
    return _GeneratedAnswer(
        content=REFUSAL_PHRASE,
        model="short_circuit",
        token_counts={"prompt": 0, "completion": 0, "total": 0},
        finish_reason="empty_context",
    )


@dataclass(slots=True)
class _PipelineResult:
    turn: AnswerTurn
    replaced_text: str | None = None


class RAGOrchestrator:
    def __init__(
        self,
        *,
        rewriter,
        router,
        transformer,
        retriever,
        graph_scorer,
        reranker,
        assembler,
        llm,
        critic,
        sessions,
        metadata_extractor: QueryMetadataExtractor | None = None,
        planner: RetrievalPlanner | None = None,
    ):
        self._rewriter = rewriter
        self._router = router
        self._transformer = transformer
        self._retriever = retriever
        self._graph = graph_scorer
        self._reranker = reranker
        self._assembler = assembler
        self._llm = llm
        self._critic = critic
        self._sessions = sessions
        self._metadata_extractor = metadata_extractor
        self._planner = planner

    @observe(name="rag.answer")
    async def answer(self, *, query, user_id, graph_weight_override: float | None = None):
        # iter-03 §B (2026-04-29): SLO budget — log-only, no cancellation.
        # Soft (5s) → WARNING. Critical (20s) → CRITICAL. Pipeline always
        # runs to completion; we'd rather ship a slow correct answer than
        # a polite refusal during the quality-ramp phase. Once P50 < 10s
        # we can layer hard-cancellation back on top.
        from website.api._latency_budget import LatencyBudget

        bgt = LatencyBudget()
        prepared = await self._prepare_query(query=query, user_id=user_id)
        bgt.checkpoint("after_prepare_query")
        result = await self._run_nonstream(
            query=query,
            user_id=user_id,
            prepared=prepared,
            graph_weight_override=graph_weight_override,
        )
        bgt.finalize("after_run_nonstream")
        return result.turn

    @observe(name="rag.answer_stream")
    async def answer_stream(self, *, query, user_id):
        prepared = await self._prepare_query(query=query, user_id=user_id)
        yield {"type": "status", "stage": "retrieving"}

        try:
            context = await self._retrieve_context(
                query=query,
                user_id=user_id,
                query_variants=prepared.variants,
                query_class=prepared.query_class,
                query_meta=prepared.metadata,
            )
        except EmptyScopeError:
            yield {
                "type": "error",
                "code": "empty_scope",
                "message": "This sandbox has no Zettels in the selected scope.",
            }
            return

        # iter-08 G1: filtered set comes in the final event
        yield {
            "type": "citations",
            "citations": [citation.model_dump() for citation in self._build_citations(context.used_candidates)],
            "tentative": True,
        }

        generation = None
        async for event in self._generate_streaming(query=query, context_xml=context.context_xml):
            if event["type"] == "token":
                yield event
                continue
            generation = event["answer"]

        if generation is None:
            generation = _GeneratedAnswer(content="", model="", token_counts={}, finish_reason="")

        result = await self._finalize_answer(
            query=query,
            user_id=user_id,
            prepared=prepared,
            context=context,
            generation=generation,
        )

        if result.replaced_text is not None:
            yield {"type": "replace", "content": result.replaced_text}

        # mode="json" coerces UUID/datetime/Enum to JSON-native types so the
        # downstream SSE encoder doesn't have to special-case them.
        yield {"type": "done", "turn": result.turn.model_dump(mode="json")}

    @trace_stage("prepare_query")
    async def _prepare_query(self, *, query, user_id) -> _PreparedQuery:
        trace_id = getattr(langfuse, "get_current_trace_id", lambda: None)() or str(uuid4())

        session_id = query.session_id
        if session_id is None:
            session_id = await self._sessions.create_session(
                user_id=user_id,
                sandbox_id=query.sandbox_id,
                quality_mode=query.quality,
            )

        # iter-04: same defensive pattern as the assistant writeback at line ~825 — persistence is best-effort.
        try:
            await self._sessions.append_user_message(
                session_id=session_id,
                user_id=user_id,
                content=query.content,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("user message writeback failed (request continues): %s", exc, exc_info=True)
            _clear_exc_locals(exc)

        # iter-03 §B (2026-04-29): pipeline-wide mem-trace. Each stage logs
        # rss_kb at exit so we can compute per-stage deltas for a single
        # query. Prior tracer only covered stage-2; this gives full
        # attribution (rewriter / router / transformer / metadata / retrieve
        # / graph_score / rerank / assemble / synth).
        from website.api._mem_trace import log_rss as _log_rss
        _log_rss("pipeline.enter", session_id=session_id)

        history = await self._sessions.load_recent_turns(session_id, user_id)
        history_payload = [
            turn.model_dump() if hasattr(turn, "model_dump") else turn
            for turn in history
        ]
        _log_rss("pipeline.history_loaded", turns=len(history_payload))

        standalone = await self._rewriter.rewrite(query.content, history_payload)
        _log_rss("pipeline.rewriter_done")

        # iter-04: sequence metadata → class-override → entity-anchored
        # transformer instead of running transformer ‖ metadata in parallel.
        # The parallel branch saved ~1-2s but blocked the q10 / q5 fix
        # because the transformer needs ``metadata.authors`` (post-A-pass)
        # to anchor proper-noun entities into every variant. The Gemini
        # cost stays identical (router + metadata + transform = 3 calls);
        # only the wall-clock changes from max(t,m) to t+m. Per the
        # Quality First rule (CLAUDE.md), correctness wins over the ~1-2s
        # latency at this stage.
        # Disable via RAG_METADATA_EXTRACTOR_ENABLED=false to fall back to
        # the parallel/no-entity behaviour.
        llm_query_class = await self._router.classify(standalone)
        _log_rss("pipeline.router_done", qc=str(llm_query_class))

        _metadata_enabled = (
            os.environ.get("RAG_METADATA_EXTRACTOR_ENABLED", "true").lower()
            not in ("false", "0", "no", "off")
        )
        if self._metadata_extractor is None or not _metadata_enabled:
            metadata = QueryMetadata()
        else:
            try:
                metadata = await self._metadata_extractor.extract(
                    standalone, query_class=llm_query_class
                )
            except Exception as exc:  # noqa: BLE001 - best-effort enrichment
                logger.warning("query metadata extract failed: %s", exc)
                metadata = QueryMetadata()
        _log_rss("pipeline.metadata_done")

        # iter-04: vote-table override — class-auto-correct after metadata.
        # See website/features/rag_pipeline/query/router.py:apply_class_overrides
        # for the full priority order. Logged at DEBUG so we can audit
        # router-LLM drift in production.
        query_class, override_reason = apply_class_overrides(
            standalone,
            llm_query_class,
            person_entities=list(metadata.authors or []),
        )
        if override_reason:
            logger.info(
                "router class override: llm=%s -> final=%s (reason=%s)",
                llm_query_class.value,
                query_class.value,
                override_reason,
            )
        _log_rss(
            "pipeline.class_overridden",
            qc=str(query_class),
            override=override_reason or "none",
        )

        # iter-04: entity-anchored transformer. Pulls authors + entities from
        # metadata so proper nouns survive paraphrase / decomposition.
        anchor_entities = _dedupe_anchor_entities(
            list(metadata.authors or []) + list(metadata.entities or [])
        )
        variants = await self._transformer.transform(
            standalone, query_class, entities=anchor_entities
        )
        _log_rss("pipeline.transformer_done", variants=len(variants))

        # iter-03 §B (2026-04-29): release the metadata-extract residual
        # (Gemini structured-output protobuf buffers, ~140 MB) before
        # retrieval/stage-1/stage-2 begin.
        from website.api._mem_release import aggressive_release as _ar
        _ar()
        _log_rss("pipeline.prepare_released")

        return _PreparedQuery(
            session_id=session_id,
            trace_id=trace_id,
            standalone=standalone,
            query_class=query_class,
            variants=variants,
            metadata=metadata,
        )

    async def _run_nonstream(
        self,
        *,
        query,
        user_id,
        prepared: _PreparedQuery,
        graph_weight_override: float | None = None,
    ) -> _PipelineResult:
        context = await self._retrieve_context(
            query=query,
            user_id=user_id,
            query_variants=prepared.variants,
            query_class=prepared.query_class,
            graph_weight_override=graph_weight_override,
            query_meta=prepared.metadata,
        )
        # iter-10 P17: capture synth-stage wall time alongside the retrieve /
        # rerank timestamps already on `context`. iter-11 mid-flight abort
        # design needs all three to pick safe abort points.
        _t_synth_start = time.monotonic_ns()
        generation = await self._generate_once(query=query, context_xml=context.context_xml)
        _t_synth_ms = (time.monotonic_ns() - _t_synth_start) // 1_000_000
        result = await self._finalize_answer(
            query=query,
            user_id=user_id,
            prepared=prepared,
            context=context,
            generation=generation,
        )
        # Piggy-back on the existing token_counts dict so the wire payload
        # surfaces stage_timings without a model schema change.
        try:
            tc = dict(result.turn.token_counts or {})
            tc["stage_timings"] = {
                "t_retrieval_ms": int(context.t_retrieval_ms),
                "t_rerank_ms": int(context.t_rerank_ms),
                "t_synth_ms": int(_t_synth_ms),
            }
            result.turn.token_counts = tc
        except Exception:  # noqa: BLE001 — best-effort instrumentation
            pass
        logger.info(
            "stage_timings retrieval=%dms rerank=%dms synth=%dms",
            int(context.t_retrieval_ms),
            int(context.t_rerank_ms),
            int(_t_synth_ms),
        )
        return result

    @trace_stage("retrieve_context")
    async def _retrieve_context(
        self,
        *,
        query,
        user_id,
        query_variants,
        query_class: QueryClass,
        graph_weight_override: float | None = None,
        query_meta: QueryMetadata | None = None,
    ) -> _RetrievedContext:
        async with track_latency("retrieve_context"):
            # T20: KG-first planning. When the planner is wired and the env
            # flag is on, narrow the scope_filter to entity-related node IDs
            # before retrieve. Any planner failure -> degrade to the original
            # scope so a planner regression cannot break the request path.
            scope_filter = query.scope_filter
            if (
                _KG_FIRST_ENABLED
                and self._planner is not None
                and query_meta is not None
                and query_meta.entities
            ):
                try:
                    scope_filter = await self._planner.plan(
                        user_id=user_id,
                        query_meta=query_meta,
                        query_class=query_class,
                        scope_filter=scope_filter,
                    )
                except Exception as exc:  # noqa: BLE001 - degrade silently
                    logger.warning("retrieval planner failed: %s", exc)
                    scope_filter = query.scope_filter
            from website.api._mem_trace import log_rss as _log_rss
            # iter-03 §B (2026-04-29): trim retrieval limit. Stage-1
            # FlashRank gets all retrieved candidates and runs MiniLM in a
            # single batch — observed +328 MB activation peak on 20-22
            # candidates. Cutting retrieval to 15/25 halves the stage-1
            # forward-pass memory while still surfacing top-10 to stage-2
            # post-trim. Quality impact is minimal: stage-2 sees the same
            # top-10 either way. Override via RAG_RETRIEVAL_LIMIT_FAST /
            # _STRONG for tuning.
            _retrieval_fast = int(os.environ.get("RAG_RETRIEVAL_LIMIT_FAST", "20"))
            _retrieval_strong = int(os.environ.get("RAG_RETRIEVAL_LIMIT_STRONG", "25"))
            # iter-10 P17: per-stage timing for iter-11 mid-flight abort design.
            _t_retr_start = time.monotonic_ns()
            candidates = await self._retriever.retrieve(
                user_id=user_id,
                query_variants=query_variants,
                sandbox_id=query.sandbox_id,
                scope_filter=scope_filter,
                query_class=query_class,
                limit=_retrieval_fast if query.quality == "fast" else _retrieval_strong,
                # iter-11 Phase 1 / Task 2 wiring: thread query_meta into retrieve()
                # so HybridRetriever's iter-08 anchor-boost gate (resolve_anchor_nodes
                # -> per-entity union) actually runs in production. Prior iters
                # silently passed None, short-circuiting the gate (q10 root cause).
                query_metadata=query_meta,
            )
            _t_retrieval_ms = (time.monotonic_ns() - _t_retr_start) // 1_000_000
            _log_rss("pipeline.retriever_done", cands=len(candidates))
            # T20: pass query_class through so graph_score activates the
            # dormant T24 usage-edge bonus tier (see graph_score.score).
            await self._graph.score(
                user_id=user_id,
                candidates=candidates,
                query_class=query_class,
            )
            _log_rss("pipeline.graph_score_done")
            _t_rerank_start = time.monotonic_ns()
            ranked = await self._reranker.rerank(
                query.content,
                candidates,
                top_k=8 if query.quality == "fast" else 12,
                query_class=query_class,
                graph_weight_override=graph_weight_override,
            )
            _t_rerank_ms = (time.monotonic_ns() - _t_rerank_start) // 1_000_000
            _log_rss("pipeline.rerank_done", ranked=len(ranked))
            # T17: hint the assembler at the LLM tier we will most likely
            # invoke so it can pick a per-tier token budget. Derived from
            # ``quality`` because the actual key/model is not chosen until
            # the generation step runs through the key pool — passing the
            # default tier is a safe pre-commit estimate that the assembler
            # treats as advisory (unknown values fall back to quality).
            # Assembler signature was extended (iter-03 §B) to take
            # query_class for class-aware context-floor logic. Older test
            # stubs / alternate assemblers may not accept the kwarg, so
            # fall back to the legacy signature on TypeError.
            try:
                context_xml, used_candidates = await self._assembler.build(
                    candidates=ranked,
                    quality=query.quality,
                    user_query=query.content,
                    model=_default_model_for_quality(query.quality),
                    query_class=query_class,
                )
            except TypeError:
                context_xml, used_candidates = await self._assembler.build(
                    candidates=ranked,
                    quality=query.quality,
                    user_query=query.content,
                    model=_default_model_for_quality(query.quality),
                )
            _log_rss(
                "pipeline.assembler_done",
                ctx_chars=len(context_xml),
                used=len(used_candidates),
            )
            # iter-04: drop unused 12-17 candidate blobs + trim heap before synth (swap was 470/1024MB).
            ctx_result = _RetrievedContext(
                context_xml=context_xml,
                used_candidates=used_candidates,
                t_retrieval_ms=int(_t_retrieval_ms),
                t_rerank_ms=int(_t_rerank_ms),
            )
            del candidates, ranked
            from website.api._mem_release import aggressive_release as _ar
            _ar()
            _log_rss("pipeline.retrieve_released")
        return ctx_result

    @trace_stage("generate_once")
    async def _generate_once(self, *, query, context_xml: str) -> _GeneratedAnswer:
        if NO_CONTEXT_MARKER in context_xml:
            return _empty_context_refusal()
        user_prompt = USER_TEMPLATE.format(
            context_xml=context_xml,
            user_query=query.content,
        )
        from website.api._mem_trace import log_rss as _log_rss
        _log_rss("pipeline.synth_start", prompt_chars=len(user_prompt))
        async with track_latency("generate_once"):
            generation = await self._llm.generate(
                query=query,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
        _log_rss(
            "pipeline.synth_done",
            answer_chars=len(getattr(generation, "content", "") or ""),
        )
        result = _GeneratedAnswer(
            content=generation.content,
            model=getattr(generation, "model", ""),
            token_counts=getattr(generation, "token_counts", {}),
            finish_reason=getattr(generation, "finish_reason", ""),
        )
        record_generation_cost(model=result.model, token_counts=result.token_counts)
        return result

    @trace_stage("generate_streaming")
    async def _generate_streaming(self, *, query, context_xml: str) -> AsyncIterator[dict]:
        if NO_CONTEXT_MARKER in context_xml:
            answer = _empty_context_refusal()
            yield {"type": "token", "content": answer.content}
            yield {"type": "complete", "answer": answer}
            return
        user_prompt = USER_TEMPLATE.format(
            context_xml=context_xml,
            user_query=query.content,
        )
        parts = []
        final_meta = {"model": "", "token_counts": {}, "finish_reason": ""}
        async with track_latency("generate_streaming"):
            async for token, meta in self._llm.generate_stream(
                query=query,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            ):
                parts.append(token)
                final_meta = meta or final_meta
                yield {"type": "token", "content": token}

        answer = _GeneratedAnswer(
            content="".join(parts),
            model=final_meta.get("model", ""),
            token_counts=final_meta.get("token_counts", {}),
            finish_reason=final_meta.get("finish_reason", ""),
        )
        record_generation_cost(
            model=answer.model,
            token_counts=answer.token_counts,
        )
        yield {"type": "complete", "answer": answer}

    @trace_stage("finalize_answer")
    async def _finalize_answer(
        self,
        *,
        query,
        user_id,
        prepared: _PreparedQuery,
        context: _RetrievedContext,
        generation: _GeneratedAnswer,
    ) -> _PipelineResult:
        t0 = time.monotonic()
        # iter-04: keep a handle to the pre-citation-validation candidate
        # set so the unsupported-skip check below can distinguish "no
        # candidates from retrieval" (genuine empty Kasten -> skip retry)
        # from "candidates present but synth dropped the citation tag"
        # (retry might help). Without this, a streaming-path answer that
        # streamed tokens without the [id=...] tag would null out the
        # used_candidates list and trip the no_candidates short-circuit
        # by accident.
        pre_validation_candidates = list(context.used_candidates)
        answer_text = sanitize_answer(generation.content)
        valid_ids = {candidate.node_id for candidate in context.used_candidates}
        answer_text, _dropped_citations = strip_invalid_citations(answer_text, valid_ids)
        if (
            valid_ids
            and answer_text != REFUSAL_PHRASE
            and not has_valid_citation(answer_text, valid_ids)
        ):
            answer_text = REFUSAL_PHRASE
            context = _RetrievedContext(
                context_xml=context.context_xml,
                used_candidates=[],
            )
        verdict, details = await self._critic.verify(
            answer_text=answer_text,
            context_xml=context.context_xml,
            context_candidates=context.used_candidates,
        )
        # iter-08 Fix A: capture the pre-escalation critic verdict so the
        # retry-skip decision can distinguish "first pass was partial with a
        # gold candidate" (preserve it) from "first pass was a true
        # unsupported" (retry as before).
        first_verdict = verdict

        replaced_text = None
        used_candidates = context.used_candidates
        active_generation = generation

        # iter-04: escalate partial→unsupported ONLY when retrieval itself was empty (pre-validation).
        # If pre_validation had candidates but streaming dropped the [id=...] tag, retain partial.
        if verdict == "partial":
            tentative_citations = self._build_citations(context.used_candidates)
            if not tentative_citations and not pre_validation_candidates:
                verdict = "unsupported"
                logger.debug("partial->unsupported escalation: zero pre-validation candidates")

        if verdict == "unsupported":
            # iter-04: feed the ORIGINAL un-substituted generation.content to
            # the refusal-regex check. answer_text may have been replaced by
            # REFUSAL_PHRASE above when citation validation failed (streaming
            # path that lost the [id=...] tag), and that substitution is NOT
            # the model genuinely refusing — retry might recover. Same logic
            # for used_candidates: the validation block clears the list, but
            # the pre-validation set is what matters for "is the Kasten
            # actually empty".
            skip, skip_reason = _should_skip_retry(
                answer_text=generation.content or "",
                used_candidates=pre_validation_candidates,
                query_class=prepared.query_class,
                metadata=prepared.metadata,
                first_verdict=first_verdict,
            )
            if skip:
                # Refusal-aware short-circuit (q9 fix). Don't burn another
                # ~45s pipeline pass producing the same refusal. The skip
                # reason ("refusal_regex" | "no_candidates" |
                # "evaluator_low_score" | "vague_low_entity" |
                # "partial_with_gold_skip") is recorded in details so it
                # surfaces via critic_notes and the observability span.
                if not isinstance(details, dict):
                    details = {}
                details = {**details, "skip_reason": skip_reason}
                if skip_reason == "partial_with_gold_skip":
                    # iter-08 Fix A: preserve the partial draft + partial
                    # verdict. This path fires when first_verdict was
                    # ``partial`` and we had a gold-tier candidate, so the
                    # retry would only degrade the answer (q4 regression).
                    verdict = "partial"
                    # answer_text and replaced_text intentionally untouched;
                    # the partial draft is the user-facing answer.
                elif skip_reason == "unsupported_with_gold_skip":
                    # iter-12 Q3: gate gold-skip tag on a real cited draft.
                    decision = _decide_gold_skip_action(
                        answer_text=answer_text,
                        raw_content=generation.content,
                        valid_ids=valid_ids,
                    )
                    verdict = decision.verdict
                    if decision.apply_gold_tag:
                        if "<summary>How sure am I?</summary>" not in (answer_text or ""):
                            answer_text = (answer_text or "") + _GOLD_RETRIEVED_DETAILS_TAG
                    else:
                        # Refusal-substituted draft → honest low-conf tag.
                        answer_text = _wrap_with_low_confidence_tag(answer_text or "")
                    replaced_text = answer_text
                else:
                    verdict = "unsupported_no_retry"
                    answer_text = _wrap_with_low_confidence_tag(answer_text)
                    replaced_text = answer_text
                logger.info(
                    "critic-retry short-circuit: reason=%s qc=%s",
                    skip_reason,
                    getattr(prepared.query_class, "value", prepared.query_class),
                )
            else:
                first_attempt_hash = _retry_param_hash(
                    prepared.query_class, prepared.variants
                )
                try:
                    retry_context, retry_generation, retry_verdict, retry_details = (
                        await asyncio.wait_for(
                            self._retry_with_mutated_context(
                                query=query,
                                user_id=user_id,
                                prepared=prepared,
                                first_attempt_hash=first_attempt_hash,
                            ),
                            timeout=_RETRY_BUDGET_S,
                        )
                    )
                    used_candidates = retry_context.used_candidates
                    active_generation = retry_generation
                    retry_valid_ids = {
                        c.node_id for c in retry_context.used_candidates
                    }
                    retry_answer = sanitize_answer(retry_generation.content)
                    retry_answer, _retry_dropped = strip_invalid_citations(
                        retry_answer, retry_valid_ids
                    )
                    if retry_verdict == "supported":
                        verdict = "retried_supported"
                        details = retry_details
                        answer_text = retry_answer
                        replaced_text = answer_text
                    else:
                        # 2nd-pass still unsupported — surface the retry
                        # draft with the spec §3.6 low-confidence tag.
                        verdict = "retried_low_confidence"
                        details = retry_details
                        answer_text = _wrap_with_low_confidence_tag(retry_answer)
                        replaced_text = answer_text
                except asyncio.TimeoutError:
                    verdict = "retry_budget_exceeded"
                    answer_text = _wrap_with_low_confidence_tag(answer_text)
                    replaced_text = answer_text
                    # iter-04: free retry's stage-1 buffers on cancel — wait_for leaves them dangling.
                    try:
                        from website.api._mem_release import aggressive_release as _ar
                        _ar()
                    except Exception:  # noqa: BLE001
                        pass
                    logger.warning(
                        "critic-retry budget exceeded (%.1fs); serving first-pass answer",
                        _RETRY_BUDGET_S,
                    )
                except _RetryDejaVu:
                    verdict = "retry_skipped_dejavu"
                    answer_text = _wrap_with_low_confidence_tag(answer_text)
                    replaced_text = answer_text
                    logger.info(
                        "critic-retry deja-vu: mutation matrix produced identical params"
                    )
                except Exception as exc:  # noqa: BLE001
                    # iter-04: catch-all so retry-path raise can never 500 — q3/q6/q9/q10 root-cause class.
                    verdict = "retry_failed"
                    answer_text = _wrap_with_low_confidence_tag(answer_text)
                    replaced_text = answer_text
                    try:
                        from website.api._mem_release import aggressive_release as _ar
                        _ar()
                    except Exception:  # noqa: BLE001
                        pass
                    logger.error(
                        "critic-retry raised (%s); serving first-pass answer with low-confidence tag",
                        type(exc).__name__,
                        exc_info=True,
                    )
                    _clear_exc_locals(exc)

        # iter-08 Fix B: pass verdict + a derived ``refused`` flag down to
        # the citation builder so refusal-shaped answers don't carry a
        # stray citation chip. ``refused`` covers the case where the
        # citation-validation block above swapped answer_text for the
        # canonical REFUSAL_PHRASE.
        _refused_flag = answer_text == REFUSAL_PHRASE
        turn = AnswerTurn(
            content=answer_text,
            citations=self._build_citations(
                used_candidates,
                verdict=verdict,
                refused=_refused_flag,
                answer_text=answer_text,  # iter-08 Phase 5
            ),
            query_class=prepared.query_class,
            critic_verdict=verdict,
            critic_notes=details.get("critic_error") if isinstance(details, dict) else None,
            trace_id=prepared.trace_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
            token_counts=active_generation.token_counts,
            llm_model=active_generation.model,
            retrieved_node_ids=[candidate.node_id for candidate in used_candidates],
            retrieved_chunk_ids=[candidate.chunk_id for candidate in used_candidates if candidate.chunk_id],
        )
        # iter-04: writeback never escapes — q9 500 root-cause was Supabase await raising after wait_for cancellation.
        try:
            await self._sessions.append_assistant_message(
                session_id=prepared.session_id,
                user_id=user_id,
                turn=turn,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("session writeback failed (turn served, persistence dropped): %s", exc, exc_info=True)
            _clear_exc_locals(exc)

        # iter-08 Phase 4.2: kasten_freq replaced by chunk-share normalization
        # (RES-2: floor=50 was never crossed in 6 iters of production runs).
        # record_hit was already a no-op writeback; bypassed for clarity.

        # iter-12 Task 35: per-query telemetry monitors (pure log + field — no behavior change).
        _retry_fired = "retry" in verdict
        _timed_out = verdict == "retry_budget_exceeded"
        _outcome = classify_retry_outcome(
            retrieved_count=len(pre_validation_candidates),
            retry_fired=_retry_fired,
            timed_out=_timed_out,
            critic_verdict=verdict,
        )
        turn.token_counts = dict(turn.token_counts or {})
        turn.token_counts["retry_outcome_class"] = _outcome.value
        _ce_scores = [
            float(c.rerank_score) for c in pre_validation_candidates
            if c.rerank_score is not None
        ]
        if _ce_scores:
            _desc = sorted(_ce_scores, reverse=True)
            _asc = sorted(_ce_scores)
            _n = len(_ce_scores)
            logger.info(
                "ce_score_distribution session_id=%s query_class=%s phase=%s "
                "n=%d top1=%.4f top3=%.4f median=%.4f p70=%.4f",
                prepared.session_id,
                getattr(prepared.query_class, "value", "unknown"),
                "retry" if _retry_fired else "initial",
                _n,
                _desc[0],
                _desc[2] if _n >= 3 else _desc[0],
                _asc[_n // 2],
                _asc[int(0.7 * _n)] if _n >= 4 else _asc[-1],
            )

        return _PipelineResult(turn=turn, replaced_text=replaced_text)

    async def _retry_with_mutated_context(
        self,
        *,
        query,
        user_id,
        prepared: _PreparedQuery,
        first_attempt_hash: str,
    ):
        """iter-04 mutation-matrix retry.

        Replaces the historical ``_retry_with_thematic_context`` which kept
        the same class for half the originals and produced q9-style identical
        retries. Mutation matrix at ``_RETRY_MUTATION`` guarantees the retry
        class differs from the first attempt so the retrieval candidate set
        is materially different. Raises ``_RetryDejaVu`` if the mutated params
        still hash-match the first attempt.
        """
        original_qc = prepared.query_class
        retry_qc = _RETRY_MUTATION.get(original_qc, QueryClass.THEMATIC)
        anchor_entities = _dedupe_anchor_entities(
            list(prepared.metadata.authors or [])
            + list(prepared.metadata.entities or [])
        )
        retry_variants = await self._transformer.transform(
            prepared.standalone, retry_qc, entities=anchor_entities
        )
        retry_hash = _retry_param_hash(retry_qc, retry_variants)
        if retry_hash == first_attempt_hash:
            raise _RetryDejaVu()

        retry_context = await self._retrieve_context(
            query=query,
            user_id=user_id,
            query_variants=retry_variants,
            query_class=retry_qc,
            query_meta=prepared.metadata,
        )
        retry_generation = await self._generate_once(
            query=query, context_xml=retry_context.context_xml
        )
        retry_verdict, retry_details = await self._critic.verify(
            answer_text=retry_generation.content,
            context_xml=retry_context.context_xml,
            context_candidates=retry_context.used_candidates,
        )
        return retry_context, retry_generation, retry_verdict, retry_details

    def _build_citations(
        self,
        candidates,
        *,
        verdict: str | None = None,
        refused: bool = False,
        answer_text: str | None = None,
    ) -> list[Citation]:
        # iter-08 Fix B: suppress citations on refused answers. The
        # ``unsupported_no_retry`` verdict (or an explicit refusal flag from
        # the caller) signals the user is being shown a refusal — a stray
        # citation chip there is misleading. Gated behind
        # RAG_SUPPRESS_CITATIONS_ON_REFUSAL (default on).
        if _SUPPRESS_CITATIONS_ON_REFUSAL and (
            refused or verdict == "unsupported_no_retry"
        ):
            return []
        by_node = {}
        for candidate in candidates:
            current = by_node.get(candidate.node_id)
            candidate_score = candidate.rerank_score if candidate.rerank_score is not None else candidate.rrf_score
            if current is None:
                by_node[candidate.node_id] = candidate
                continue

            current_score = current.rerank_score if current.rerank_score is not None else current.rrf_score
            if candidate_score > current_score:
                by_node[candidate.node_id] = candidate

        ranked_candidates = sorted(
            by_node.values(),
            key=lambda candidate: candidate.rerank_score if candidate.rerank_score is not None else candidate.rrf_score,
            reverse=True,
        )

        # iter-08 Phase 5: cite-hygiene filter (gated, default OFF).
        if _CITE_HYGIENE_ENABLED and answer_text:
            cited_ids = _extract_cited_ids(answer_text)
            if cited_ids:
                filtered = [c for c in ranked_candidates if c.node_id in cited_ids]
                if len(filtered) >= _CITE_HYGIENE_MIN_KEEP:
                    ranked_candidates = filtered
                else:
                    ranked_candidates = ranked_candidates[:_CITE_HYGIENE_FALLBACK_TOPK]
            # else: LLM cited nothing inline → keep ranked_candidates as today.

        return [
            Citation(
                id=candidate.node_id,
                node_id=candidate.node_id,
                title=candidate.name,
                source_type=candidate.source_type,
                url=candidate.url,
                snippet=candidate.content[:400],
                timestamp=str(candidate.metadata.get("timestamp")) if candidate.metadata.get("timestamp") else None,
                rerank_score=float(candidate.rerank_score or 0.0),
            )
            for candidate in ranked_candidates
        ]
