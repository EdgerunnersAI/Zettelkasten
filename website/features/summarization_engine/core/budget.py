"""Per-request LLM call budget enforcement + GenAI observability counters.

Industry pattern: ContextVar (PEP 567) set inside a FastAPI async dependency,
threaded implicitly through nested async/sync call stacks; explicit
``Budget.consume()`` call at every Gemini call site so the cost is visible to
code reviewers (decorator-only hides cost; middleware-only can't decrement
per-call).

Hard invariant: max 3 LLM calls per summarization. Test asserts this.

The counters in this module follow the OpenTelemetry GenAI semantic
conventions (https://opentelemetry.io/docs/specs/semconv/gen-ai/):

  - gen_ai_client_calls_total       (per-call counter)
  - gen_ai_client_budget_exceeded_total (3-call cap violations)
  - gen_ai_client_rate_limited_total (429 events per key/model/role)
  - gen_ai_client_quota_exhausted_total (hard-quota events)

These are scraped as Prometheus metrics and consumed by Grafana/Langfuse.

Labels are deliberately low-cardinality:
  - gen_ai_system: always "gemini" today
  - summarizer:   one of newsletter|youtube|github|reddit|default|unknown
  - role:         dense_verify|summarizer|repair|patch|other
  - model:        gemini-2.5-pro|gemini-2.5-flash|gemini-2.5-flash-lite
  - key_role:     free|billing

key_index is NEVER a label (high cardinality); always an event-field only.

SCOPE — production summarization only.
    The budget covers Gemini calls inside ``summarizer.summarize()`` (extract /
    verify / repair / structured-extract / patch). It MUST NOT be wired into
    eval-side calls such as ``extract_atomic_facts``, ``ConsolidatedEvaluator``,
    ``RagasBridge.faithfulness``, or any scoring/judge prompt — those are
    test-harness measurements and are explicitly out-of-budget by operator
    spec (2026-05-13). The scope is established in exactly one place
    (``core/orchestrator.py`` ``budget_scope()`` context) and closes before
    the harness invokes evaluators. Do NOT add ``get_budget().consume()``
    inside any evaluator/atomic-facts/judge code path.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Module-level ContextVar — copy-on-fork-safe; isolated per asyncio Task.
_BUDGET: ContextVar["Budget | None"] = ContextVar("_llm_budget", default=None)
_SUMMARIZER: ContextVar[str] = ContextVar("_summarizer", default="unknown")


class BudgetExceeded(RuntimeError):
    """Raised when summarization tries a 4th LLM call. Internal alarm.

    Should never bubble to user; indicates a code regression. Catch at the
    route boundary, log, and return HTTP 500.
    """


@dataclass
class Budget:
    """Per-request LLM call budget. Capped at 3 by spec invariant."""

    limit: int = 3
    used: int = 0
    summarizer: str = "unknown"
    overrun_attempts: int = 0

    def consume(self, n: int = 1, *, role: str = "llm") -> None:
        """Decrement budget. Raises BudgetExceeded if would exceed cap.

        Call this at every billable LLM call site (Gemini Pro or Flash).
        Non-LLM operations (transcript fetch, embedding cache, etc.) should
        NOT call this.
        """
        if self.used + n > self.limit:
            self.overrun_attempts += 1
            _emit_overrun_counter(self.summarizer, role)
            raise BudgetExceeded(
                f"summarizer={self.summarizer} role={role}: LLM call budget "
                f"{self.limit} exceeded (would consume {self.used + n})"
            )
        self.used += n
        _emit_call_counter(self.summarizer, role)
        logger.debug(
            "budget.consume summarizer=%s role=%s used=%d/%d",
            self.summarizer, role, self.used, self.limit,
        )


def get_budget() -> Budget:
    """Get current budget. Returns a no-op default if no budget is active
    (e.g., unit tests calling summarizer directly).
    """
    b = _BUDGET.get()
    if b is None:
        # Test-mode default — won't enforce, but won't crash either.
        b = Budget(limit=3, summarizer="test")
        _BUDGET.set(b)
    return b


async def llm_budget_dep(summarizer: str = "unknown") -> AsyncIterator[Budget]:
    """FastAPI async dependency. Sets up a fresh budget for this request,
    yields it, and resets the ContextVar on exit.
    """
    b = Budget(limit=3, summarizer=summarizer)
    tok = _BUDGET.set(b)
    s_tok = _SUMMARIZER.set(summarizer)
    try:
        yield b
    finally:
        if b.used > 0:
            logger.info(
                "summary.llm_calls summarizer=%s used=%d/%d overrun_attempts=%d",
                b.summarizer, b.used, b.limit, b.overrun_attempts,
            )
        _BUDGET.reset(tok)
        _SUMMARIZER.reset(s_tok)


@asynccontextmanager
async def budget_scope(summarizer: str = "unknown") -> AsyncIterator[Budget]:
    """Async context manager mirror of ``llm_budget_dep`` for non-FastAPI
    callers (e.g., the orchestrator entry point, eval harness).

    Same lifecycle: set ContextVar on enter, reset on exit, log usage.
    """
    b = Budget(limit=3, summarizer=summarizer)
    tok = _BUDGET.set(b)
    s_tok = _SUMMARIZER.set(summarizer)
    try:
        yield b
    finally:
        if b.used > 0:
            logger.info(
                "summary.llm_calls summarizer=%s used=%d/%d overrun_attempts=%d",
                b.summarizer, b.used, b.limit, b.overrun_attempts,
            )
        _BUDGET.reset(tok)
        _SUMMARIZER.reset(s_tok)


# --- Prometheus-style counters (OTel gen_ai_* aligned) ---
# Lightweight: if prometheus_client isn't available, fall back to log lines.
try:
    from prometheus_client import Counter

    LLM_CALLS_TOTAL = Counter(
        "gen_ai_client_calls_total",
        "Total LLM calls per summarizer.",
        ["gen_ai_system", "summarizer", "role"],
    )
    BUDGET_EXCEEDED = Counter(
        "gen_ai_client_budget_exceeded_total",
        "Times the per-request LLM budget cap was hit.",
        ["summarizer", "role"],
    )
    KEY_POOL_RATE_LIMITED = Counter(
        "gen_ai_client_rate_limited_total",
        "Times a Gemini call was rate-limited (per key, model, role).",
        ["gen_ai_system", "summarizer", "role", "model", "key_role"],
    )
    KEY_POOL_QUOTA_EXHAUSTED = Counter(
        "gen_ai_client_quota_exhausted_total",
        "Times a Gemini call hit hard quota exhaustion (per key, model, role).",
        ["gen_ai_system", "summarizer", "role", "model", "key_role"],
    )

    def _emit_call_counter(summarizer: str, role: str) -> None:
        LLM_CALLS_TOTAL.labels("gemini", summarizer, role).inc()

    def _emit_overrun_counter(summarizer: str, role: str) -> None:
        BUDGET_EXCEEDED.labels(summarizer, role).inc()

    def emit_rate_limited(
        *, summarizer: str, role: str, model: str, key_role: str
    ) -> None:
        KEY_POOL_RATE_LIMITED.labels("gemini", summarizer, role, model, key_role).inc()

    def emit_quota_exhausted(
        *, summarizer: str, role: str, model: str, key_role: str
    ) -> None:
        KEY_POOL_QUOTA_EXHAUSTED.labels("gemini", summarizer, role, model, key_role).inc()

except ImportError:  # pragma: no cover — prom optional

    def _emit_call_counter(summarizer: str, role: str) -> None:
        logger.info(
            "metric gen_ai_client_calls_total summarizer=%s role=%s",
            summarizer, role,
        )

    def _emit_overrun_counter(summarizer: str, role: str) -> None:
        logger.warning(
            "metric gen_ai_client_budget_exceeded_total summarizer=%s role=%s",
            summarizer, role,
        )

    def emit_rate_limited(
        *, summarizer: str, role: str, model: str, key_role: str
    ) -> None:
        logger.info(
            "metric gen_ai_client_rate_limited_total summarizer=%s role=%s "
            "model=%s key_role=%s",
            summarizer, role, model, key_role,
        )

    def emit_quota_exhausted(
        *, summarizer: str, role: str, model: str, key_role: str
    ) -> None:
        logger.warning(
            "metric gen_ai_client_quota_exhausted_total summarizer=%s role=%s "
            "model=%s key_role=%s",
            summarizer, role, model, key_role,
        )


__all__ = [
    "Budget",
    "BudgetExceeded",
    "budget_scope",
    "emit_quota_exhausted",
    "emit_rate_limited",
    "get_budget",
    "llm_budget_dep",
]
