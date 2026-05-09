# iter-12 RAG-eval Recovery Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read [RESEARCH.md](RESEARCH.md) before each phase.

**Goal:** Close iter-11's catastrophic-rollback chapter and hit **composite ≥ 85, accuracy_user_visible ≥ 0.85, within_budget ≥ 0.85, burst 502 = 0%, zero worker OOM**. iter-11 left a single dominant root cause (12+ sync `supabase.rpc().execute()` calls blocking the asyncio event loop on a 1-vCPU/2-worker droplet) with seven downstream symptoms. iter-12 ships PATH_F (Class P) as the foundational fix, then layers correctness fixes (Q3/Q5/Q7 with audit-mandated guards), dynamic primitives (K3 confidence-gap, K4 per-Kasten bootstrap) replacing six static knobs, and a trust-first scoring redesign (S + W).

**Architecture:** Eight class-generic fixes ordered by dependency — Class P (infra: PATH_F + anchor-boost re-enable in two deploys) is foundational, K3/K4 introduce dynamic primitives that replace iter-11's static thresholds, Q3/Q5/Q7 fix the user-visible regressions surfaced by the iter-11 forensic, Class D-out removes the over-fit gazetteer, S adds correct scoring, and W reweights the composite to a trust-first formula. Each fix lands behind an env flag where reversibility matters, has a unit test, and is verified against `tests/unit/rag/integration/test_class_x_source_matrix.py` plus a new fixture per class. NLI post-validation (D3) and Postgres function collapse (PATH_C) are deferred to iter-13 with explicit gate conditions.

**Tech Stack:** Python 3.12, FastAPI/uvicorn/gunicorn `--preload`, asyncio + `asyncio.to_thread` + `ThreadPoolExecutor`, pytest + pytest-asyncio, Supabase Postgres + pgvector + supabase-py 2.x sync client, BGE int8 cross-encoder, Gemini 2.5 (flash-lite for cheap rewriter and few-shot router), Caddy 2 SSE, Cloudflare, Docker Compose blue/green on DigitalOcean droplet (2 GB RAM / 1 vCPU), Playwright Python harness.

---

## Architectural decisions baked in (per CLAUDE.md guardrails — DO NOT touch)

`GUNICORN_WORKERS=2`, `--preload`, `FP32_VERIFY_ENABLED` top-3 only, `GUNICORN_TIMEOUT≥180s` (verified prod=240s), rerank semaphore + bounded queue (`RAG_QUEUE_MAX=8/worker`), SSE heartbeat wrapper, Caddy `read_timeout 240s`, schema-drift gate, `kg_users` allowlist gate, teal/amber color rule, BGE int8 reranker (no swap), `_PARTIAL_NO_RETRY_FLOOR=0.5` LITERAL (only the per-class effective floor moves via Class F offset; K3 confidence-gap fires before the floor logic).

**iter-12 explicit operator approvals (chat 2026-05-06, "Looks good!"):**
- Class P (PATH_F) introduces a new `loop.set_default_executor(ThreadPoolExecutor(max_workers=8))` at FastAPI lifespan startup AND a global `asyncio.Semaphore(8)` AND bumps httpx `max_connections` 10→16. None of these are protected CLAUDE.md knobs (gunicorn worker count, timeouts, rerank semaphore — all unchanged).
- Class W changes the headline composite formula (iter-04..iter-11 stay locked at old weights for historical comparability; iter-12+ uses new weights).
- Class D-out removes iter-11's static gazetteer (over-fit; iter-13 K6 will replace with one-shot LLM expansion).
- Q5 replaces the audit-superseded `>= 0.20` static proposal with percentile-based dynamic exemption.
- PATH_F sized to `max_workers=8` (NOT 16) plus global Semaphore(8); rationale in RESEARCH.md Class P.

---

## File Structure

| File | Responsibility | Phase / Task |
|---|---|---|
| `website/app.py` (or `main.py`) | Class P: FastAPI lifespan startup — set default executor with `max_workers=8` | 1 / 1 |
| `website/core/supabase_kg/client.py` | Class P: bump `httpx.Limits(max_connections=16, max_keepalive_connections=8)` | 1 / 1 |
| `website/features/rag_pipeline/retrieval/_async_helpers.py` (NEW) | Class P: `await rpc_call(...)` wrapper + module-level `_RPC_SEM = asyncio.Semaphore(8)` | 1 / 1 |
| `website/features/rag_pipeline/retrieval/hybrid.py` | Class P: wrap RPC sites at L395, 443, 579; Class K3: confidence-gap helper + magnet-gate bypass; Class Q5: percentile-based exemption | 1 / 1 + 3 / 6 + 5 / 9 |
| `website/features/rag_pipeline/retrieval/entity_anchor.py` | Class P: wrap L50, 85; per-request `Semaphore(3)` on `asyncio.gather` fan-out | 1 / 1 |
| `website/features/rag_pipeline/retrieval/anchor_seed.py` | Class P: wrap L22 | 1 / 1 |
| `website/features/rag_pipeline/retrieval/chunk_share.py` | Class P: wrap L91 | 1 / 1 |
| `website/features/rag_pipeline/retrieval/kasten_freq.py` | Class P: wrap L69, 100 | 1 / 1 |
| `website/features/rag_pipeline/retrieval/graph_score.py` | Class P: wrap L73 | 1 / 1 |
| `website/features/rag_pipeline/observability/event_loop_monitor.py` (NEW) | Class P: `event_loop_lag_ms` sentinel coroutine + `/api/health` field | 1 / 2 |
| `website/features/rag_pipeline/observability/kasten_stats.py` (NEW) | Class K4: rolling per-Kasten top-1 frequency cache | 3 / 7 |
| `supabase/migrations/iter12_kg_kasten_metrics.sql` (NEW) | Class K4: `kg_kasten_metrics` table | 3 / 7 |
| `website/features/rag_pipeline/orchestrator.py` | Class K3: confidence-gap in `should_skip_retry`; Class Q3: gate skip-path with `REFUSAL_PHRASE` + `has_valid_citation` check | 3 / 6 + 4 / 8 |
| `website/features/rag_pipeline/query/router.py` | Class Q7: `_VAGUE_DISCOVERY_PATTERN` with 3 guards before LLM-fallback word-count rule; bump `ROUTER_VERSION` v3→v4; Class A1: 8–12 few-shot examples in `_ROUTER_PROMPT` | 6 / 11 + 6 / 12 |
| `website/features/rag_pipeline/query/transformer.py` | Class D-out: remove THEMATIC-gated gazetteer call (L62-69) | 6 / 13 |
| `ops/scripts/score_rag_eval.py` | Class S: extend `_aggregate_gold_metrics` with `accuracy_user_visible` + `over_refusal_rate` + `under_refusal_rate`; per-class breakdown; Class W: load weights from `composite_weights.yaml` | 7 / 14 + 7 / 18 |
| `ops/scripts/eval_iter_03_playwright.py` | Class S: apply E1 to `_qa_summary`; remove q9 hardcode at L1238-1239; rename `latency_ms_server` → `latency_ms_synth_after_ttft`; derive `latency_ms_server_total` | 7 / 15 |
| `docs/rag_eval/_config/composite_weights.yaml` (NEW) | Class W: hash-locked composite formula | 7 / 18 |
| `tests/unit/rag/retrieval/test_async_rpc_wrapper.py` (NEW) | Class P unit tests | 1 / 1 |
| `tests/unit/rag/retrieval/test_event_loop_monitor.py` (NEW) | Class P sentinel coroutine | 1 / 2 |
| `tests/unit/rag/retrieval/test_confidence_gap.py` (NEW) | Class K3 tests | 3 / 6 |
| `tests/unit/rag/observability/test_kasten_stats.py` (NEW) | Class K4 tests | 3 / 7 |
| `tests/unit/rag/test_orchestrator_q3_gate.py` (NEW) | Class Q3 tests | 4 / 8 |
| `tests/unit/rag/retrieval/test_title_overlap_percentile.py` (NEW) | Class Q5 tests | 5 / 9 |
| `tests/unit/rag/query/test_router_q7_regex.py` (NEW) | Class Q7 regex + 3 guards | 6 / 11 |
| `tests/unit/rag/query/test_router_few_shot.py` (NEW) | Class A1 few-shot prompt | 6 / 12 |
| `tests/unit/rag/integration/test_class_x_source_matrix.py` | Cross-class regression net — extend with one fixture per new class | 1–7 (per phase) |
| `tests/unit/ops_scripts/test_score_rag_eval_iter12.py` (NEW) | Class S + W tests | 7 / 14 + 7 / 18 |
| `ops/.env.example` | New env flags for all classes + iter-12 documentation | 8 / 19 |
| `docs/rag_eval/common/knowledge-management/iter-12/scores.md` | Final scorecard (canonical template — NO fix recommendations in scores.md) | 8 / 21 |

---

## Phase 0 — Pre-flight (no code changes)

### Task 1: Mandatory reading

**Files:** none.

- [ ] **Step 1:** Read in this order:
  1. `docs/rag_eval/common/knowledge-management/iter-11/scores.md` — iter-11 outcomes + per-query forensic
  2. `docs/rag_eval/common/knowledge-management/iter-11/verification_results.json` — per-query truth
  3. `docs/rag_eval/common/knowledge-management/iter-11/RESEARCH.md` — Phase 8 / Task 11 rollback decision section is canonical for the PATH_F mandate
  4. `docs/rag_eval/common/knowledge-management/iter-12/RESEARCH.md` (this iter)
  5. `iter-10/scores.md` + `iter-10/RESEARCH.md` — magnet gate / tiebreak baseline
  6. `CLAUDE.md` root — Critical Infra Decision Guardrails

- [ ] **Step 2:** Confirm both rollback flags are still false on droplet:
  ```bash
  ssh droplet "grep -E 'RAG_ANCHOR_(BOOST|SEED_INJECTION)_ENABLED' /opt/zettelkasten/compose/.env"
  ```
  Both must be `false` going into iter-12 Phase 1.

---

## Phase 1 — Class P (PATH_F): asyncio.to_thread + global semaphore + httpx pool

> **Critical:** anchor-boost STAYS OFF for the entirety of Phase 1. Re-enabling is Phase 2 in a separate deploy. iter-11's failure was wiring + flag-flip in one shot.

### Task 1: Wrap every sync `supabase.rpc().execute()` in `asyncio.to_thread`

**Files:**
- New: `website/features/rag_pipeline/retrieval/_async_helpers.py`
- Modify: `website/core/supabase_kg/client.py:109` (httpx limits)
- Modify: `website/app.py` (lifespan startup — `loop.set_default_executor`)
- Modify all 12 RPC call sites listed in RESEARCH.md Class P
- Test: `tests/unit/rag/retrieval/test_async_rpc_wrapper.py` (new)

- [ ] **Step 1: Failing test for the wrapper.**

```python
# tests/unit/rag/retrieval/test_async_rpc_wrapper.py
import asyncio
import time
from unittest.mock import MagicMock
import pytest
from website.features.rag_pipeline.retrieval._async_helpers import rpc_call, _RPC_SEM


@pytest.mark.asyncio
async def test_rpc_call_offloads_blocking_to_thread():
    """rpc_call must run the sync RPC in a thread so the event loop stays free."""
    sync_rpc = MagicMock()
    def _slow_execute():
        time.sleep(0.05)
        return MagicMock(data=[{"node_id": "n1"}])
    sync_rpc.execute = _slow_execute

    loop_blocked_ms = 0.0
    async def _ticker():
        nonlocal loop_blocked_ms
        t0 = time.perf_counter()
        await asyncio.sleep(0.001)
        loop_blocked_ms = (time.perf_counter() - t0) * 1000

    async def _call():
        return await rpc_call(sync_rpc)

    await asyncio.gather(_call(), _ticker())
    # Loop tick fired within 5 ms even though the RPC took 50 ms.
    assert loop_blocked_ms < 10.0


@pytest.mark.asyncio
async def test_global_semaphore_caps_concurrent_rpcs():
    """At most 8 in-flight rpc_call invocations at once."""
    in_flight: set[int] = set()
    max_seen = 0

    def _slow():
        nonlocal max_seen
        in_flight.add(id(_slow))
        max_seen = max(max_seen, len(in_flight))
        time.sleep(0.01)
        in_flight.discard(id(_slow))
        return MagicMock(data=[])

    rpc_objs = [MagicMock(execute=_slow) for _ in range(20)]
    await asyncio.gather(*[rpc_call(r) for r in rpc_objs])
    assert max_seen <= 8


@pytest.mark.asyncio
async def test_rpc_call_propagates_exceptions():
    rpc = MagicMock()
    rpc.execute = MagicMock(side_effect=RuntimeError("supabase down"))
    with pytest.raises(RuntimeError):
        await rpc_call(rpc)
```

- [ ] **Step 2: Implement the wrapper.**

```python
# website/features/rag_pipeline/retrieval/_async_helpers.py
"""iter-12 Class P: PATH_F sync-RPC offload to thread pool with global cap.

The supabase-py 2.x client is synchronous; awaiting `rpc(...).execute()` from
async code blocks the asyncio event loop for the full RPC RTT (150-400 ms).
Under burst-12 + per-entity gather, this saturates CPython 3.12's default
5-thread executor on a 1-vCPU droplet and starves the SSE heartbeat — the
iter-11 OOM root cause.

This wrapper enforces:
  1. `asyncio.to_thread` offload (loop stays free).
  2. Module-level `Semaphore(N)` for global DB-side concurrency cap (across
     all coroutines in this worker, not just per-request).
  3. Optional per-request `Semaphore` parameter for caller-side bounded
     fan-out (used by entity_anchor's per-entity gather).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


_GLOBAL_RPC_SEM_SIZE = int(os.environ.get("RAG_RPC_GLOBAL_SEMAPHORE", "8"))
_RPC_SEM = asyncio.Semaphore(_GLOBAL_RPC_SEM_SIZE)


async def rpc_call(rpc_obj: Any, *, request_sem: asyncio.Semaphore | None = None) -> Any:
    """Execute a sync supabase.rpc(...) call in a thread under the global cap.

    Args:
        rpc_obj: the supabase rpc builder (e.g. `supabase.rpc("foo", {...})`).
        request_sem: optional per-request semaphore (entity_anchor uses
            `Semaphore(3)` to cap fan-out when NER returns many entities).

    Returns the same response object the sync `.execute()` call would have
    returned, including `.data`, raising any exception synchronously.
    """
    async with _RPC_SEM:
        if request_sem is not None:
            async with request_sem:
                return await asyncio.to_thread(rpc_obj.execute)
        return await asyncio.to_thread(rpc_obj.execute)
```

- [ ] **Step 3: Wire FastAPI lifespan to size the default executor.**

```python
# website/app.py — inside the lifespan context manager
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

@asynccontextmanager
async def lifespan(app: FastAPI):
    # iter-12 Class P: explicit executor sizing. Default is min(32, cpu_count+4)
    # = 5 threads on the 1-vCPU droplet, which saturates under burst-12.
    max_workers = int(os.environ.get("RAG_EXECUTOR_MAX_WORKERS", "8"))
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="supa",
    ))
    # ... rest of existing lifespan setup
    yield
    # ... existing teardown
```

- [ ] **Step 4: Bump httpx limits in supabase client.**

```python
# website/core/supabase_kg/client.py:109 (existing httpx config)
import os
import httpx

_max_conn = int(os.environ.get("RAG_HTTPX_MAX_CONNECTIONS", "16"))
_max_keep = int(os.environ.get("RAG_HTTPX_MAX_KEEPALIVE", "8"))
limits = httpx.Limits(max_connections=_max_conn, max_keepalive_connections=_max_keep)
```

- [ ] **Step 5: Replace each of the 12 RPC sites.** For every `supabase.rpc(...).execute()` in the listed files, change to `await rpc_call(supabase.rpc(...))`. Make the enclosing function `async def` if it isn't already; `await` cascades to callers.

**Per-entity gather in `entity_anchor.py`:**

```python
# website/features/rag_pipeline/retrieval/entity_anchor.py
import asyncio
import os
from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

_ENTITY_GATHER_SIZE = int(os.environ.get("RAG_ENTITY_GATHER_SEMAPHORE", "3"))


async def resolve_anchor_nodes(entities, sandbox_id, supabase):
    if not entities or sandbox_id is None:
        return set()
    request_sem = asyncio.Semaphore(_ENTITY_GATHER_SIZE)

    async def _resolve_one(entity):
        if not entity or not entity.strip():
            return set()
        try:
            response = await rpc_call(
                supabase.rpc("rag_resolve_entity_anchors",
                             {"p_sandbox_id": str(sandbox_id), "p_entities": [entity]}),
                request_sem=request_sem,
            )
            return {row["node_id"] for row in (response.data or [])}
        except Exception as exc:
            _log.debug("entity_anchor rpc_error entity=%r exc=%s", entity, type(exc).__name__)
            return set()

    results = await asyncio.gather(*[_resolve_one(e) for e in entities])
    resolved = set().union(*results)
    _log.info("entity_anchor_resolve n_entities=%d resolved=%d",
              len(entities), len(resolved))
    return resolved
```

- [ ] **Step 6: Run all retrieval tests.**

```bash
pytest tests/unit/rag/retrieval/ -q
```

- [ ] **Step 7: Commit (Class P core).**

```bash
git add website/features/rag_pipeline/retrieval/_async_helpers.py \
        website/core/supabase_kg/client.py \
        website/app.py \
        website/features/rag_pipeline/retrieval/{hybrid,entity_anchor,anchor_seed,chunk_share,kasten_freq,graph_score}.py \
        tests/unit/rag/retrieval/test_async_rpc_wrapper.py
git commit -m "feat: async rpc wrapper with global semaphore"
```

### Task 2: Event-loop lag sentinel coroutine + `/api/health` field

**Files:**
- New: `website/features/rag_pipeline/observability/event_loop_monitor.py`
- Modify: `website/api/health.py` (or wherever `/api/health` is defined)
- Test: `tests/unit/rag/retrieval/test_event_loop_monitor.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/unit/rag/retrieval/test_event_loop_monitor.py
import asyncio
import pytest
from website.features.rag_pipeline.observability.event_loop_monitor import (
    EventLoopMonitor,
)


@pytest.mark.asyncio
async def test_lag_below_50ms_under_normal_load():
    monitor = EventLoopMonitor(interval_ms=100)
    await monitor.start()
    await asyncio.sleep(1.0)  # let it tick ~10 times
    snapshot = monitor.snapshot()
    await monitor.stop()
    assert snapshot["p95_ms"] < 50.0


@pytest.mark.asyncio
async def test_detects_blocking_load():
    """A 200ms blocking sleep should make p95 lag spike above 100ms."""
    import time
    monitor = EventLoopMonitor(interval_ms=50)
    await monitor.start()
    time.sleep(0.2)  # block the loop intentionally
    await asyncio.sleep(0.5)
    snapshot = monitor.snapshot()
    await monitor.stop()
    assert snapshot["p95_ms"] > 100.0
```

- [ ] **Step 2: Implement.**

```python
# website/features/rag_pipeline/observability/event_loop_monitor.py
"""iter-12 Class P validation: detect event-loop blocking in production.

Mirrors uvicorn-style lag monitoring. A ticker coroutine sleeps for
`interval_ms` and measures the actual elapsed wall-clock; the difference is
the loop lag. p95 is the canary that gates anchor-boost re-enable in Phase 2.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque


class EventLoopMonitor:
    def __init__(self, interval_ms: int = 100, window: int = 600):
        self._interval = interval_ms / 1000.0
        self._lag_samples: deque[float] = deque(maxlen=window)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            await asyncio.sleep(self._interval)
            elapsed = time.perf_counter() - t0
            lag = max(0.0, (elapsed - self._interval) * 1000)
            self._lag_samples.append(lag)

    def snapshot(self) -> dict[str, float]:
        if not self._lag_samples:
            return {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "n": 0}
        sorted_samples = sorted(self._lag_samples)
        n = len(sorted_samples)
        return {
            "p50_ms": sorted_samples[n // 2],
            "p95_ms": sorted_samples[int(n * 0.95)],
            "max_ms": sorted_samples[-1],
            "n": n,
        }
```

- [ ] **Step 3: Wire into `/api/health` and FastAPI lifespan.**

```python
# website/app.py — extend lifespan
from website.features.rag_pipeline.observability.event_loop_monitor import EventLoopMonitor

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing executor setup
    monitor = EventLoopMonitor(interval_ms=100)
    await monitor.start()
    app.state.event_loop_monitor = monitor
    yield
    await monitor.stop()
```

```python
# website/api/health.py
@router.get("/api/health")
async def health(request: Request):
    monitor = request.app.state.event_loop_monitor
    return {
        "ok": True,
        "event_loop_lag": monitor.snapshot(),
    }
```

- [ ] **Step 4: Run tests + commit.**

```bash
pytest tests/unit/rag/retrieval/test_event_loop_monitor.py -q
git add website/features/rag_pipeline/observability/event_loop_monitor.py \
        website/api/health.py website/app.py \
        tests/unit/rag/retrieval/test_event_loop_monitor.py
git commit -m "feat: event loop lag sentinel for path f"
```

### Task 3: Phase 1 deploy + staging burst-12 validation

- [ ] **Step 1:** Push Phase 1 commits.
  ```bash
  git push origin master
  ```

- [ ] **Step 2:** After GHA `deploy-droplet.yml` lands green, run the burst-12 staging probe BEFORE re-enabling anchor-boost:
  ```bash
  python ops/scripts/burst_pressure_probe.py --concurrency 12 --duration 60 --target https://zettelkasten.in
  ```
  **Pass criteria:** zero 502s; any 503s carry `retry_after=5` (rerank semaphore is the only allowed backpressure); `/api/health` `event_loop_lag.p95_ms < 50`.

- [ ] **Step 3:** If pass, advance to Phase 2. If fail, root-cause from logs and pull droplet `dmesg` / `free -h` / `gh workflow run read_recent_logs.yml`. **Do NOT advance Phase 2 until burst-12 is clean.**

---

## Phase 2 — Re-enable anchor-boost in a SEPARATE deploy

### Task 4: Pre-deploy validation

- [ ] **Step 1:** Confirm Phase 1 burst-12 pass criteria documented in chat. Operator chat-confirms before flipping flags. **No exceptions.**

### Task 5: Flip both anchor flags in droplet env

- [ ] **Step 1:** Update droplet `/opt/zettelkasten/compose/.env`:
  ```
  RAG_ANCHOR_BOOST_ENABLED=true
  RAG_ANCHOR_SEED_INJECTION_ENABLED=true
  ```

- [ ] **Step 2:** Restart the active color (blue/green hot-flip via `/opt/zettelkasten/deploy/deploy.sh` no-arg restart).

- [ ] **Step 3:** Re-run burst-12 probe. **Pass criteria same as Phase 1.** If fail, set both flags back to `false`, root-cause, do NOT proceed.

- [ ] **Step 4:** Run a single-query smoke against q10 ("Steve Jobs and Naval Ravikant compare meaningful work") — gold should now retrieve.

- [ ] **Step 5:** Commit env example update if changed.
  ```bash
  git add ops/.env.example
  git commit -m "ops: re enable anchor boost after path f"
  ```

---

## Phase 3 — Class K3 + K4: dynamic primitives

### Task 6: K3 confidence-gap helper + magnet-gate bypass

**Files:**
- Modify: `website/features/rag_pipeline/retrieval/hybrid.py` (new `_top1_top2_gap` helper; bypass in `_apply_score_rank_demote`)
- Modify: `website/features/rag_pipeline/orchestrator.py` (bypass in `should_skip_retry`)
- Test: `tests/unit/rag/retrieval/test_confidence_gap.py` (new)

- [ ] **Step 1: Failing tests.**

```python
# tests/unit/rag/retrieval/test_confidence_gap.py
from website.features.rag_pipeline.retrieval.hybrid import (
    _top1_top2_gap, _apply_score_rank_demote,
)
from website.features.rag_pipeline.types import QueryClass


def _cand(node_id, base_rrf, final_rrf):
    from website.features.rag_pipeline.types import RetrievalCandidate
    c = RetrievalCandidate(node_id=node_id, rrf_score=final_rrf)
    c.metadata = {"_base_rrf_score": base_rrf}
    return c


def test_clear_winner_skips_gate():
    """When top-1 / top-2 gap >= 1.5, the magnet gate is bypassed entirely."""
    cands = [
        _cand("clear-winner", 0.10, 0.90),
        _cand("a", 0.55, 0.50),
        _cand("b", 0.50, 0.45),
        _cand("c", 0.45, 0.40),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # gap = 0.90 / 0.50 = 1.8 >= 1.5 → bypass
    assert cands[0].rrf_score == 0.90  # unchanged


def test_close_competition_gate_fires():
    """Gap < 1.5 → normal magnet-gate logic."""
    cands = [
        _cand("magnet", 0.10, 0.65),
        _cand("a", 0.55, 0.60),
        _cand("b", 0.50, 0.55),
        _cand("c", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # gap = 0.65 / 0.60 = 1.083 < 1.5 → demote fires; magnet drops
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "magnet"


def test_gap_undefined_for_single_candidate():
    """Single-candidate pool → gap is None; gate skipped trivially (len < 4 anyway)."""
    cands = [_cand("only", 0.5, 0.5)]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    assert cands[0].rrf_score == 0.5
```

- [ ] **Step 2: Implement helper.**

```python
# website/features/rag_pipeline/retrieval/hybrid.py
import os

_SCORE_RANK_GAP_BYPASS = float(os.environ.get("RAG_SCORE_RANK_GAP_BYPASS", "1.5"))


def _top1_top2_gap(candidates: list) -> float | None:
    """iter-12 Class K3: relative confidence-gap. Returns None when undefined."""
    if not candidates or len(candidates) < 2:
        return None
    sorted_cands = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
    top1 = sorted_cands[0].rrf_score
    top2 = max(sorted_cands[1].rrf_score, 1e-9)
    return top1 / top2
```

- [ ] **Step 3: Add bypass to `_apply_score_rank_demote`.**

```python
def _apply_score_rank_demote(candidates, *, query_class, query_text="", anchor_nodes=None):
    if query_class not in _SCORE_RANK_GATED_CLASSES:
        return
    if not candidates or len(candidates) < 4:
        return
    # iter-12 Class K3: clear-winner bypass.
    gap = _top1_top2_gap(candidates)
    if gap is not None and gap >= _SCORE_RANK_GAP_BYPASS:
        _log.info("score_rank_gate bypass=clear_winner gap=%.3f", gap)
        return
    # ... existing logic
```

- [ ] **Step 4: Add bypass to `should_skip_retry` in orchestrator.**

```python
# website/features/rag_pipeline/orchestrator.py
import os

_RETRY_GAP_BYPASS = float(os.environ.get("RAG_RETRY_GAP_BYPASS", "1.5"))


def should_skip_retry(query_class, used_candidates, ...):
    # iter-12 Class K3: skip retry when there's a clear winner.
    gap = _top1_top2_gap(used_candidates)
    if gap is not None and gap >= _RETRY_GAP_BYPASS:
        return ("skip_clear_winner",)
    # ... existing partial / unsupported_with_gold_skip logic
```

- [ ] **Step 5: Tests + commit.**

```bash
pytest tests/unit/rag/retrieval/test_confidence_gap.py tests/unit/rag/test_orchestrator.py -q
git add website/features/rag_pipeline/retrieval/hybrid.py \
        website/features/rag_pipeline/orchestrator.py \
        tests/unit/rag/retrieval/test_confidence_gap.py
git commit -m "feat: confidence gap bypass for magnet and retry gates"
```

### Task 7: K4 per-Kasten bootstrap of magnet-spotter

**Files:**
- New: `website/features/rag_pipeline/observability/kasten_stats.py`
- New: `supabase/migrations/iter12_kg_kasten_metrics.sql`
- Modify: `ops/scripts/score_rag_eval.py` (magnet-spotter section)
- Test: `tests/unit/rag/observability/test_kasten_stats.py` (new)

- [ ] **Step 1: Migration.**

```sql
-- supabase/migrations/iter12_kg_kasten_metrics.sql
create table if not exists kg_kasten_metrics (
  id bigserial primary key,
  sandbox_id uuid not null,
  top1_node_id text not null,
  ts timestamptz not null default now()
);

create index if not exists kg_kasten_metrics_sandbox_ts_idx
  on kg_kasten_metrics(sandbox_id, ts desc);
```

- [ ] **Step 2: Failing test.**

```python
# tests/unit/rag/observability/test_kasten_stats.py
from website.features.rag_pipeline.observability.kasten_stats import KastenStats


def test_bootstrap_returns_static_fallback_below_n_min():
    stats = KastenStats(window=50, n_min=20)
    for _ in range(10):
        stats.record("kasten-1", "node-a")
    # n=10 < n_min=20 → static 0.25 fallback
    assert stats.bootstrap_threshold("kasten-1") == 0.25


def test_bootstrap_computes_mean_plus_2_stdev_above_n_min():
    stats = KastenStats(window=50, n_min=20)
    # 30 records, all top-1 node-a → freq 1.0 for node-a, 0.0 elsewhere
    for _ in range(30):
        stats.record("kasten-1", "node-a")
    threshold = stats.bootstrap_threshold("kasten-1")
    # All weight on one node → stdev=0 → threshold = mean = 1.0/k_unique = 1.0
    assert 0.5 < threshold <= 1.0


def test_window_evicts_old_entries():
    stats = KastenStats(window=5, n_min=2)
    for _ in range(10):
        stats.record("k", "n1")
    stats.record("k", "n2")
    stats.record("k", "n3")
    # window=5 → only last 5 entries: 3xn1, n2, n3
    threshold = stats.bootstrap_threshold("k")
    assert 0.3 <= threshold <= 1.0
```

- [ ] **Step 3: Implement.**

```python
# website/features/rag_pipeline/observability/kasten_stats.py
"""iter-12 Class K4: per-Kasten rolling magnet-spotter threshold.

Replaces the static 25% top-1-share threshold (iter-09 magnet-spotter) with
a per-Kasten bootstrap. Threshold = mean + 2 * stdev of per-node top-1
frequencies over the last N=50 queries. Below n_min=20 queries, falls back
to the static 0.25 baseline.

Persisted to `kg_kasten_metrics` Supabase table for cross-restart durability;
in-memory cache for hot-path access.
"""
from __future__ import annotations

import statistics
from collections import Counter, deque


class KastenStats:
    def __init__(self, window: int = 50, n_min: int = 20):
        self._window = window
        self._n_min = n_min
        self._buffers: dict[str, deque[str]] = {}

    def record(self, sandbox_id: str, top1_node_id: str) -> None:
        if sandbox_id not in self._buffers:
            self._buffers[sandbox_id] = deque(maxlen=self._window)
        self._buffers[sandbox_id].append(top1_node_id)

    def bootstrap_threshold(self, sandbox_id: str) -> float:
        buf = self._buffers.get(sandbox_id)
        if not buf or len(buf) < self._n_min:
            return 0.25  # static fallback
        counts = Counter(buf)
        n = len(buf)
        freqs = [c / n for c in counts.values()]
        if len(freqs) < 2:
            return min(1.0, max(freqs))  # single-node Kasten edge case
        mean = statistics.mean(freqs)
        stdev = statistics.pstdev(freqs)
        return min(1.0, mean + 2 * stdev)
```

- [ ] **Step 4: Wire into `ops/scripts/score_rag_eval.py` magnet-spotter section.** Read top-1 frequencies from the eval's per-query rows; pass through `KastenStats` for the iter-12 sandbox-id. Render `magnet-spotter (>= dynamic threshold X.XX)` instead of the static `>= 25%`.

- [ ] **Step 5: Tests + commit.**

```bash
pytest tests/unit/rag/observability/ -q
git add website/features/rag_pipeline/observability/kasten_stats.py \
        supabase/migrations/iter12_kg_kasten_metrics.sql \
        ops/scripts/score_rag_eval.py \
        tests/unit/rag/observability/test_kasten_stats.py
git commit -m "feat: per kasten bootstrap of magnet spotter"
```

---

## Phase 4 — Class Q3: gate skip-path on REFUSAL_PHRASE / has_valid_citation

### Task 8: Honest "best draft" tag instead of silent mask

**Files:**
- Modify: `website/features/rag_pipeline/orchestrator.py:977-985`
- Test: `tests/unit/rag/test_orchestrator_q3_gate.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/unit/rag/test_orchestrator_q3_gate.py
from unittest.mock import MagicMock
import pytest
from website.features.rag_pipeline.orchestrator import _finalize_answer, REFUSAL_PHRASE


def test_unsupported_with_gold_skip_does_not_mask_refusal_phrase():
    """iter-12 Q3: when synth produced a no-cite draft and citation-validation
    substituted REFUSAL_PHRASE, the gold-skip path MUST NOT wrap it in
    'reflects sources' tag — it must fall through to unsupported_no_retry
    so the user sees an honest 'best draft' warning."""
    # Build a generation where content has no valid [id=...] tag.
    generation = MagicMock(content="Short answer with no citation.")
    valid_ids = {"yt-effective-public-speakin"}
    pre_validation = [MagicMock(node_id="yt-effective-public-speakin", rerank_score=0.8)]

    result = _finalize_answer(
        generation=generation,
        valid_ids=valid_ids,
        pre_validation_candidates=pre_validation,
        # ... other args
    )
    assert result["verdict"] == "unsupported_no_retry"
    assert REFUSAL_PHRASE in result["answer_text"]
    assert "Answer reflects retrieved sources" not in result["answer_text"]


def test_unsupported_with_gold_skip_with_valid_citation_keeps_tag():
    """Valid path: synth's draft DOES have a valid [id=...] tag; the gold-skip
    'reflects sources' tag is correct."""
    generation = MagicMock(content='answer [id="yt-effective-public-speakin"] more')
    valid_ids = {"yt-effective-public-speakin"}
    pre_validation = [MagicMock(node_id="yt-effective-public-speakin", rerank_score=0.8)]

    result = _finalize_answer(
        generation=generation,
        valid_ids=valid_ids,
        pre_validation_candidates=pre_validation,
        # ... other args
    )
    assert result["verdict"] == "unsupported_with_gold_skip"
    assert "Answer reflects retrieved sources" in result["answer_text"]
```

- [ ] **Step 2: Implement.**

At `orchestrator.py:977-985`, change the gold-skip branch to:

```python
elif skip_reason == "unsupported_with_gold_skip":
    raw_content = (generation.content or "").strip()
    has_real_draft = (
        answer_text != REFUSAL_PHRASE
        and bool(raw_content)
        and has_valid_citation(raw_content, valid_ids)
    )
    if has_real_draft:
        verdict = "unsupported_with_gold_skip"
        if "<summary>How sure am I?</summary>" not in (answer_text or ""):
            answer_text = (answer_text or "") + _GOLD_RETRIEVED_DETAILS_TAG
        replaced_text = answer_text
    else:
        # iter-12 Q3: refusal-substituted drafts get the honest "best draft"
        # tag, not the misleading "reflects sources" tag.
        verdict = "unsupported_no_retry"
        if "<summary>How sure am I?</summary>" not in (answer_text or ""):
            answer_text = (answer_text or "") + _LOW_CONFIDENCE_TAG
        replaced_text = answer_text
```

- [ ] **Step 3: Tests + commit.**

```bash
pytest tests/unit/rag/test_orchestrator_q3_gate.py tests/unit/rag/test_orchestrator.py -q
git add website/features/rag_pipeline/orchestrator.py \
        tests/unit/rag/test_orchestrator_q3_gate.py
git commit -m "fix: q3 over refusal masks refusal as reflects sources"
```

---

## Phase 5 — Class Q5: percentile-based magnet exemption

### Task 9: Replace iter-11 `> 0.0` with percentile-based exemption

**Files:**
- Modify: `website/features/rag_pipeline/retrieval/hybrid.py:_apply_score_rank_demote` (~L195-265) AND `_tiebreak_key` (~L165-195)
- Test: `tests/unit/rag/retrieval/test_title_overlap_percentile.py` (new)
- Test: extend `tests/unit/rag/integration/test_class_x_source_matrix.py`

- [ ] **Step 1: Failing tests.**

```python
# tests/unit/rag/retrieval/test_title_overlap_percentile.py
from website.features.rag_pipeline.retrieval.hybrid import _apply_score_rank_demote
from website.features.rag_pipeline.types import QueryClass


def _cand(node_id, base_rrf, final_rrf, title_boost=0.0):
    from website.features.rag_pipeline.types import RetrievalCandidate
    c = RetrievalCandidate(node_id=node_id, rrf_score=final_rrf)
    c.metadata = {"_base_rrf_score": base_rrf, "_title_overlap_boost": title_boost}
    return c


def test_incidental_token_overlap_does_not_exempt_magnet():
    """iter-12 Q5: a magnet candidate with an incidental 0.05 boost (single
    token coincidence) MUST NOT be exempted from the magnet gate, because
    the 75th percentile of boosts in this pool is well above 0.05."""
    cands = [
        _cand("magnet", 0.10, 0.65, title_boost=0.05),
        _cand("a", 0.55, 0.60, title_boost=0.30),
        _cand("b", 0.50, 0.55, title_boost=0.25),
        _cand("c", 0.45, 0.50, title_boost=0.22),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # P75 over [0.05, 0.30, 0.25, 0.22] = 0.275. Magnet at 0.05 < 0.275 → not exempt → demote fires.
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "magnet"


def test_earned_title_overlap_exempts():
    """A 0.40 verbatim-title boost in a pool of low-boost siblings DOES exempt."""
    cands = [
        _cand("named", 0.10, 0.65, title_boost=0.40),
        _cand("a", 0.55, 0.60, title_boost=0.05),
        _cand("b", 0.50, 0.55, title_boost=0.0),
        _cand("c", 0.45, 0.50, title_boost=0.05),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # P75 ≈ 0.05; named at 0.40 >> P75 → exempted.
    assert cands[0].rrf_score == 0.65


def test_uniform_boost_no_exemption():
    """All boosts equal → percentile-based gate fires uniformly (or not)."""
    cands = [
        _cand("a", 0.55, 0.60, title_boost=0.20),
        _cand("b", 0.50, 0.55, title_boost=0.20),
        _cand("c", 0.45, 0.50, title_boost=0.20),
        _cand("d", 0.40, 0.45, title_boost=0.20),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    # P75 = 0.20; everyone is at the percentile floor. Floor fallback (0.10)
    # is exceeded for all → all exempt — no demote anywhere. Acceptable.


def test_zero_boosts_never_exempt():
    """All boosts 0.0 → P75 = 0.0; floor fallback 0.10 binds → no exemption."""
    cands = [
        _cand("a", 0.10, 0.65),
        _cand("b", 0.55, 0.60),
        _cand("c", 0.50, 0.55),
        _cand("d", 0.45, 0.50),
    ]
    _apply_score_rank_demote(cands, query_class=QueryClass.THEMATIC, query_text="topic")
    sorted_by_score = sorted(cands, key=lambda c: c.rrf_score, reverse=True)
    assert sorted_by_score[0].node_id != "a"
```

- [ ] **Step 2: Run; confirm failure.**

- [ ] **Step 3: Implement percentile exemption in `_apply_score_rank_demote`.**

```python
# website/features/rag_pipeline/retrieval/hybrid.py
import os
import statistics

_TITLE_OVERLAP_PERCENTILE = int(os.environ.get("RAG_TITLE_OVERLAP_PERCENTILE", "75"))
_TITLE_OVERLAP_FLOOR_FALLBACK = float(
    os.environ.get("RAG_TITLE_OVERLAP_FLOOR_FALLBACK", "0.10")
)


def _percentile(values: list[float], p: int) -> float:
    """Linear-interpolation percentile. Empty -> 0.0."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return sorted_v[0]
    rank = (p / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac


def _apply_score_rank_demote(candidates, *, query_class, query_text="", anchor_nodes=None):
    # ... existing class/length checks + K3 gap bypass

    anchored = anchor_nodes or set()
    # iter-12 Q5: percentile-based title-overlap exemption.
    boosts = [float(c.metadata.get("_title_overlap_boost", 0.0)) for c in candidates]
    boost_p_threshold = max(
        _percentile(boosts, _TITLE_OVERLAP_PERCENTILE),
        _TITLE_OVERLAP_FLOOR_FALLBACK,
    )

    # ... existing percentile/rank-pct math

    for c in candidates:
        is_anchored = c.node_id in anchored
        boost = float(c.metadata.get("_title_overlap_boost", 0.0))
        # iter-12 Q5: exemption requires earned signal (top-quartile boost).
        has_earned_title = boost >= boost_p_threshold
        if is_anchored or has_earned_title:
            continue
        # ... existing demote logic
```

- [ ] **Step 4: Same percentile primitive applies to `_tiebreak_key`'s name-overlap inversion-bypass (iter-11 Class B).** Replace `title_overlap_boost > 0.0` with the same percentile check at the call site in `_dedup_and_fuse`.

- [ ] **Step 5: Add fixture to cross-class regression net.**

```python
# tests/unit/rag/integration/test_class_x_source_matrix.py — extend
"thematic_unearned_title_magnet_q5": {
    "class": "thematic",
    "rows": [
        {**_row("gh-zk-org-zk", "github", 0.10), "title_boost": 0.05},
        {**_row("nl-the-pragmatic-engineer-t", "newsletter", 0.55), "title_boost": 0.30},
        {**_row("yt-programming-workflow-is", "youtube", 0.50), "title_boost": 0.25},
        {**_row("yt-matt-walker-sleep-depriv", "youtube", 0.45), "title_boost": 0.22},
    ],
    "query_variants": ["how should a knowledge worker structure a day"],
    "expected_primary_NOT": "gh-zk-org-zk",  # Q5 ensures magnet doesn't win
},
```

- [ ] **Step 6: Run all retrieval + integration tests.**

```bash
pytest tests/unit/rag/retrieval/ tests/unit/rag/integration/ -q
```

- [ ] **Step 7: Commit.**

```bash
git add website/features/rag_pipeline/retrieval/hybrid.py \
        tests/unit/rag/retrieval/test_title_overlap_percentile.py \
        tests/unit/rag/integration/test_class_x_source_matrix.py
git commit -m "feat: percentile based title overlap exemption"
```

---

## Phase 6 — Class Q7 + A1: router regex with guards + few-shot prompt + Class D-out

### Task 11: Q7 regex with 3 guards before LLM-fallback word-count rule

**Files:**
- Modify: `website/features/rag_pipeline/query/router.py:151-171` (insert override) + `:21` (bump `ROUTER_VERSION`)
- Test: `tests/unit/rag/query/test_router_q7_regex.py` (new)

- [ ] **Step 1: Failing tests.**

```python
# tests/unit/rag/query/test_router_q7_regex.py
from website.features.rag_pipeline.query.router import (
    apply_class_overrides, ROUTER_VERSION,
)
from website.features.rag_pipeline.types import QueryClass


def test_anything_about_x_routes_to_vague():
    cls, reason = apply_class_overrides(
        "Anything about commencement?", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.VAGUE
    assert "vague_discovery_shape" in reason


def test_anything_NOT_about_x_does_not_match_negation_guard():
    """Negation guard must prevent regex firing."""
    cls, _ = apply_class_overrides(
        "Anything NOT about climate", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP


def test_long_anything_about_x_falls_through_to_multi_hop():
    """Length guard preserves iter-09 long-query MULTI_HOP upgrade."""
    long_query = (
        "Anything about how Steve Jobs framed mortality across his "
        "speeches and interviews and writings and the rest of his life"
    )
    cls, _ = apply_class_overrides(long_query, QueryClass.LOOKUP, person_entities=None)
    assert cls != QueryClass.VAGUE  # falls through; word-count rule fires later


def test_anything_about_proper_noun_falls_through():
    """Proper-noun guard preserves precision for named-entity LOOKUP."""
    cls, _ = apply_class_overrides(
        "Anything about Stanford 2005", QueryClass.LOOKUP, person_entities=None,
    )
    assert cls == QueryClass.LOOKUP  # year token guards


def test_router_version_bumped_to_v4():
    assert ROUTER_VERSION == "v4"


def test_real_lookup_unaffected():
    cls, _ = apply_class_overrides(
        "What did Naval say about happiness?", QueryClass.LOOKUP, person_entities=["Naval"],
    )
    assert cls == QueryClass.LOOKUP
```

- [ ] **Step 2: Implement.**

```python
# website/features/rag_pipeline/query/router.py
import re

ROUTER_VERSION = "v4"  # iter-12: invalidate iter-11 cached responses

_VAGUE_DISCOVERY_PATTERN = re.compile(
    r"^\s*(anything|something|stuff|things|info|notes?)"
    r"\s+(about|on|regarding|re|around|related to)\b",
    re.IGNORECASE,
)
_NEGATION_PATTERN = re.compile(
    r"\b(not|isn'?t|except|excluding|without)\b", re.IGNORECASE
)
_PROPER_NOUN_OR_YEAR_PATTERN = re.compile(
    r"(\b(?:19|20)\d{2}\b)|(\B[A-Z][a-z]+\b)",  # year OR Capitalised non-leading
)


def _matches_vague_discovery(query: str) -> bool:
    """iter-12 Q7: 3-guarded regex for 'Anything about X?' shape."""
    if not _VAGUE_DISCOVERY_PATTERN.search(query):
        return False
    if _NEGATION_PATTERN.search(query):
        return False  # negation guard
    if len(query.split()) >= 25:
        return False  # length guard preserves long-query MULTI_HOP
    if _PROPER_NOUN_OR_YEAR_PATTERN.search(query):
        return False  # proper-noun guard preserves named-entity LOOKUP
    return True


def apply_class_overrides(query, llm_class, person_entities):
    # ... existing rules (multi-person, synthesis, compare, enumerate,
    #     double-?, relate, summary-of) — keep these BEFORE Q7 regex.

    # iter-12 Q7: vague-discovery override BEFORE word-count fallback.
    if _matches_vague_discovery(query):
        return (QueryClass.VAGUE, "override_vague_discovery_shape")

    # ... existing word-count rule (>= 25 words → MULTI_HOP)
```

- [ ] **Step 3: Tests + commit.**

```bash
pytest tests/unit/rag/query/ -q
git add website/features/rag_pipeline/query/router.py \
        tests/unit/rag/query/test_router_q7_regex.py
git commit -m "feat: vague discovery override with 3 guards"
```

### Task 12: A1 few-shot examples in `_ROUTER_PROMPT`

**Files:**
- Modify: `website/features/rag_pipeline/query/router.py:16` (`_ROUTER_PROMPT`)
- Test: `tests/unit/rag/query/test_router_few_shot.py` (new)

- [ ] **Step 1: Failing test.**

```python
# tests/unit/rag/query/test_router_few_shot.py
from website.features.rag_pipeline.query.router import _ROUTER_PROMPT


def test_router_prompt_has_balanced_few_shot_examples():
    """A1: 8-12 balanced examples (2 per class) in the prompt."""
    # Each class label appears at least twice in the example block.
    for label in ("lookup", "vague", "thematic", "multi_hop", "step_back"):
        assert _ROUTER_PROMPT.count(f"=> {label}") >= 2, f"missing examples for {label}"
    # Total examples between 8 and 12.
    n_examples = _ROUTER_PROMPT.count("=>")
    assert 8 <= n_examples <= 12
```

- [ ] **Step 2: Update prompt with 10 examples (2 per class).**

```python
# website/features/rag_pipeline/query/router.py:16
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

Query: {query}
Class:"""
```

- [ ] **Step 3: Run tests + commit.**

```bash
pytest tests/unit/rag/query/ -q
git add website/features/rag_pipeline/query/router.py \
        tests/unit/rag/query/test_router_few_shot.py
git commit -m "feat: balanced few shot examples in router prompt"
```

### Task 13: Class D-out — remove static gazetteer call from THEMATIC branch

**Files:**
- Modify: `website/features/rag_pipeline/query/transformer.py:62-69`
- Modify: `tests/unit/rag/query/test_short_query_expansion.py` (iter-11 test — invert assertion)

- [ ] **Step 1: Update transformer.**

```python
# website/features/rag_pipeline/query/transformer.py
elif cls is QueryClass.THEMATIC:
    _thematic_n = int(os.environ.get("RAG_THEMATIC_MULTIQUERY_N", "3"))
    base_variants = await self._multi_query(query, n=_thematic_n, entities=ents)
    # iter-12 Class D-out: short-THEMATIC queries no longer invoke the
    # static gazetteer + HyDE here. They route to VAGUE via Q7 regex
    # override (router.py) and pick up the VAGUE-branch expansion
    # naturally. iter-13 K6 will replace the VAGUE-branch gazetteer
    # with one-shot LLM expansion.
    variants = [query, *base_variants]
```

- [ ] **Step 2: Invert iter-11 short-thematic test.**

```python
# tests/unit/rag/query/test_short_query_expansion.py — modify
@pytest.mark.asyncio
async def test_short_thematic_no_longer_invokes_gazetteer():
    """iter-12 Class D-out: short-THEMATIC routes to VAGUE via router; the
    THEMATIC branch itself NO longer calls expand_vague."""
    pool = AsyncMock()
    async def _fake_gen(prompt, **kw):
        return "alt: paraphrase 1\nalt: paraphrase 2"
    pool.generate_content = _fake_gen
    qt = QueryTransformer(pool=pool)
    variants = await qt.transform("Anything about commencement?", QueryClass.THEMATIC)
    joined = " ".join(variants).lower()
    # Gazetteer keys MUST NOT appear in THEMATIC variants.
    assert "graduation" not in joined and "stanford 2005" not in joined
```

- [ ] **Step 3: Run + commit.**

```bash
pytest tests/unit/rag/query/ -q
git add website/features/rag_pipeline/query/transformer.py \
        tests/unit/rag/query/test_short_query_expansion.py
git commit -m "refactor: remove static gazetteer from thematic branch"
```

---

## Phase 7 — Class S + W: scoring + composite redesign

### Task 14: `accuracy_user_visible` + `over_refusal_rate` + `under_refusal_rate` + per-class breakdown

**Files:**
- Modify: `ops/scripts/score_rag_eval.py` (`_aggregate_gold_metrics`, `_holistic_metrics`, `_render_scores_md`)
- Test: `tests/unit/ops_scripts/test_score_rag_eval_iter12.py` (new)

- [ ] **Step 1: Failing tests.**

```python
# tests/unit/ops_scripts/test_score_rag_eval_iter12.py
def test_accuracy_user_visible_excludes_refused():
    from ops.scripts.score_rag_eval import _aggregate_gold_metrics
    rows = [
        {"gold_at_1": True,  "refused": False, "over_refusal": False, "expected_empty": False},
        {"gold_at_1": True,  "refused": True,  "over_refusal": True,  "expected_empty": False},  # q3-shape
        {"gold_at_1": False, "refused": False, "over_refusal": False, "expected_empty": True},   # q9-shape
        {"gold_at_1": False, "refused": False, "over_refusal": False, "expected_empty": False},
    ]
    out = _aggregate_gold_metrics(rows)
    # n_scored = 3 (excluding q9). Pass = 1 (only first row). 1/3 ≈ 0.3333.
    assert out["accuracy_user_visible"] == round(1/3, 4)
    assert out["over_refusal_rate"] == round(1/3, 4)


def test_per_class_breakdown_emits_each_class():
    from ops.scripts.score_rag_eval import _per_class_breakdown
    rows = [
        {"gold_at_1": True,  "query_class": "lookup", "refused": False, "over_refusal": False, "expected_empty": False},
        {"gold_at_1": False, "query_class": "lookup", "refused": True,  "over_refusal": True,  "expected_empty": False},
        {"gold_at_1": True,  "query_class": "thematic", "refused": False, "over_refusal": False, "expected_empty": False},
    ]
    out = _per_class_breakdown(rows)
    assert "lookup" in out
    assert "thematic" in out
    assert out["lookup"]["accuracy_user_visible"] == 0.5
    assert out["thematic"]["accuracy_user_visible"] == 1.0
```

- [ ] **Step 2: Implement.**

```python
# ops/scripts/score_rag_eval.py
def _aggregate_gold_metrics(rows: list[dict]) -> dict[str, float]:
    """iter-12 Class S: trust-first metric set."""
    scored = [r for r in rows if not r.get("expected_empty")]
    n_scored = max(len(scored), 1)
    n_na = sum(1 for r in rows if r.get("expected_empty"))

    user_visible_pass = sum(
        1 for r in scored
        if r.get("gold_at_1") is True
        and not r.get("over_refusal")
        and not r.get("refused")
    )
    over_refusal = sum(1 for r in scored if r.get("over_refusal"))
    answered = [r for r in scored if not r.get("refused")]
    under_refusal = sum(
        1 for r in answered if (r.get("faithfulness") or 1.0) < 0.5
    )
    n_answered = max(len(answered), 1)

    return {
        "accuracy_user_visible": round(user_visible_pass / n_scored, 4),
        "over_refusal_rate": round(over_refusal / n_scored, 4),
        "under_refusal_rate": round(under_refusal / n_answered, 4),
        "gold_at_1_unconditional": round(  # legacy diagnostic
            sum(1 for r in scored if r.get("gold_at_1") is True) / n_scored, 4
        ),
        "gold_at_1_not_applicable": n_na,
    }


def _per_class_breakdown(rows: list[dict]) -> dict[str, dict[str, float]]:
    """iter-12 Class S: per-QueryClass metric breakdown."""
    by_class: dict[str, list[dict]] = {}
    for r in rows:
        cls = r.get("query_class", "unknown")
        by_class.setdefault(cls, []).append(r)
    return {cls: _aggregate_gold_metrics(rs) for cls, rs in by_class.items()}
```

- [ ] **Step 3: Update `_render_scores_md` template** to surface per-class breakdown + the new metrics. Keep gold@K (1/3/8) as a recall-curve diagnostic. Render `cites: 0 (refused)` for refused rows.

- [ ] **Step 4: Tests + commit.**

```bash
pytest tests/unit/ops_scripts/ -q
git add ops/scripts/score_rag_eval.py \
        tests/unit/ops_scripts/test_score_rag_eval_iter12.py
git commit -m "feat: accuracy user visible plus per class breakdown"
```

### Task 15: E1 in `_qa_summary` + remove q9 hardcode + rename `latency_ms_server`

**Files:**
- Modify: `ops/scripts/eval_iter_03_playwright.py` (~L1230, L1238-1239, L1444-1467)

- [ ] **Step 1:** Apply E1 to `_qa_summary`:

```python
# ops/scripts/eval_iter_03_playwright.py:1444-1467
def _qa_summary(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r.get("expected_empty")]
    user_visible_passes = sum(
        1 for r in scored
        if r.get("gold_at_1") is True
        and not r.get("over_refusal")
        and not r.get("refused")
    )
    n = max(len(scored), 1)
    return {
        "total": len(rows),
        "n_scored": len(scored),
        "n_not_applicable": len(rows) - len(scored),
        "accuracy_user_visible": round(user_visible_passes / n, 4),
        "synthesizer_over_refusals": sum(1 for r in rows if r.get("over_refusal")),
        # ... rest unchanged
    }
```

- [ ] **Step 2:** Remove q9 hardcode at L1238-1239. Replace with:

```python
# ops/scripts/eval_iter_03_playwright.py:1238 area
expected = q.get("expected") or []
result["expected_empty"] = not bool(expected)
# Don't set gold_at_1 mechanically for refusal-expected rows.
if result["expected_empty"]:
    result["gold_at_1"] = None  # explicit N/A; consumers must handle
else:
    result["gold_at_1"] = bool(primary in expected)
```

- [ ] **Step 3:** Rename `latency_ms_server` field. Add `latency_ms_synth_after_ttft` (the same value, new name) AND `latency_ms_server_total` (derived from `p_user_complete_ms`):

```python
# ops/scripts/eval_iter_03_playwright.py:1230
result["latency_ms_synth_after_ttft"] = turn.get("latency_ms")  # iter-12: clarify
result["latency_ms_server_total"] = result.get("p_user_complete_ms")  # iter-12: honest TTLT
# Keep legacy name for one iter; readers will be migrated by iter-13.
result["latency_ms_server"] = result["latency_ms_synth_after_ttft"]
```

- [ ] **Step 4: Update scorer readers** to prefer `latency_ms_server_total`. Add a deprecation log when the legacy field is read.

- [ ] **Step 5: Tests + commit.**

```bash
pytest tests/unit/ops_scripts/ -q
git add ops/scripts/eval_iter_03_playwright.py
git commit -m "fix: e1 in qa summary plus rename latency ms server"
```

### Task 18: Composite reweight (Class W)

**Files:**
- New: `docs/rag_eval/_config/composite_weights.yaml`
- Modify: `ops/scripts/score_rag_eval.py` (composite computation)

- [ ] **Step 1: New composite-weights file.**

```yaml
# docs/rag_eval/_config/composite_weights.yaml
# iter-12+ trust-first composite weights. Hash-locked.
# Pre-iter-12 evals stay locked at the legacy weights:
#   {chunking: 0.10, retrieval: 0.25, reranking: 0.20, synthesis: 0.45}
schema_version: 1
applies_from_iter: 12
weights:
  trust:       0.40   # max(0, faithfulness - under_refusal_rate)
  accuracy:    0.30   # accuracy_user_visible
  retrieval:   0.15   # 0.5*gold@1 + 0.3*gold@3 + 0.2*gold@8
  calibration: 0.10   # 1 - over_refusal_rate
  latency:     0.05   # within_budget_rate on p_user_complete_ms
hash: ""  # filled in by ops/scripts/lock_composite_weights.py
```

- [ ] **Step 2: Update scorer.** Load weights from yaml; compute composite per-iter using the matching schema for that iter (iter-04..iter-11 use legacy weights stored in same yaml under `applies_from_iter: 0` block; iter-12+ uses new weights).

- [ ] **Step 3: Hash-lock the file** via a small `ops/scripts/lock_composite_weights.py` helper that computes SHA-256 over the weights block and writes it back into the `hash` field. Pre-commit hook validates the hash.

- [ ] **Step 4: Tests + commit.**

```bash
pytest tests/unit/ops_scripts/ -q
git add docs/rag_eval/_config/composite_weights.yaml \
        ops/scripts/score_rag_eval.py \
        ops/scripts/lock_composite_weights.py
git commit -m "feat: trust first composite weights iter12"
```

---

## Phase 8 — Final: env example, docs, eval, scores

### Task 19: `ops/.env.example` iter-12 block

- [ ] **Step 1:** Append to `ops/.env.example`:

```
# ── iter-12 RAG knobs (defaults; see iter-12 RESEARCH.md) ──
# Class P: PATH_F sizing (sync RPC offload)
RAG_EXECUTOR_MAX_WORKERS=8
RAG_RPC_GLOBAL_SEMAPHORE=8
RAG_HTTPX_MAX_CONNECTIONS=16
RAG_HTTPX_MAX_KEEPALIVE=8
RAG_ENTITY_GATHER_SEMAPHORE=3
RAG_ANCHOR_BOOST_ENABLED=true       # re-enable in Phase 2
RAG_ANCHOR_SEED_INJECTION_ENABLED=true

# Class K3: confidence-gap bypass for magnet & retry gates
RAG_SCORE_RANK_GAP_BYPASS=1.5
RAG_RETRY_GAP_BYPASS=1.5

# Class K4: per-Kasten bootstrap window for magnet-spotter
RAG_KASTEN_BOOTSTRAP_WINDOW=50

# Class Q5: percentile-based title-overlap exemption
RAG_TITLE_OVERLAP_PERCENTILE=75
RAG_TITLE_OVERLAP_FLOOR_FALLBACK=0.10
```

Remove `RAG_SHORT_THEMATIC_THRESHOLD` (Class D-out — no longer used).

- [ ] **Step 2: Commit.**

```bash
git add ops/.env.example
git commit -m "docs: iter12 env flags"
```

### Task 20: Full pytest

- [ ] **Step 1:** `pytest -q` — expected: prior 2075+ passing plus iter-12 new tests, with the documented 4 pre-existing CI-environment failures.

- [ ] **Step 2: Push.**

```bash
git push origin master
```

### Task 21: Deploy + smoke validation + eval

- [ ] **Step 1:** Wait for `deploy-droplet.yml` green. If smoke 402 fires, run `python ops/scripts/reset_naruto_smoke_meter.py` and re-run via `gh run rerun --failed`.

- [ ] **Step 2:** Operator runs the eval (PowerShell):

```powershell
cd C:\Users\LENOVO\Documents\Claude_Code\Projects\Obsidian_Vault
$env:ZK_BEARER_TOKEN = (python ops/scripts/mint_eval_jwt.py)
$env:EVAL_USE_SSE_HARNESS='true'
python ops\scripts\eval_iter_03_playwright.py --iter iter-12
python ops\scripts\score_rag_eval.py --iter-dir docs\rag_eval\common\knowledge-management\iter-12
```

### Task 22: Write iter-12/scores.md (canonical template — NO fix recommendations)

- [ ] **Step 1:** Use the auto-generated `scores.md` from `score_rag_eval.py`. Match iter-09 / iter-10 / iter-11 canonical layout exactly: composite, components, RAGAS, latency, coverage, holistic (now with the new metric set), per-class breakdown, distributions, magnet-spotter (now bootstrap-derived), burst pressure, per-query table.

- [ ] **Step 2:** **Do NOT include fix recommendations, root-cause analysis, or carryover tables in scores.md.** Those belong in chat or RESEARCH.md / a separate iter-13 plan, never in scores.md.

- [ ] **Step 3: Commit.**

```bash
git add docs/rag_eval/common/knowledge-management/iter-12/scores.md
git commit -m "docs: iter12 scores"
git push origin master
```

---

## Phase 9 — Audit-derived additions (post-ratification 2026-05-07)

> **Why this exists:** after iter-12 PLAN was authored, a 5-agent verification pass + 6-agent industry-standard pass surfaced four high-confidence findings that fold cleanly into iter-12 scope without changing the core architecture. Recorded as Phase 9 (ordered AFTER core class work) so the diff is atomic; tasks reference the affected earlier phases for execution sequencing.

### Task 23: I1+I2 — `deploy-droplet.yml` STATIC_BODY + `.env.local` overlay + CI drift gate

**Why:** `.github/workflows/deploy-droplet.yml:307` does `printf '%s\n' "$ENV_BODY" | sudo tee /opt/zettelkasten/compose/.env` — UNCONDITIONAL overwrite (no `-a`). `compose/.env` is the only env_file in `ops/docker-compose.{blue,green}.yml`. Therefore every operator `echo >>` edit on the droplet is wiped on the next master push. The 5 iter-11 RAG knobs (`RAG_ANCHOR_BOOST_ENABLED`, both Class F offsets, `RAG_SHORT_THEMATIC_THRESHOLD`, `RAG_SCORE_RANK_PROTECT_ANCHORED`) are **missing from STATIC_BODY (lines 244-276)** — so the iter-11 final-eval rollback may NEVER have actually taken effect. Without this fix, every iter-12 env flag (Class P + K3 + K4 + Q5 + Q7 = 13 new knobs) will silently fail to land.

**Files:**
- Modify: `.github/workflows/deploy-droplet.yml` (STATIC_BODY ~L244-276, plus a NEW pre-deploy job that diffs STATIC_BODY against `.env.example`)
- Modify: `ops/docker-compose.blue.yml` and `ops/docker-compose.green.yml` (add `.env.local` as second `env_file`)
- Test: extend `tests/unit/ops_scripts/test_deploy_workflow_env.py` (NEW)

**Position in execution order:** RUN BEFORE Phase 1 (Class P deploy). The 13 iter-12 env flags listed in Task 19 of Phase 8 must ALSO be in STATIC_BODY, otherwise Phase 1 deploy ships with default-fallback values.

- [ ] **Step 1: Extend `STATIC_BODY` with all iter-11 + iter-12 knobs.**

```bash
# .github/workflows/deploy-droplet.yml — extend STATIC_BODY heredoc
STATIC_BODY=$(printf '%s\n' \
    # ... existing lines 244-276 ...
    # iter-11 carry-overs (previously only in .env.example)
    "RAG_ANCHOR_BOOST_ENABLED=true" \
    "RAG_ANCHOR_SEED_INJECTION_ENABLED=true" \
    "RAG_PARTIAL_NO_RETRY_FLOOR_OFFSET_THEMATIC=-0.1" \
    "RAG_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR_OFFSET_THEMATIC=-0.1" \
    "RAG_SCORE_RANK_PROTECT_ANCHORED=true" \
    # iter-12 Class P
    "RAG_EXECUTOR_MAX_WORKERS=8" \
    "RAG_RPC_GLOBAL_SEMAPHORE=8" \
    "RAG_HTTPX_MAX_CONNECTIONS=16" \
    "RAG_HTTPX_MAX_KEEPALIVE=8" \
    "RAG_ENTITY_GATHER_SEMAPHORE=3" \
    # iter-12 Class K3 + K4
    "RAG_SCORE_RANK_GAP_BYPASS=1.5" \
    "RAG_RETRY_GAP_BYPASS=1.5" \
    "RAG_KASTEN_BOOTSTRAP_WINDOW=50" \
    # iter-12 Class Q5 + Q7
    "RAG_TITLE_OVERLAP_PERCENTILE=75" \
    "RAG_TITLE_OVERLAP_FLOOR_FALLBACK=0.10" \
    "RAG_ROUTER_VERSION=v4")
```

**Critical:** for the iter-11 final-eval ROLLBACK posture, `RAG_ANCHOR_BOOST_ENABLED=false` must be set in this Phase 9 / Task 23 commit. Phase 1 PATH_F lands while flag is FALSE; Phase 2 flips both to TRUE.

- [ ] **Step 2: Add `.env.local` overlay to compose blue/green.**

```yaml
# ops/docker-compose.blue.yml (and green.yml)
services:
  app:
    env_file:
      - /opt/zettelkasten/compose/.env           # workflow-managed (every push rewrites)
      - /opt/zettelkasten/compose/.env.local     # operator overrides (NOT touched by workflow)
```

`.env.local` is created by operator via SSH ad-hoc (e.g., emergency rollback during outage). Docker Compose later-wins precedence (Docker docs: *envvars-precedence*) ensures override takes effect. `.env.local` survives every workflow push because the `tee` only writes `.env`.

- [ ] **Step 3: Add CI drift-gate job to the workflow.**

```yaml
# .github/workflows/deploy-droplet.yml — new job before deploy
env-drift-check:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Verify all knobs in .env.example are also in STATIC_BODY
      run: |
        EXAMPLE_KNOBS=$(grep -E '^RAG_|^GUNICORN_|^GEMINI_COOLDOWN_' ops/.env.example | cut -d= -f1 | sort -u)
        STATIC_KNOBS=$(grep -oE '"[A-Z_][A-Z0-9_]+=' .github/workflows/deploy-droplet.yml | tr -d '"=' | sort -u)
        DRIFT=$(comm -23 <(echo "$EXAMPLE_KNOBS") <(echo "$STATIC_KNOBS"))
        if [ -n "$DRIFT" ]; then
          echo "::error::Knobs documented in .env.example but missing from STATIC_BODY:"
          echo "$DRIFT"
          exit 1
        fi
```

`deploy` job depends on `env-drift-check` so a missing knob fails the deploy fast.

- [ ] **Step 4: Post-deploy smoke test verifying live env on droplet.**

```yaml
# .github/workflows/deploy-droplet.yml — extend post-deploy SSH step
- name: Verify live container env matches STATIC_BODY
  run: |
    ssh deploy@$DROPLET_IP "docker exec zettelkasten-${ACTIVE_COLOR} env" > /tmp/live_env.txt
    for KEY in RAG_ANCHOR_BOOST_ENABLED RAG_EXECUTOR_MAX_WORKERS RAG_RPC_GLOBAL_SEMAPHORE; do
      grep -q "^${KEY}=" /tmp/live_env.txt || { echo "::error::${KEY} missing on live droplet"; exit 1; }
    done
```

Catches "operator-edit got wiped" footgun and "STATIC_BODY missing the new knob" footgun in one check.

- [ ] **Step 5: Document the operator-override pattern in `ops/.env.example`** with a header comment:

```
# ops/.env.example
#
# OPERATOR OVERRIDE PATTERN (post-iter-12 Task 23):
#   1. Long-term knob changes go in .github/workflows/deploy-droplet.yml STATIC_BODY (committed).
#   2. Emergency / experimental overrides go in /opt/zettelkasten/compose/.env.local on the droplet
#      (SSH only; survives master pushes; Docker Compose later-wins).
#   3. Never `echo >> /opt/zettelkasten/compose/.env` — that file is rewritten on every push.
```

- [ ] **Step 6: Tests + commit.**

```bash
pytest tests/unit/ops_scripts/test_deploy_workflow_env.py -q
git add .github/workflows/deploy-droplet.yml \
        ops/docker-compose.blue.yml ops/docker-compose.green.yml \
        ops/.env.example \
        tests/unit/ops_scripts/test_deploy_workflow_env.py
git commit -m "ops: deploy env static body plus operator overlay"
git push origin master
```

After this lands, **before Phase 1 deploy**: SSH to droplet, `docker exec zettelkasten-${ACTIVE_COLOR} env | grep RAG_ANCHOR_BOOST_ENABLED` — must match the value in STATIC_BODY (default `false` for Phase 1 posture). This is the gate.

**Citations:**
- [Docker Compose env-var precedence (multi `--env-file` later-wins)](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/)
- [12-factor app III. Config](https://12factor.net/config)

---

### Task 24: I7b — `vm.swappiness=1` sysctl on droplet

**Why:** iter-11 final-eval showed 661 MB swap in use, `vm_swap_kb=587` per worker (verified via `docker inspect zettelkasten-green oom_killed=true` + `proc_stats` cgroup_swap). On a 2 GB droplet with `--preload` + BGE int8, the kernel's default `vm.swappiness=10` preemptively evicts the preloaded model pages even when RSS hasn't crossed the cgroup limit. Setting `vm.swappiness=1` keeps anonymous-mapped (model) pages resident and only swaps under genuine pressure. **Independent of iter-12 PATH_F** — orthogonal mitigation that helps even if PATH_F lands cleanly. Persisted via `/etc/sysctl.d/99-zettelkasten.conf` so it survives droplet reboot.

**Important:** the `_build_runtime` lru_cache claim (I7a) was rejected — `cascade.py:126,157,165` already make BGE/FlashRank/tokenizer module-level singletons under `--preload`, and `cascade.py:109-110` already disables ONNX arena (`enable_cpu_mem_arena=False`, `enable_mem_pattern=False`). The 80 MB worst-case from 16 cache slots × ~5 MB Python wrappers is NOT the swap driver. The real driver is sync-RPC stalls during burst — addressed by Class P. Swappiness is a defensive layer beneath that.

**Files:** none in repo. Droplet-side sysctl change.

**Position in execution order:** Phase 1 / Task 3 (post-deploy validation) — set immediately after PATH_F deploy lands, before re-running burst-12 staging probe.

- [ ] **Step 1:** SSH to droplet and persist sysctl:

```bash
ssh deploy@$DROPLET_IP <<'EOF'
sudo tee /etc/sysctl.d/99-zettelkasten.conf > /dev/null <<SYSCTL
# iter-12 Task 24 — keep preloaded BGE/Gemini SDK pages resident under
# --preload + 2 GB cgroup. Default 10 was preemptively evicting model pages
# even before genuine memory pressure (iter-11 forensic: 587MB/worker swap
# despite cgroup not at limit).
vm.swappiness=1
SYSCTL
sudo sysctl --system
sysctl vm.swappiness  # confirm value = 1
EOF
```

- [ ] **Step 2:** Re-run burst-12 staging probe (Phase 1 / Task 3 / Step 2). Expect: `proc_stats.vm_swap_kb` per worker drops from ~587 to <200 over the next 100 queries (kernel reclaims existing swap as access patterns favor RSS).

- [ ] **Step 3:** Document the sysctl in `ops/runbooks/droplet_setup.md` (NEW, or append to existing runbook if present) so droplet rebuild reproduces the setting.

- [ ] **Step 4:** Commit the runbook (no code changes; sysctl lives outside repo).

```bash
git add ops/runbooks/droplet_setup.md
git commit -m "ops: document vm swappiness sysctl iter12"
```

**Citations:**
- [Chris Down — In Defence of Swap](https://chrisdown.name/2018/01/02/in-defence-of-swap.html)
- [kernel.org sysctl vm documentation](https://www.kernel.org/doc/Documentation/sysctl/vm.txt)

---

### Task 25: I9 — switch `_holistic_metrics` headline to `primary_citation in expected` + add `retrieval_recall_at_1` diagnostic

**Why:** iter-11 had two divergent gold@1 numbers (0.6154 in scores.md, 0.7143 in timing_report.md) for the SAME run. Verified by research:
- `score_rag_eval.py:319` — `_holistic_metrics` reads `retrieved[0] in expected` (8/13 = 0.6154)
- `eval_iter_03_playwright.py:1447` — `_qa_summary` reads `gold_at_1` field set at L1243-1244 to `primary_citation in expected` (10/14 = 0.7143)

xQuAD reorders post-retrieval (verified for q5, q8, q12: `primary_citation != retrieved_node_ids[0]`). Aligning both on `primary_citation in expected` removes the rerank-noise from the headline metric — `retrieved[0]` measures retrieval recall (a stage diagnostic), `primary_citation` measures what the user actually saw cited (the user-facing truth). They serve different purposes; keep BOTH but cleanly separated.

**Class S (Phase 7 / Task 14) already adds `accuracy_user_visible = primary in expected AND NOT over_refusal AND NOT refused`. Task 25 augments Class S** by:
1. Switching the legacy `gold_at_1_unconditional` to also use `primary_citation` (so the diagnostic field aligns with the headline).
2. Adding a separate `retrieval_recall_at_1 = retrieved[0] in expected` field for retrieval-stage triage.
3. Per-class breakdown applies to BOTH metrics.

**Files:**
- Modify: `ops/scripts/score_rag_eval.py:289-350` (`_holistic_metrics` + `_aggregate_gold_metrics`)
- Modify: `ops/scripts/eval_iter_03_playwright.py:1444-1467` (`_qa_summary`)
- Test: extend `tests/unit/ops_scripts/test_score_rag_eval_iter12.py` (Task 14 test)

**Position in execution order:** Phase 7 / Task 14 — augment the same task. Single commit covers both.

- [ ] **Step 1: Failing test.**

```python
# tests/unit/ops_scripts/test_score_rag_eval_iter12.py — append
def test_retrieval_recall_separate_from_primary_citation():
    """iter-12 I9: retrieval_recall_at_1 (retrieved[0] in expected) is a
    diagnostic; gold_at_1_unconditional (primary_citation in expected) is the
    headline. Diverge on q5/q8/q12-shape (xQuAD reorders post-retrieval)."""
    from ops.scripts.score_rag_eval import _aggregate_gold_metrics
    rows = [
        {"primary_citation": "n1", "retrieved": ["n1"],     "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},
        {"primary_citation": "n1", "retrieved": ["n2"],     "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},  # rerank reorder
        {"primary_citation": "n2", "retrieved": ["n1"],     "expected": ["n1"], "expected_empty": False, "refused": False, "over_refusal": False},  # primary mismatch
    ]
    out = _aggregate_gold_metrics(rows)
    assert out["gold_at_1_unconditional"] == round(2/3, 4)         # primary-citation match: rows 1,2
    assert out["retrieval_recall_at_1"] == round(2/3, 4)           # retrieved[0] match: rows 1,3
    # accuracy_user_visible follows gold_at_1_unconditional (since none refused)
    assert out["accuracy_user_visible"] == round(2/3, 4)
```

- [ ] **Step 2: Implement.**

```python
# ops/scripts/score_rag_eval.py:_aggregate_gold_metrics — extend
def _aggregate_gold_metrics(rows: list[dict]) -> dict[str, float]:
    """iter-12 Class S + I9: trust-first metric set with retrieval-recall split."""
    scored = [r for r in rows if not r.get("expected_empty")]
    n_scored = max(len(scored), 1)

    def _primary_in_expected(r: dict) -> bool:
        prim = r.get("primary_citation")
        exp = r.get("expected") or []
        return bool(prim) and prim in exp

    def _retrieved0_in_expected(r: dict) -> bool:
        ret = r.get("retrieved") or r.get("retrieved_node_ids") or []
        exp = r.get("expected") or []
        return bool(ret) and ret[0] in exp

    user_visible_pass = sum(
        1 for r in scored
        if _primary_in_expected(r) and not r.get("over_refusal") and not r.get("refused")
    )
    primary_match = sum(1 for r in scored if _primary_in_expected(r))
    retrieved_match = sum(1 for r in scored if _retrieved0_in_expected(r))
    over_refusal = sum(1 for r in scored if r.get("over_refusal"))
    answered = [r for r in scored if not r.get("refused")]
    n_answered = max(len(answered), 1)
    under_refusal = sum(1 for r in answered if (r.get("faithfulness") or 1.0) < 0.5)

    return {
        "accuracy_user_visible": round(user_visible_pass / n_scored, 4),       # HEADLINE
        "gold_at_1_unconditional": round(primary_match / n_scored, 4),         # primary_citation diagnostic
        "retrieval_recall_at_1": round(retrieved_match / n_scored, 4),         # retrieval-stage diagnostic (NEW)
        "over_refusal_rate": round(over_refusal / n_scored, 4),
        "under_refusal_rate": round(under_refusal / n_answered, 4),
        "gold_at_1_not_applicable": sum(1 for r in rows if r.get("expected_empty")),
    }
```

- [ ] **Step 3: Mirror in `_qa_summary`.**

```python
# ops/scripts/eval_iter_03_playwright.py:_qa_summary
def _qa_summary(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r.get("expected_empty")]
    # ... existing accuracy_user_visible computation
    # iter-12 I9: surface BOTH metrics
    primary_match = sum(
        1 for r in scored
        if (r.get("primary_citation") and r["primary_citation"] in (r.get("expected") or []))
    )
    retrieved_match = sum(
        1 for r in scored
        if r.get("retrieved_node_ids") and r["retrieved_node_ids"][0] in (r.get("expected") or [])
    )
    n = max(len(scored), 1)
    return {
        "total": len(rows),
        "n_scored": len(scored),
        "n_not_applicable": len(rows) - len(scored),
        "accuracy_user_visible": round(/* as before */),
        "gold_at_1_unconditional": round(primary_match / n, 4),
        "retrieval_recall_at_1": round(retrieved_match / n, 4),
        # ... rest unchanged
    }
```

- [ ] **Step 4: Update `_render_scores_md` template** to surface both metrics with clear labels:

```
## Holistic monitoring (iter-12 trust-first)
- accuracy_user_visible:           0.XXXX  (HEADLINE — primary_citation in expected, refusal-deducted)
- gold_at_1_unconditional:         0.XXXX  (diagnostic — primary_citation in expected, no refusal deduction)
- retrieval_recall_at_1:           0.XXXX  (diagnostic — retrieved[0] in expected; isolates retrieval-stage from rerank reorder)
- over_refusal_rate:               0.XXXX
- under_refusal_rate:              0.XXXX
```

When `gold_at_1_unconditional > retrieval_recall_at_1`, rerank IMPROVED the user-visible top-1 (good). When inverse, rerank reordered AWAY from gold (bad — investigate xQuAD / Q5 percentile demote margin).

- [ ] **Step 5: Tests + commit.**

```bash
pytest tests/unit/ops_scripts/ -q
git add ops/scripts/score_rag_eval.py \
        ops/scripts/eval_iter_03_playwright.py \
        tests/unit/ops_scripts/test_score_rag_eval_iter12.py
git commit -m "feat: separate primary citation headline from retrieval recall"
```

---

### Task 26: I4 caveat — track demote-margin telemetry; conditional `_SCORE_RANK_DEMOTE_FACTOR` 0.85→0.75

**Why:** Q5 percentile-based magnet exemption (Phase 5 / Task 9) damps unearned magnets via `*= _SCORE_RANK_DEMOTE_FACTOR` (currently 0.85). Worst-case margin calculation: pre-demote magnet rrf 0.65 → post-demote 0.5525 vs legit top 0.55 → margin 0.0025. xQuAD slot-1 = argmax(rrf), so this thin margin lets the magnet still win in adversarial cases. **Industry consensus from R2 research (Carbonell+Goldstein MMR, Qdrant MMR, Ranksys xQuAD): anchor-pinning lives OUTSIDE the diversity picker.** Touching xQuAD is wrong (over-promotes anchored cands on multi-anchor queries q10-shape, anchor-mismatch shapes).

**Right approach: add telemetry + conditional knob.** Phase 5 ships with 0.85 (no behavior change vs current). Phase 9 / Task 26 instruments the demote-margin and ships a feature-flag for 0.75. After iter-12 final eval, IF the telemetry shows margins < 0.05 on >5% of THEMATIC queries, flip the env flag to use 0.75. Otherwise keep 0.85.

**Files:**
- Modify: `website/features/rag_pipeline/retrieval/hybrid.py:_apply_score_rank_demote` (add margin telemetry log line)
- Modify: `ops/scripts/score_rag_eval.py` (read telemetry from per-query rows; report margin distribution)
- Modify: `ops/.env.example` (new flag `RAG_SCORE_RANK_DEMOTE_FACTOR`)
- Test: extend `tests/unit/rag/retrieval/test_title_overlap_percentile.py`

**Position in execution order:** Phase 5 (Q5 percentile) augment. The telemetry change must land WITH the percentile change so iter-12 final eval has the data.

- [ ] **Step 1: Make the demote factor env-configurable.**

```python
# website/features/rag_pipeline/retrieval/hybrid.py
import os

_SCORE_RANK_DEMOTE_FACTOR = float(os.environ.get("RAG_SCORE_RANK_DEMOTE_FACTOR", "0.85"))
# iter-12 default 0.85 (unchanged from iter-10/11). If iter-12 final eval shows
# THEMATIC magnet-margin < 0.05 on >5% of queries, flip to 0.75 in droplet env.
```

- [ ] **Step 2: Emit margin telemetry in the demote log line.**

```python
# In _apply_score_rank_demote, BEFORE returning:
post_demote = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
top1 = post_demote[0].rrf_score if post_demote else 0.0
top2 = post_demote[1].rrf_score if len(post_demote) > 1 else 0.0
margin = top1 - top2
_log.info(
    "score_rank_demote class=%s n_cands=%d n_demoted=%d title_demoted=%d "
    "demote_factor=%.3f post_top1=%.4f post_top2=%.4f margin=%.4f",
    getattr(query_class, "value", query_class),
    n, n_demoted, n_title_demoted, _SCORE_RANK_DEMOTE_FACTOR,
    top1, top2, margin,
)
```

- [ ] **Step 3: Surface margin distribution in scores.md.**

`_render_scores_md` adds:

```
## Magnet-gate margin distribution (iter-12 Task 26)
- THEMATIC queries with margin < 0.05: N (target: 0)
- p50 margin: 0.XXXX
- p10 margin: 0.XXXX
- recommended demote factor: 0.85 (current) | 0.75 (if p10 < 0.05)
```

If `p10 < 0.05`, the next iter should flip the env flag.

- [ ] **Step 4: Add to `ops/.env.example`.**

```
# iter-12 Task 26 — magnet-gate demote factor (default 0.85; flip to 0.75 if iter-12 eval shows p10 margin < 0.05)
RAG_SCORE_RANK_DEMOTE_FACTOR=0.85
```

Add to `STATIC_BODY` in deploy-droplet.yml (Task 23 already lists this principle).

- [ ] **Step 5: Tests + commit.**

```bash
pytest tests/unit/rag/retrieval/ -q
git add website/features/rag_pipeline/retrieval/hybrid.py \
        ops/scripts/score_rag_eval.py \
        ops/.env.example \
        tests/unit/rag/retrieval/test_title_overlap_percentile.py
git commit -m "feat: demote factor telemetry and env knob"
```

**Citations:**
- [Carbonell & Goldstein 1998 MMR](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf) — anchor-pinning OUTSIDE the diversity picker is the canonical pattern
- [Qdrant MMR diversity-aware reranking](https://qdrant.tech/blog/mmr-diversity-aware-reranking/)

---

### Task 28: R5 — Ingest-time entity canonicalization (LLM-generated aliases on `kg_nodes`)

**Why:** today's anchor resolver does fuzzy `ILIKE` on `kg_nodes.name` only. Queries like "Naval" miss zettels titled "Naval Ravikant on happiness". Hand-coded aliases per Kasten over-fit. Industry pattern (Wikidata, Neo4j agent-memory, DBpedia surface-forms) converges on **canonical name + array of aliases materialized at ingest, indexed for fuzzy match at query time**. Replaces R6-rejected hand-curated alias proposal with proper IaC. Cost: ~$0.0001/zettel (flash-lite single call), one-time per ingest + on-summary-change.

**Files:**
- New: `supabase/website/kg_public/migrations/2026-05-07_kg_node_aliases.sql`
- New: `website/features/rag_pipeline/ingest/entity_canonicalizer.py`
- Modify: `website/core/pipeline.py` (zettel write path — call canonicalizer before `kg_nodes` upsert)
- Modify: `website/features/rag_pipeline/retrieval/entity_anchor.py` (read new `matched_via` column for iter-13 attribution)
- New: `ops/scripts/backfill_aliases.py` (one-shot backfill of existing zettels)
- Tests: `tests/unit/rag/ingest/test_entity_canonicalizer.py` (NEW); extend `test_entity_anchor.py`

**Position:** New Phase 10 — runs AFTER iter-12 retrieval-stage changes settle. Aliases populate progressively (new ingests immediately, old zettels via backfill).

- [ ] **Step 1: Schema migration.**

```sql
-- supabase/website/kg_public/migrations/2026-05-07_kg_node_aliases.sql
ALTER TABLE kg_nodes ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}';
ALTER TABLE kg_nodes ADD COLUMN IF NOT EXISTS summary_hash text;  -- for idempotent regen on UPDATE
CREATE INDEX IF NOT EXISTS kg_nodes_aliases_gin ON kg_nodes USING GIN (aliases);
CREATE INDEX IF NOT EXISTS kg_nodes_aliases_trgm
  ON kg_nodes USING GIN (array_to_string(aliases, ' ') gin_trgm_ops);

-- Replace rag_resolve_entity_anchors to also match aliases + emit matched_via:
CREATE OR REPLACE FUNCTION rag_resolve_entity_anchors(
  p_sandbox_id uuid, p_entities text[]
) RETURNS TABLE (node_id text, matched_via text) LANGUAGE sql STABLE AS $$
  SELECT DISTINCT n.id,
    CASE
      WHEN EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE n.name ILIKE '%'||e||'%') THEN 'name'
      WHEN EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE e ILIKE ANY(n.aliases)) THEN 'alias'
      WHEN EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE e = ANY(n.tags)) THEN 'tag'
    END AS matched_via
  FROM rag_sandbox_members m
  JOIN kg_nodes n ON n.id = m.node_id AND n.user_id = m.user_id
  WHERE m.sandbox_id = p_sandbox_id
    AND ( EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE n.name ILIKE '%'||e||'%')
       OR EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE e ILIKE ANY(n.aliases))
       OR EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE e = ANY(n.tags)) );
$$;
```

- [ ] **Step 2: Canonicalizer module.**

```python
# website/features/rag_pipeline/ingest/entity_canonicalizer.py
"""iter-12 R5: LLM-generated entity aliases at zettel ingest.

Single flash-lite call per zettel produces a canonical name + up to 8
aliases. Aliases include common abbreviations, transliterations,
multi-language variants. Summary-conditioned to disambiguate acronyms
(e.g. "AI" + tech summary -> "Artificial Intelligence", not "Adobe Illustrator").

Idempotent on UPDATE: skip if `summary_hash` unchanged.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

_PROMPT = """\
Given a zettel's title and summary, return canonical entity names that
the user might use to refer to this zettel. Include abbreviations,
common variants, and transliterations if applicable.

Strict JSON: {"canonical": str, "aliases": [str]}
- canonical: the most precise name (often the zettel's primary subject).
- aliases: up to 8 alternative ways the user might phrase it; exclude
  generic words and the canonical itself.
- For people: include first-name only, last-name only, and full name.
- For acronyms: include both expanded and acronym forms.
- Drop entries whose lowercased form equals the title's lowercased form.

Title: {title}
Summary: {summary}
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "canonical": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
    },
    "required": ["canonical", "aliases"],
}


async def canonicalize_node(*, title: str, summary: str, key_pool) -> dict[str, Any]:
    """Returns {'canonical': str, 'aliases': list[str]}. On failure returns empty aliases."""
    if not title:
        return {"canonical": "", "aliases": []}
    try:
        result = await key_pool.generate_structured(
            prompt=_PROMPT.format(title=title, summary=summary[:1500]),
            schema=_SCHEMA,
            model_preference="flash-lite",
        )
    except Exception as exc:
        # Never fail the ingest — return empty aliases, log warning.
        return {"canonical": title, "aliases": []}

    aliases = result.get("aliases") or []
    title_lower = title.lower().strip()
    cleaned = []
    seen = set()
    for a in aliases:
        if not isinstance(a, str):
            continue
        a = a.strip()
        if not a or len(a) < 2 or len(a) > 100:
            continue
        a_lower = a.lower()
        if a_lower == title_lower or a_lower in seen:
            continue
        if a_lower in title_lower or title_lower in a_lower:
            continue  # substring of title — redundant
        if not re.search(r"\w", a):
            continue  # punctuation / numbers only
        cleaned.append(a)
        seen.add(a_lower)
        if len(cleaned) >= 8:
            break
    return {
        "canonical": (result.get("canonical") or title).strip(),
        "aliases": cleaned,
    }


def summary_hash(summary: str) -> str:
    return hashlib.sha256((summary or "").encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 3: Wire into write path** (`website/core/pipeline.py`):

```python
# In the kg_nodes upsert path (write/update zettel):
from website.features.rag_pipeline.ingest.entity_canonicalizer import canonicalize_node, summary_hash

new_summary_hash = summary_hash(zettel.summary)
existing = supabase.table("kg_nodes").select("summary_hash").eq("id", zettel.id).execute()
needs_regen = (not existing.data) or existing.data[0].get("summary_hash") != new_summary_hash

if needs_regen:
    canonical_result = await canonicalize_node(
        title=zettel.title, summary=zettel.summary, key_pool=settings.key_pool,
    )
    aliases_payload = canonical_result["aliases"]
else:
    aliases_payload = None  # leave existing aliases untouched

upsert_payload = {
    "id": zettel.id,
    "name": zettel.title,
    "summary": zettel.summary,
    "summary_hash": new_summary_hash,
    # ...
}
if aliases_payload is not None:
    upsert_payload["aliases"] = aliases_payload
supabase.table("kg_nodes").upsert(upsert_payload).execute()
```

- [ ] **Step 4: Caller-side change in `entity_anchor.py`** — surface `matched_via` per resolved row in `_log.info` for iter-13 attribution. No semantic change.

- [ ] **Step 5: Backfill script** for existing zettels:

```python
# ops/scripts/backfill_aliases.py — iterate kg_nodes WHERE aliases='{}' OR summary_hash IS NULL,
# call canonicalize_node, batch-update. Rate-limited to flash-lite quota; resumable; idempotent.
```

- [ ] **Step 6: Tests + commit.**

```bash
pytest tests/unit/rag/ingest/ tests/unit/rag/retrieval/test_entity_anchor.py -q
git add supabase/website/kg_public/migrations/2026-05-07_kg_node_aliases.sql \
        website/features/rag_pipeline/ingest/entity_canonicalizer.py \
        website/core/pipeline.py \
        website/features/rag_pipeline/retrieval/entity_anchor.py \
        ops/scripts/backfill_aliases.py \
        tests/unit/rag/ingest/test_entity_canonicalizer.py \
        tests/unit/rag/retrieval/test_entity_anchor.py
git commit -m "feat: ingest time entity canonicalization with llm aliases"
```

**Citations:**
- [Wikidata Help:Aliases](https://www.wikidata.org/wiki/Help:Aliases)
- [Neo4j Agent Memory: Entity Resolution](https://neo4j.com/labs/agent-memory/explanation/resolution-deduplication/)
- [BLINK + Elasticsearch industrial paper (NAACL 2022)](https://aclanthology.org/2022.naacl-industry.38.pdf)
- [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)
- [DBpedia Spotlight surface forms](https://www.dbpedia-spotlight.org/)

**Anti-pattern guards:**
- DO NOT add hand-coded alias lists per Kasten — defeats the purpose.
- DO NOT regenerate aliases on every read; gate on `summary_hash` change.
- DO NOT block the ingest write on LLM failure — return empty aliases, log warning, retry via backfill.

#### Caveats & rollback

**Risk-mitigation:**
- **Alias quality from flash-lite varies by domain.** Mitigation: schema-validate post-LLM (drop substrings of title, len < 2, punctuation-only, cap 8); summary-conditioned prompt disambiguates acronyms ("AI" + tech summary → "Artificial Intelligence", not "Adobe Illustrator").
- **Bulk re-summarization storm** (e.g. operator runs `backfill_aliases.py` over 100-Kasten user base): cap concurrent flash-lite calls via `asyncio.Semaphore(4)` matched to Class P's `RAG_RPC_GLOBAL_SEMAPHORE=8` to share thread budget; resumable via `WHERE aliases='{}' OR summary_hash IS NULL` — running twice is safe.
- **GIN index bloat on `aliases text[]`** under high-write-rate Kastens: schedule monthly `REINDEX INDEX CONCURRENTLY kg_nodes_aliases_gin` via existing maintenance cron; `pg_stat_user_indexes` monitor for `idx_blks_read / idx_blks_hit` ratio > 5% triggers manual reindex.
- **`matched_via='alias'` attribution is iter-13 territory** — iter-12 emits but does not act on it (avoid premature optimization).

**Edge cases:**
- **LLM returns alias matching SQL token** (e.g. `'); DROP TABLE--`): aliases are Postgres `text[]` parameters, NEVER concatenated into raw SQL. Parameterized queries make injection impossible. Schema validation also strips entries failing `\w` regex.
- **Empty zettel summary** (auto-generated title only): canonicalizer returns `{"canonical": title, "aliases": []}` — benign no-op.
- **Multi-language Kasten with mixed scripts** (e.g. half English, half Mandarin): LLM emits transliterations + native-script + common English forms. If LLM quality is poor for a script, aliases stay empty and the resolver degrades gracefully to name+tag matching (existing iter-08 behavior).
- **Race: concurrent updates to same zettel** (write A and write B both compute aliases): last-writer-wins via `kg_nodes` UPSERT — both writes succeed; one alias set persists. Acceptable.
- **Alias list grows past 8 across multiple updates**: cap at 8 in canonicalizer output; old aliases overwritten on regen — no append semantics.
- **`summary_hash` collision** (two distinct summaries hash to same 16-hex prefix): probability ~10⁻¹⁹ per pair; accept.

**Rollback paths (rank-ordered, lightest first):**
1. **Disable canonicalization on write** — env flag `RAG_INGEST_CANONICALIZER_ENABLED=false` in `STATIC_BODY` + redeploy. Existing aliases remain; new writes skip the LLM call and emit `aliases='{}'`. **Reversible; zero data loss.**
2. **Roll back the resolver** — revert `rag_resolve_entity_anchors` to name+tag-only via SQL migration:
   ```sql
   CREATE OR REPLACE FUNCTION rag_resolve_entity_anchors(p_sandbox_id uuid, p_entities text[]) RETURNS TABLE (node_id text) LANGUAGE sql STABLE AS $$
     SELECT DISTINCT n.id FROM rag_sandbox_members m JOIN kg_nodes n ON n.id=m.node_id AND n.user_id=m.user_id WHERE m.sandbox_id=p_sandbox_id AND (EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE n.name ILIKE '%'||e||'%') OR EXISTS (SELECT 1 FROM unnest(p_entities) e WHERE e=ANY(n.tags)));
   $$;
   ```
   Aliases ignored; query-time falls back to iter-11 behavior. **Reversible.**
3. **Drop alias columns** — `ALTER TABLE kg_nodes DROP COLUMN aliases, DROP COLUMN summary_hash;`. **Reversible per CLAUDE.md** (additive migration).
4. **Code revert** — `git revert` the canonicalizer commit. Last resort; operator-approved.

---

### Task 29: R6 — Confidence-thresholded entity extraction + cap-3 + per-Kasten resolution-gate cache

**Why:** the query-time metadata extractor at `metadata.py:124-168` returns 4-7 entities including noise ("burst probe", "command-line tool", "programming language"). Each entity triggers a sync supabase RPC. Even with iter-12 PATH_F's `to_thread` offload, fewer/sharper entities reduces DB load proportionally. Industry pattern: **verbalized confidence in JSON schema** (native to Gemini structured output, [Gemini docs](https://ai.google.dev/gemini-api/docs/structured-output)) + cap-N + negative-resolution cache (DNS pattern, [RFC 9520](https://datatracker.ietf.org/doc/rfc9520/)).

**Files:**
- Modify: `website/features/rag_pipeline/query/metadata.py` (prompt, schema, `_a_pass`)
- New: `website/features/rag_pipeline/query/blocklist.py` (`EntityBlocklist` class)
- New: `supabase/website/kg_public/migrations/2026-05-07_kg_extraction_blocklist.sql`
- Modify: anchor-resolver caller (where resolution success/miss is observed) to call `record_miss`/`record_hit`
- Test: `tests/unit/rag/query/test_metadata_confidence.py` (NEW)

**Position:** New Phase 10 / Task 29 — independent of R5 ingest path; can ship in parallel.

- [ ] **Step 1: Update prompt to ask for confidence.**

```python
# website/features/rag_pipeline/query/metadata.py:27 — replace _QUERY_ENTITY_PROMPT
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
```

Schema:

```python
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
        "authors": {"$ref": "#/definitions/entityList"},
        "channels": {"$ref": "#/definitions/entityList"},
    },
}
```

- [ ] **Step 2: Filter, sort, cap-3 in `_a_pass`.**

```python
# metadata.py — replace _a_pass body
_CONFIDENCE_FLOOR = float(os.environ.get("RAG_ENTITY_CONFIDENCE_FLOOR", "0.7"))
_ENTITY_TOP_N = int(os.environ.get("RAG_ENTITY_TOP_N", "3"))


def _filter_and_cap(items: list[dict], blocklist=None, sandbox_id=None) -> list[str]:
    # 1. Drop below confidence floor
    kept = [e for e in items if isinstance(e, dict)
            and isinstance(e.get("confidence"), (int, float))
            and float(e["confidence"]) >= _CONFIDENCE_FLOOR]
    # 2. Sort by confidence DESC, tie-break by len(text) DESC, then istitle()
    kept.sort(key=lambda e: (
        -float(e.get("confidence", 0)),
        -len(e.get("text", "")),
        not e.get("text", "").istitle(),
    ))
    # 3. Cap-N
    kept = kept[:_ENTITY_TOP_N]
    # 4. Apply per-Kasten blocklist (if available)
    if blocklist is not None and sandbox_id is not None:
        kept = [e for e in kept if not blocklist.is_blocked(sandbox_id, e["text"])]
    # 5. If all dropped, keep top-1 by confidence (fallback)
    if not kept and items:
        top = max(
            (e for e in items if isinstance(e, dict)),
            key=lambda e: float(e.get("confidence", 0)),
            default=None,
        )
        if top:
            kept = [top]
    return [e["text"] for e in kept]
```

- [ ] **Step 3: Resolution-gate cache table.**

```sql
-- supabase/website/kg_public/migrations/2026-05-07_kg_extraction_blocklist.sql
CREATE TABLE IF NOT EXISTS kg_extraction_blocklist (
  sandbox_id uuid NOT NULL,
  entity_text_norm text NOT NULL,                  -- lower(trim(text))
  consecutive_misses int NOT NULL DEFAULT 0,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  blocked_until timestamptz,                        -- NULL = active block
  PRIMARY KEY (sandbox_id, entity_text_norm)
);
CREATE INDEX IF NOT EXISTS kg_extraction_blocklist_active_idx
  ON kg_extraction_blocklist (sandbox_id, blocked_until);
```

- [ ] **Step 4: `EntityBlocklist` helper.** Async via `rpc_call` (Class P wrapper).

```python
# website/features/rag_pipeline/query/blocklist.py
"""iter-12 R6: per-Kasten negative-resolution cache (DNS-style)."""
from __future__ import annotations

import os
from datetime import timedelta

_MISS_THRESHOLD = int(os.environ.get("RAG_BLOCKLIST_MISS_THRESHOLD", "2"))
_BLOCK_TTL_DAYS = int(os.environ.get("RAG_BLOCKLIST_TTL_DAYS", "7"))
_COLD_START_NODE_FLOOR = int(os.environ.get("RAG_BLOCKLIST_COLD_START_NODES", "50"))


class EntityBlocklist:
    def __init__(self, supabase):
        self._sb = supabase

    async def is_blocked(self, sandbox_id: str, entity: str) -> bool:
        # Cold-start: skip block if Kasten has < 50 nodes
        # ... query node count or accept Kasten-stats hint
        # Then SELECT blocked_until FROM kg_extraction_blocklist WHERE ...
        # Return True if blocked_until > now()
        ...

    async def record_miss(self, sandbox_id: str, entity: str) -> None:
        # UPSERT consecutive_misses += 1; if >= _MISS_THRESHOLD, set blocked_until
        ...

    async def record_hit(self, sandbox_id: str, entity: str) -> None:
        # DELETE row (resolution success evicts)
        ...
```

- [ ] **Step 5: Hook resolution outcomes** — in `entity_anchor.resolve_anchor_nodes` (already touched by R5), after each per-entity RPC, call `blocklist.record_miss(sandbox_id, entity)` if empty result, `record_hit` otherwise.

- [ ] **Step 6: Cold-start guard** — if Kasten has < 50 nodes, blocklist is bypassed (fail-open). Read node count from `kasten_stats` cache (already in K4).

- [ ] **Step 7: Failure-mode handling** — if blocklist DB unavailable, fail-open (don't block); log WARN. Malformed confidence → default to 0.5 + WARN log.

- [ ] **Step 8: Tests + commit.**

```bash
pytest tests/unit/rag/query/ -q
git add website/features/rag_pipeline/query/metadata.py \
        website/features/rag_pipeline/query/blocklist.py \
        supabase/website/kg_public/migrations/2026-05-07_kg_extraction_blocklist.sql \
        tests/unit/rag/query/test_metadata_confidence.py
git commit -m "feat: confidence threshold cap3 plus per kasten blocklist"
```

**Citations:**
- [Gemini Structured Output (verbalized confidence in schema)](https://ai.google.dev/gemini-api/docs/structured-output)
- [SteerConf ICLR 2025 (calibration of LLM verbalized confidence)](https://arxiv.org/pdf/2503.02863)
- [RFC 9520 — Negative Caching of DNS Resolution Failures](https://datatracker.ietf.org/doc/rfc9520/) — pattern foundation
- [GraphRAG entity-disambiguation 85% gate](https://www.sowmith.dev/blog/graphrag-entity-disambiguation)
- [LangChain Extraction Benchmarking](https://blog.langchain.com/extraction-benchmarking/)

**Anti-pattern guards:**
- DO NOT add hand-coded English stoplist (R6 audit-rejected; over-fits Kasten).
- DO NOT block at < 50-node Kastens (cold-start defense).
- DO NOT block entities the LLM extracted with confidence ≥ 0.9 (treat high-confidence as override).
- Treat blocklist read as best-effort — never make it critical-path.

#### Caveats & rollback

**Risk-mitigation:**
- **Verbalized confidence is poorly calibrated for non-English** ([SteerConf ICLR 2025](https://arxiv.org/pdf/2503.02863): ECE ~20% post-anchoring, worse for non-EN). Mitigation: log `query_lang_hint` per query; iter-13 will introduce per-language threshold (e.g. 0.6 for non-Latin) gated on observed precision. iter-12 ships static 0.7 with WARN log when query lacks Latin chars.
- **Single-topic Kasten over-blocking** (e.g. all queries mention "transformer" → `transformer` blocked after 2 misses → future legitimate queries fail). Mitigation: hard override — never block entities present in ≥10% of Kasten nodes. Computed nightly from `kg_nodes` content; cached in `kg_kasten_metrics`.
- **Cold-start over-blocking on small Kastens** (resolution miss rate is artificially high before Kasten populated). Mitigation: skip blocklist read when Kasten has < 50 nodes (Step 6 cold-start guard).
- **Blocklist drift on Kasten content change** (user adds zettel that contains a previously-blocked entity). Mitigation: `record_hit` deletes the row immediately — successful resolution evicts. New zettel ingest triggers nothing extra; first query that resolves the entity evicts.
- **LLM emits malformed JSON / wrong schema**: try/except in parser; on failure, defaults `confidence=0.5` per entity + WARN log; query proceeds with degraded but functional extraction.

**Edge cases:**
- **Gemini returns confidence as string (`"0.9"`)**: parser tolerates string→float coercion; on `ValueError` defaults to 0.5.
- **Gemini returns confidence > 1.0 or < 0.0**: clamp to `[0, 1]` before threshold compare.
- **All entities below 0.7**: keep top-1 by confidence (Step 2 fallback). Better than empty entity list which would make `_needs_a_pass` re-fire.
- **Blocklist DB unreachable**: `EntityBlocklist.is_blocked` catches exception, returns False (fail-open). WARN logged. Zero behavior regression vs no-blocklist baseline.
- **Race: same entity blocked AND record_hit fires concurrently**: Postgres row-level lock on UPSERT serializes; either `record_miss` increments and survives `record_hit` DELETE, or DELETE wins. Either way self-corrects within 1-2 queries.
- **Tie-break determinism**: when two entities share the same confidence, sort by `(−len(text), not text.istitle())` ensures deterministic ordering across runs (test-stable).
- **Schema rejected by Gemini structured-output API** (rare; depth limit): caller falls back to flat schema, emits WARN, no confidence filter that query.

**Rollback paths (rank-ordered, lightest first):**
1. **Disable blocklist** — env flag `RAG_BLOCKLIST_ENABLED=false` in `STATIC_BODY` + redeploy. Cap-3 + confidence floor still active. **Reversible.**
2. **Disable confidence filter** — env flag `RAG_ENTITY_CONFIDENCE_FLOOR=0.0` (or `RAG_ENTITY_TOP_N=999`). All entities returned regardless of confidence. **Reversible.**
3. **Revert prompt + schema** — env flag `RAG_METADATA_LEGACY_SCHEMA=true` falls back to iter-11's flat schema (no confidence field). Caller code path branches on the flag. **Reversible.**
4. **Drop blocklist table** — `DROP TABLE kg_extraction_blocklist;`. Additive migration; safe to drop. **Reversible.**
5. **Code revert** — `git revert` the metadata + blocklist commit. Last resort; operator-approved.

---

### Task 30: R3 — Tier-1 monitor stack (cited_in_context + gold_expectation_groundedness_check)

**Why:** Q3 over-refusal had a deeper cause than the orchestrator gate — the gold zettel `yt-effective-public-speakin` may not literally contain "verbal punctuation". An automated pre-eval gate ([RAGAS context_recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/) pattern) would flag stale gold expectations BEFORE the eval runs, preventing the system from being penalized for honest refusals on uncoverable queries. Plus, runtime `cited_in_context` guard catches citation hallucination during normal operation across all Kastens.

**Files:**
- New: `website/api/_citation_guard.py` — Tier-1 runtime guard
- Modify: `website/api/routes.py` summarize handler — invoke guard
- New: `ops/scripts/audit_gold_expectations.py` — Tier-1 pre-eval check
- New: `docs/rag_eval/_audit/` — output directory with stale-gold flags
- Test: `tests/unit/rag/api/test_citation_guard.py` (NEW)

**Position:** Phase 1.5 (between PATH_F deploy and Phase 2 anchor-boost re-enable). Cheap, foundational; informs all subsequent eval interpretation.

- [ ] **Step 1: `cited_in_context` runtime guard** — before returning the SSE response, assert `primary_citation in retrieved_node_ids`. If not, log WARN with `qid`, mark response with `_citation_drift: true` flag (does NOT fail the request). Catches citation hallucination at runtime in production.

- [ ] **Step 2: `gold_expectation_groundedness_check`** — pre-eval helper script. For each (query, expected zettel_id) pair in `queries.json`:
  1. Fetch all chunks for `zettel_id`.
  2. Use Gemini-flash-lite NLI prompt: "Does the following content support this answer? content=<chunks>, hypothesis=<query+expected_keyphrases>".
  3. If NLI score < 0.5 → mark gold expectation as `coverage_blind: true` and EXCLUDE from gold@1 numerator (treat as N/A).
  4. Output `docs/rag_eval/common/<kasten>/iter-N/_audit/coverage_blind_queries.json`.

- [ ] **Step 3: Auto-flag stale gold for the 14 KM-Kasten queries** as part of iter-12 final eval; the audit's output is consumed by `score_rag_eval.py` to exclude flagged queries from scoring (similar to E1 expected_empty treatment).

- [ ] **Step 4: Tests + commit.**

```bash
pytest tests/unit/rag/api/test_citation_guard.py -q
git add website/api/_citation_guard.py website/api/routes.py \
        ops/scripts/audit_gold_expectations.py \
        tests/unit/rag/api/test_citation_guard.py
git commit -m "feat: tier1 runtime citation guard plus pre eval groundedness audit"
```

**Citations:**
- [RAGAS Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)
- [RAGAS Faithfulness](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/faithfulness/)
- [RAGTruth ACL 2024](https://aclanthology.org/2024.acl-long.585/)

**Deferred to iter-13 (Tier 2 + Tier 3):**
- LettuceDetect / ModernBERT span-NLI (~150MB model dep — needs droplet RAM review)
- RAGAS TestSet Generator for per-Kasten corpus self-test (token cost; quota planning)
- Phoenix/Arize observability dashboards
- Embedding-drift JS-divergence monitor

#### Caveats & rollback

**Risk-mitigation:**
- **`gold_expectation_groundedness_check` may disagree with operator's hand-curated gold expectation** (model-as-judge has known biases). Mitigation: gate's output is ADVISORY — produces a `coverage_blind` flag in `_audit/coverage_blind_queries.json`, but the human operator decides whether to accept the flag (manual review pass before scoring). Add `--auto-exclude` flag to `audit_gold_expectations.py` that defaults to FALSE (require explicit operator opt-in to auto-exclude).
- **NLI prompt drift between runs** (model upgrades, prompt tweaks): pin the NLI prompt template + model version per iter under `docs/rag_eval/_audit/groundedness_prompt_v1.txt` (hash-locked); audit script fails if prompt hash differs from the eval's prompt hash without explicit operator override.
- **False positive on paraphrased zettels** (gold expectation is semantically supported but lexically distant from chunks): NLI threshold 0.5 calibrated for "supports the answer". If FPR observed > 10% on canary, raise threshold to 0.6 or augment with multi-prompt voting (deferred to iter-13).
- **Runtime `cited_in_context` guard runs on every request** — keep it cheap: O(set membership), no LLM calls; <1ms per request.
- **`coverage_blind` queries excluded from gold@1 numerator** (E1-style N/A treatment): means iter-12 final eval may show a lower denominator than iter-11; document this explicitly in `scores.md` so operator doesn't compare denominators across iters as if they were the same.

**Edge cases:**
- **Zettel chunk content changes between audit run and eval run** (rare race): re-run audit before each eval is the operator-side safeguard; build into `eval_iter_03_playwright.py` pre-eval hook.
- **Empty `expected` array** (refusal-expected query like q9): `gold_expectation_groundedness_check` skips (E1 already excludes); audit emits `expected_empty=true` row in audit JSON.
- **Multi-zettel `expected` array** (compound queries): NLI runs per gold zettel; query is `coverage_blind` only if NLI < 0.5 for ALL listed zettels. Conservative — if any one zettel grounds the answer, accept.
- **Citation guard: synth cites a zettel NOT in `retrieved_node_ids`** (e.g. fabricated node-id): runtime guard logs WARN with `_citation_drift: true` flag in response metadata; does NOT fail the request (degrades gracefully). Eval scoring uses the flag to exclude.
- **Audit script DB unreachable**: returns "could not run audit, skipping" + non-zero exit; caller (eval pipeline) must decide whether to proceed without audit (operator-approved per-run).

**Rollback paths (rank-ordered, lightest first):**
1. **Disable runtime citation guard** — env flag `RAG_CITATION_GUARD_ENABLED=false` in `STATIC_BODY`. Response metadata loses `_citation_drift` field; nothing else changes. **Reversible.**
2. **Disable pre-eval audit auto-exclusion** — `audit_gold_expectations.py --no-auto-exclude` (default). Audit runs and produces JSON, but flagged queries are NOT excluded from scoring. Operator reviews JSON manually. **Reversible.**
3. **Skip audit entirely** — don't call `audit_gold_expectations.py` in eval pipeline. iter-11 scoring semantics preserved. **Reversible — single-line skip.**
4. **Code revert** — `git revert` the citation-guard + audit commit. Last resort; operator-approved.

---

### Task 31: R4 — Per-Kasten Thompson-sampling bandit for anchor-seed floor (FULL SHIP iter-12 with R4-followup safety harness)

**Why:** static `_ANCHOR_SEED_FLOOR_RRF=0.30` is a per-Kasten over-fit. Replace with per-Kasten Thompson-sampling bandit over arms `{0.25, 0.30, 0.35, 0.40}`. Reward = `seed_survived` (rerank rank ≤ FINAL_TOP_K). Operator authorization (chat 2026-05-07): "we are in testing mode with few users — ship the FULL BANDIT in iter-12 with telemetry for monitoring across time". R4-followup websearch agent (2026-05-07) validated the design with **SHIP-WITH-MODIFICATIONS** verdict requiring 4 mandatory changes before activation.

**4 mandatory R4-followup modifications (all baked into the design below):**
1. **Decay γ = 0.98 per day** (NOT γ=0.9 weekly — too aggressive, halves effective sample every 6.6 weeks). Matches Garivier-Moulines DS-UCB regime ([arXiv:2305.10718](https://arxiv.org/abs/2305.10718)).
2. **Pool-size stratification** — three buckets `{S: <30, M: 30-79, L: ≥80}`. Posteriors stratified per `(Kasten × arm × pool_bucket)` to remove the "larger Kasten → more competitors → lower survival" confound ([Russo TS Tutorial §6](https://web.stanford.edu/~bvr/pubs/TS_Tutorial.pdf)).
3. **Informative prior** from static-0.30 historical — seed `Beta(α=1+s, β=1+f)` from last 30 days of static-0.30 logs, scaled to total mass 4 (warm-start prior — [Knowledge & Information Systems 2024](https://link.springer.com/article/10.1007/s10115-023-01861-2)). NOT `Beta(2,2)` weak prior.
4. **Per-Kasten kill switch column** — `bandit_disabled_at TIMESTAMPTZ NULL` + `bandit_disabled_reason TEXT NULL` in `kg_kasten_metrics`. Single-Kasten misbehavior auto-reverts ONLY that Kasten without forcing global revert.

**Files:**
- New: `website/features/rag_pipeline/observability/anchor_seed_bandit.py` — sampling logic, posterior updates, decay job
- Modify: `supabase/migrations/iter12_kg_kasten_metrics.sql` (extend Task 7's migration — see Step 1)
- Modify: `website/features/rag_pipeline/retrieval/hybrid.py` — replace `_ANCHOR_SEED_FLOOR_RRF` lookup at L552/L638 with `bandit.sample_floor(p_user_id, kasten_id)`
- Modify: `website/features/rag_pipeline/retrieval/anchor_seed.py:22` — record `seed_survived` outcome after rerank
- Modify: `website/api/health.py` — surface bandit posterior + entropy for ops dashboard
- New: `ops/scripts/bandit_decay_job.py` — daily cron applying γ=0.98 to all (α, β) rows
- New: `ops/scripts/bandit_warm_start.py` — one-shot informative-prior backfill from last-30-days static-0.30 logs (run BEFORE first arm sample)
- Test: `tests/unit/rag/observability/test_anchor_seed_bandit.py` (NEW)
- Test: `tests/integration/test_bandit_concurrent_writes.py` (NEW — verify atomic UPSERT under 5 qps simulation)

**Position:** Phase 3 / Task 7b (after K4's `kg_kasten_metrics` migration). Day 1-3 telemetry-only; Day 4-7 canary 1; Day 8-14 canaries 2-3; Day 15+ all-Kasten.

- [ ] **Step 1: Extend `kg_kasten_metrics` schema (R4-followup mod 2 + 4).**

```sql
-- supabase/migrations/iter12_kg_kasten_metrics.sql — add to Task 7's migration
-- (reward stored per Kasten × arm × pool_bucket for stratification)
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_arm numeric;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_pool_bucket text  -- 'S', 'M', 'L'
  CHECK (seed_pool_bucket IS NULL OR seed_pool_bucket IN ('S','M','L'));
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_alpha numeric DEFAULT 1.0;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_beta numeric DEFAULT 1.0;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_last_decay_at timestamptz;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_total_pulls int DEFAULT 0;

-- R4-followup mod 4: per-Kasten kill switch
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS bandit_disabled_at timestamptz NULL;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS bandit_disabled_reason text NULL;

CREATE UNIQUE INDEX IF NOT EXISTS kg_kasten_metrics_bandit_key
  ON kg_kasten_metrics(p_user_id, kasten_id, seed_arm, seed_pool_bucket)
  WHERE seed_arm IS NOT NULL;
```

- [ ] **Step 2: Bandit module (R4-followup mods 1, 2, 3 + reward formulation A).**

```python
# website/features/rag_pipeline/observability/anchor_seed_bandit.py
"""iter-12 R4: per-Kasten Thompson-sampling bandit for anchor-seed floor.

Production-safety design (R4-followup websearch validated 2026-05-07):
- Decay γ=0.98/day (Garivier-Moulines DS-UCB regime; NOT 0.9 weekly).
- Pool-size stratification (S<30, M<80, L≥80) removes Kasten-size confound.
- Informative prior from static-0.30 historical (NOT Beta(2,2) weak prior).
- Per-Kasten kill switch column (NOT global env flag only).
- Per-request θ sampling (NOT cached — caching breaks TS exploration).
- Atomic INSERT...ON CONFLICT...DO UPDATE for concurrent writes.
- Hard floor at 0.25 preserves CE-reranker primacy.
- N_min=20 cold-start gate falls back to static 0.30.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
from typing import Any

from website.features.rag_pipeline.retrieval._async_helpers import rpc_call

_log = logging.getLogger("rag.anchor_seed_bandit")

_BANDIT_ENABLED = os.environ.get("RAG_ANCHOR_BANDIT_ENABLED", "true").lower() == "true"
_ARMS = [float(a) for a in os.environ.get(
    "RAG_ANCHOR_BANDIT_ARMS", "0.25,0.30,0.35,0.40"
).split(",")]
_N_MIN_PULLS = int(os.environ.get("RAG_ANCHOR_BANDIT_N_MIN", "20"))
_STATIC_FALLBACK = float(os.environ.get("RAG_ANCHOR_SEED_FLOOR_RRF", "0.30"))
_FINAL_TOP_K = int(os.environ.get("RAG_FINAL_TOP_K", "8"))


def bucket_pool_size(n: int) -> str:
    if n < 30:
        return "S"
    if n < 80:
        return "M"
    return "L"


async def sample_floor(
    *, p_user_id: str, kasten_id: str, pool_size: int, supabase
) -> tuple[float, dict]:
    """Returns (floor_value, telemetry_dict). Falls back to static when:
    - bandit globally disabled, OR
    - per-Kasten kill switch active, OR
    - cold-start (total pulls < N_MIN), OR
    - DB unreachable (fail-open).
    """
    bucket = bucket_pool_size(pool_size)
    telemetry = {
        "p_user_id": p_user_id, "kasten_id": kasten_id, "pool_bucket": bucket,
        "fallback_reason": None, "arm_sampled": None,
        "alpha_at_sample": None, "beta_at_sample": None,
        "theta_drawn": None, "posterior_entropy_nats": None,
    }
    if not _BANDIT_ENABLED:
        telemetry["fallback_reason"] = "global_flag_off"
        return _STATIC_FALLBACK, telemetry

    try:
        rows = await rpc_call(supabase.rpc(
            "rag_bandit_read_arms",
            {"p_user_id": p_user_id, "p_kasten_id": kasten_id, "p_bucket": bucket},
        ))
        rows = rows.data or []
    except Exception as exc:
        _log.warning("bandit_read_failed kasten=%s err=%s", kasten_id, type(exc).__name__)
        telemetry["fallback_reason"] = "db_unreachable"
        return _STATIC_FALLBACK, telemetry

    if not rows or rows[0].get("bandit_disabled_at"):
        telemetry["fallback_reason"] = "kasten_kill_switch" if rows else "no_rows"
        return _STATIC_FALLBACK, telemetry

    total_pulls = sum(int(r.get("seed_total_pulls") or 0) for r in rows)
    if total_pulls < _N_MIN_PULLS:
        telemetry["fallback_reason"] = "cold_start"
        return _STATIC_FALLBACK, telemetry

    # R4-followup mod: per-request θ draw (NOT cached).
    arm_to_params = {float(r["seed_arm"]): (float(r["seed_alpha"]), float(r["seed_beta"]))
                     for r in rows if r.get("seed_arm") is not None}
    samples = []
    for arm in _ARMS:
        a, b = arm_to_params.get(arm, (1.0, 1.0))  # missing arm → uniform prior
        theta = random.betavariate(a, b)
        samples.append((arm, theta, a, b))
    arm_chosen, theta_max, alpha_at, beta_at = max(samples, key=lambda x: x[1])

    # Posterior entropy (pathology metric for stuck-arm detection).
    arm_means = [a / (a + b) for _, _, a, b in samples]
    total = sum(arm_means) or 1.0
    probs = [m / total for m in arm_means]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)

    telemetry.update({
        "arm_sampled": arm_chosen,
        "alpha_at_sample": alpha_at,
        "beta_at_sample": beta_at,
        "theta_drawn": theta_max,
        "posterior_entropy_nats": entropy,
    })
    return arm_chosen, telemetry


async def record_outcome(
    *, p_user_id: str, kasten_id: str, arm: float, pool_bucket: str,
    seed_survived: bool, supabase,
) -> None:
    """Atomic UPSERT increment (R4-followup deliverable E)."""
    try:
        await rpc_call(supabase.rpc("rag_bandit_record_outcome", {
            "p_user_id": p_user_id, "p_kasten_id": kasten_id,
            "p_arm": arm, "p_bucket": pool_bucket,
            "p_reward": 1 if seed_survived else 0,
        }))
    except Exception as exc:
        _log.warning("bandit_record_failed kasten=%s err=%s", kasten_id, type(exc).__name__)
        # Fail-open: no behavior change on logging failure.
```

Plus matching SQL functions `rag_bandit_read_arms` (SELECT) and `rag_bandit_record_outcome` (INSERT...ON CONFLICT...DO UPDATE with atomic α/β increment) in the same migration file.

- [ ] **Step 3: Wire into hybrid.py** — replace static `_ANCHOR_SEED_FLOOR_RRF` reads at `hybrid.py:552, 638` with `arm, telemetry = await sample_floor(...)`. Pass `arm` as the floor; capture `telemetry` for log emission.

- [ ] **Step 4: Reward recording** — in `_dedup_and_fuse` AFTER rerank rank computed, for each injected seed:
  ```python
  for seed in injected_seeds:
      survived = seed.node_id in {c.node_id for c in reranked[:_FINAL_TOP_K]}
      await record_outcome(
          p_user_id=p_user_id, kasten_id=kasten_id,
          arm=seed.floor_arm, pool_bucket=bucket,
          seed_survived=survived, supabase=supabase,
      )
  ```

- [ ] **Step 5: Decay job** (`ops/scripts/bandit_decay_job.py`) — runs daily via existing cron infra:
  ```sql
  -- γ = 0.98 per day; data >180d has weight <0.025
  UPDATE kg_kasten_metrics
     SET seed_alpha = GREATEST(1.0, seed_alpha * 0.98),
         seed_beta  = GREATEST(1.0, seed_beta  * 0.98),
         seed_last_decay_at = now()
   WHERE seed_arm IS NOT NULL
     AND (seed_last_decay_at IS NULL OR seed_last_decay_at < now() - interval '23 hours');
  ```
  GREATEST clamp keeps Beta(α≥1, β≥1) so prior never collapses.

- [ ] **Step 6: Warm-start backfill** (`ops/scripts/bandit_warm_start.py`) — one-shot, run BEFORE first arm sample:
  - Read last 30 days of static-0.30 logs from droplet (filter by `seed_inject_floor=0.30`)
  - Per (Kasten, pool_bucket): aggregate (s = total survivors, f = total drops)
  - Scale to total mass 4: prior_alpha = 1 + 4 × s/(s+f), prior_beta = 1 + 4 × f/(s+f)
  - Insert into `kg_kasten_metrics` for ALL FOUR arms with the SAME warm-start prior (priors are seeded from observed performance, not from arm-specific history we don't have)

- [ ] **Step 7: Per-Kasten observables** — log line per query that injects seeds:
  ```
  anchor_seed_bandit qid=<hash> user_hash=<sha8> kasten_id=<uuid>
    pool_bucket=S|M|L pool_size=<int>
    arm_sampled=<float> alpha_at=<float> beta_at=<float>
    theta_drawn=<float> posterior_entropy_nats=<float>
    fallback_reason=<str|None>
    bandit_decision_latency_us=<int>
  ```
  Reward joined post-rerank: `anchor_seed_reward qid=<hash> arm=<float> survived=<bool>`.

- [ ] **Step 8: Pathology-detection metrics** (R4-followup deliverable D) — exposed at `/api/health` for ops dashboard:
  - `posterior_mode_flips_24h` — count `argmax(α/(α+β))` switches over rolling 24h, alert if `>3` after 50 pulls
  - `posterior_entropy_nats` — alert if `>1.3` after 200 pulls (uniform = 1.39; near-uniform = no learning)
  - `min_arm_pulls / max_arm_pulls` — starvation flag, alert if `<0.05` after 100 total
  - `bandit_decision_latency_p99_ms` — alert if `>5` (sampling overhead budget)
  - `db_upsert_conflict_rate` — alert if `>5%` (concurrent-write health)

- [ ] **Step 9: Auto-rollback rules (per-Kasten ONLY; never auto-global)** — implement in `ops/scripts/bandit_health_check.py` (cron every 1h):
  - `accuracy_user_visible` drops ≥ 2.0pp vs 14-day pre-bandit baseline over rolling 7-day → set `bandit_disabled_at = NOW()`, `bandit_disabled_reason = 'auto_accuracy_drop'`
  - `seed_inject_error_rate > 1%` over 24h → same with reason `auto_inject_errors`
  - `p95_retrieval_latency_ms` increases > 30ms vs baseline → same with reason `auto_latency_regression`
  - **Auto-global revert is FORBIDDEN per CLAUDE.md guardrails — requires operator approval.**

- [ ] **Step 10: Acceptance gate (canary → all)** — extend bandit beyond canaries only when ALL hold for ≥7 consecutive days:
  1. Per-canary `accuracy_user_visible` ≥ baseline + 0.5pp (or within ±0.5pp if no degradation tolerance)
  2. Zero auto-rollbacks triggered
  3. ≥2 arms have ≥30 effective pulls per canary (`α+β−2 ≥ 30` after decay)
  4. Posterior entropy `<1.0 nats` on ≥1 canary (evidence of learning)
  5. Operator review of dashboard

- [ ] **Step 11: Two-week ramp plan** (operator follows this calendar):
  - **Day 1-3:** telemetry-only on all Kastens; decision = static 0.30 (validates DB writes, decay job, dashboard)
  - **Day 4-7:** activate bandit on **canary 1** (KM Kasten, sparse English); rest static
  - **Day 8-10:** if green, add **canary 2** (dense single-author phenotype if available; else single-topic)
  - **Day 11-14:** add **canary 3** (multi-topic English)
  - **Day 15+:** extend to all on operator approval per acceptance gate above

- [ ] **Step 12: Tests + commit.**

```bash
pytest tests/unit/rag/observability/test_anchor_seed_bandit.py \
       tests/integration/test_bandit_concurrent_writes.py -q
git add website/features/rag_pipeline/observability/anchor_seed_bandit.py \
        supabase/migrations/iter12_kg_kasten_metrics.sql \
        website/features/rag_pipeline/retrieval/hybrid.py \
        website/features/rag_pipeline/retrieval/anchor_seed.py \
        website/api/health.py \
        ops/scripts/bandit_decay_job.py ops/scripts/bandit_warm_start.py \
        ops/scripts/bandit_health_check.py \
        tests/unit/rag/observability/test_anchor_seed_bandit.py \
        tests/integration/test_bandit_concurrent_writes.py
git commit -m "feat: thompson sampling bandit for anchor seed floor"
```

#### Caveats & rollback

**Risk-mitigation:**
- **DB-unreachable fail-open** → falls back to static 0.30; bandit decision latency 5ms hard cap (Step 8) prevents query stall.
- **Cold-start over-fitting** → N_min=20 gate per Kasten; warm-start prior (Step 6) reduces gate's exposure window.
- **Concurrent reward races** → atomic Postgres `INSERT ... ON CONFLICT ... DO UPDATE` with row-level lock; SELECT-then-UPDATE explicitly forbidden in code.
- **Posterior pathology** → 5 alerts in Step 8 + auto-rollback rules in Step 9 catch stuck-arm / oscillation / starvation.
- **Multi-tenant isolation** → primary key `(p_user_id, kasten_id, seed_arm, seed_pool_bucket)`; one user's bandit never reads another's posterior.

**Edge cases:**
- **Brand-new Kasten (<5 zettels):** N_min=20 gate fires → static 0.30 fallback. Eventually bandit activates as Kasten grows; pool-bucket stratification handles size transitions.
- **Multi-language non-Latin Kasten:** R4-followup verdict says DON'T blanket-suspend bandit on script; instead suspend ONLY if entity-extractor confidence < 0.5 (Task 29 R6 confidence floor enforces this naturally — entities are filtered before reward joins).
- **Query that injects 0 seeds:** no reward recorded; posteriors unchanged. Bandit only learns from seed-injecting queries (correct).
- **Single-author Kasten where bandit converges to 0.40:** intended behavior — anti-magnet; floor at 0.40 prevents over-correction (hard floor 0.25 enforced by arm set).

**Rollback paths (rank-ordered, lightest first):**
1. **Per-Kasten kill** (Step 9 auto OR operator manual) — `UPDATE kg_kasten_metrics SET bandit_disabled_at=NOW() WHERE kasten_id=$1` → that Kasten reverts to static 0.30; others continue. **Reversible.**
2. **Global env flag** — `RAG_ANCHOR_BANDIT_ENABLED=false` in `STATIC_BODY` (Task 23) + redeploy → all Kastens revert to static 0.30; bandit code remains in place. **Reversible.**
3. **Schema rollback** — drop the new columns from `kg_kasten_metrics` (the migration is additive; columns are NULLable). **Reversible per CLAUDE.md.**
4. **Code revert** — `git revert` the bandit commit. Last resort; operator-approved.

**Citations:**
- [Russo et al. — Thompson Sampling Tutorial (Stanford)](https://web.stanford.edu/~bvr/pubs/TS_Tutorial.pdf)
- [Discounted TS for Non-Stationary Bandits arXiv:2305.10718](https://arxiv.org/abs/2305.10718)
- [TS for Non-Stationary Bandit Problems (MDPI Entropy)](https://www.mdpi.com/1099-4300/27/1/51)
- [Spotify BaRT — Explore, Exploit, Explain (RecSys 2018)](https://research.atspotify.com/publications/explore-exploit-explain-personalizing-explainable-recommendations-with-bandits)
- [Carousel Personalization with Contextual Bandits arXiv:2009.06546](https://arxiv.org/pdf/2009.06546)
- [Warm-start Contextual Bandits (KIS 2024)](https://link.springer.com/article/10.1007/s10115-023-01861-2)
- [LaunchDarkly — MAB + Guarded Releases](https://launchdarkly.com/docs/home/multi-armed-bandits)
- [MS Research — Identifying Outlier Arms in MAB (NeurIPS 2017)](https://www.microsoft.com/en-us/research/uploads/prod/2017/12/NIPS2017outlierarm.pdf)
- [AutoRAG-HP arxiv 2406.19251](https://arxiv.org/abs/2406.19251)

---

### Task 32: R2 — Q5 revision: slot-1 anchor pin + percentile-derived demote factor (SUPERSEDES Task 26's static 0.85→0.75 fallback)

**Why:** R2 cross-Kasten audit showed (a) the static `_SCORE_RANK_DEMOTE_FACTOR` 0.85→0.75 over-demotes legitimate same-topic siblings on dense single-topic Kastens, (b) the slot-1 magnet-wins risk on q5 is best fixed by **pinning slot-1 to the anchored top-1 OUTSIDE xQuAD**, NOT by zeroing overlap inside the picker. Industry validation: [Elasticsearch query rules `pinned_query`](https://www.elastic.co/search-labs/blog/elasticsearch-query-rules-generally-available), [Algolia Dynamic Re-Ranking](https://www.algolia.com/doc/guides/algolia-ai/re-ranking) two-phase boost-then-pin, [Ranking with Slot Constraints (Castells et al.)](https://synthical.com/article/a97474b9-308b-45a9-a574-4bf72f75b0c0).

**Replaces Task 26.** Task 26's "track demote-margin telemetry" is preserved (still useful for iter-13 calibration); the conditional `0.85→0.75` flip is removed.

**Files:**
- Modify: `website/features/rag_pipeline/retrieval/hybrid.py` — new helper `_pick_anchor_pin`; insert before `_xquad_select` call at L834; replace `_SCORE_RANK_DEMOTE_FACTOR` constant with percentile-derived computation
- Test: extend `tests/unit/rag/retrieval/test_title_overlap_percentile.py` + `test_class_x_source_matrix.py`

**Position:** Phase 5 / Task 9 augment.

- [ ] **Step 1: Slot-1 anchor pin.**

```python
# website/features/rag_pipeline/retrieval/hybrid.py
def _pick_anchor_pin(
    candidates: list[RetrievalCandidate],
    anchor_neighbours: set[str],
    *,
    evidence_floor: float = 0.05,
) -> RetrievalCandidate | None:
    """iter-12 R2 (slot-constraint pattern): pick the highest-rrf anchored
    candidate whose title-overlap-boost crosses the evidence floor. Returns
    None if no candidate qualifies (vanilla xQuAD fallback).
    """
    qualifying = [
        c for c in candidates
        if c.node_id in anchor_neighbours
        and float(c.metadata.get("_title_overlap_boost", 0.0)) >= evidence_floor
    ]
    if not qualifying:
        return None
    return max(qualifying, key=lambda c: c.rrf_score)
```

In `_dedup_and_fuse` BEFORE `_xquad_select`:

```python
pin = _pick_anchor_pin(ordered, anchor_neighbours or set(), evidence_floor=0.05)
if pin is not None:
    ordered_rest = [c for c in ordered if c is not pin]
    selected = [pin] + _xquad_select(ordered_rest, lam=_xquad_lambda_for_class(query_class))
else:
    selected = _xquad_select(ordered, lam=_xquad_lambda_for_class(query_class))
```

Cap pin to slot-1 only — preserves multi-anchor compare q10 behavior (anchors 2/3/N still compete via diversity).

- [ ] **Step 2: Percentile-derived demote factor** (replaces static 0.85):

```python
# website/features/rag_pipeline/retrieval/hybrid.py
import os

_DEMOTE_SLOPE = float(os.environ.get("RAG_SCORE_RANK_DEMOTE_SLOPE", "0.20"))


def _demote_factor_for_candidate(c: RetrievalCandidate, base_rrf_pool: list[float]) -> float:
    """Percentile-derived demote factor.

    Magnet at the very top of the rrf-pool gets a gentle factor (~0.90);
    magnet near the median gets a firmer factor (~0.75). One slope knob
    instead of a brittle absolute number — auto-scales per query.
    """
    base = float(c.metadata.get("_base_rrf_score", c.rrf_score))
    n = len(base_rrf_pool)
    if n == 0:
        return 0.85  # legacy fallback
    rank_above = sum(1 for s in base_rrf_pool if s <= base) / n  # percentile
    # rank=1.0 (top) -> factor 0.90; rank=0.0 (bottom) -> factor 0.70
    factor = max(0.70, min(0.90, 1.0 - _DEMOTE_SLOPE * (1.0 - rank_above)))
    return factor
```

In `_apply_score_rank_demote`:

```python
base_pool = [float(c.metadata.get("_base_rrf_score", c.rrf_score)) for c in candidates]
for c in candidates:
    # ... existing exemption checks (Q5 percentile, anchored)
    if delta >= delta_threshold:
        c.rrf_score *= _demote_factor_for_candidate(c, base_pool)
```

- [ ] **Step 3: New env knob.** Replaces `RAG_SCORE_RANK_DEMOTE_FACTOR` with `RAG_SCORE_RANK_DEMOTE_SLOPE=0.20`. Add to STATIC_BODY (Task 23) and `.env.example`.

- [ ] **Step 4: Tests** — extend `test_title_overlap_percentile.py`:
  - Sparse Kasten (gate skip): pin = no-op (anchor_neighbours empty).
  - Dense single-topic: anchored magnet pinned slot-1; siblings demoted by their pool percentile (mid-pool gets firm factor ~0.75; top-pool gets gentle ~0.90).
  - Compare q10: cap pin = 1; anchors 2/3 still compete via diversity.
  - Anchor-mismatch (anchor resolves but evidence floor not crossed): pin = None → vanilla path.

- [ ] **Step 5: Telemetry retained from Task 26** — keep the `score_rank_demote ... margin=...` log line; iter-13 can read it to validate the percentile-slope's empirical performance.

- [ ] **Step 6: Tests + commit.**

```bash
pytest tests/unit/rag/retrieval/ tests/unit/rag/integration/ -q
git add website/features/rag_pipeline/retrieval/hybrid.py \
        tests/unit/rag/retrieval/test_title_overlap_percentile.py \
        tests/unit/rag/integration/test_class_x_source_matrix.py
git commit -m "feat: slot1 anchor pin plus percentile demote slope"
```

**Citations:**
- [Elasticsearch query rules GA](https://www.elastic.co/search-labs/blog/elasticsearch-query-rules-generally-available)
- [Algolia Dynamic Re-Ranking](https://www.algolia.com/doc/guides/algolia-ai/re-ranking)
- [Carbonell & Goldstein 1998 MMR](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf)
- [Ranking with Slot Constraints (Castells et al.)](https://synthical.com/article/a97474b9-308b-45a9-a574-4bf72f75b0c0)
- [Qdrant MMR diversity](https://qdrant.tech/blog/mmr-diversity-aware-reranking/)
- [Pinecone rerankers two-stage](https://www.pinecone.io/learn/series/rag/rerankers/)

**Anti-pattern guards:**
- DO NOT zero overlap inside `_xquad_select` (R2 audit-rejected; harmful for compare q10).
- DO NOT cap pin > 1 (compare-shape multi-anchor must compete via diversity).
- DO NOT use a static 0.75 demote factor (over-demotes single-topic siblings).

#### Caveats & rollback

**Risk-mitigation:**
- **Slot-1 pin over-promotes anchored-but-irrelevant candidate** when anchor resolves but content doesn't match query semantically. Mitigation: `evidence_floor=0.05` requires a positive `_title_overlap_boost` — pure anchor-only matches are NOT pinned.
- **Percentile-derived demote factor unstable on tiny pools** (n=4-5 candidates → percentile is coarse). Mitigation: when `len(candidates) < 4`, gate already short-circuits (existing behavior). For `n=4-7`, percentile ranks are still stable enough; tested in `test_class_x_source_matrix.py` fixtures.
- **Slope knob `RAG_SCORE_RANK_DEMOTE_SLOPE=0.20`** is a single dial vs static 0.75 (a single absolute). Slope is more robust to Kasten-shape variation but operator must understand the formula. Document inline + add a "safe ranges" comment: `slope ∈ [0.10, 0.30]`; clamp factor in [0.70, 0.90] regardless.
- **Margin telemetry from Task 26 carries over** — emit `score_rank_demote ... margin=...` log line so iter-13 can validate that the percentile-slope's empirical performance matches predictions.
- **xQuAD slot-1 logic interacts with K3 confidence-gap bypass** (Task 6): if K3 fires (top1/top2 gap ≥ 1.5×), the entire score-rank gate is bypassed AND slot-1 pin runs against the unmodified pool. Order: K3 bypass → pin check → percentile demote → xQuAD. Document this control flow in `_dedup_and_fuse` header comment.

**Edge cases:**
- **Anchor resolves but the anchored candidate is NOT in retrieved pool** (rare; resolver returned node_id but hybrid retrieval didn't surface it within `limit`): `_pick_anchor_pin` filters on `c.node_id in anchor_neighbours` AND presence in candidates; if anchor not in pool, `qualifying=[]` → returns None → vanilla xQuAD. **DO NOT inject anchor into pool** — that's anchor-seed inject's job (Task 31), not xQuAD pinning.
- **All candidates have `rrf_score = 0`** (rare; cold-start or query-empty): `max(qualifying, key=lambda c: c.rrf_score)` returns deterministic-by-stable-sort first; no division. Percentile fallback to legacy 0.85 when `len(base_pool) == 0`.
- **Single-candidate pool (n=1)**: gate skips (existing `len < 4` check); pin would degenerate to argmax(1 element) — harmless.
- **Two anchored candidates tied on rrf**: stable-sort pick — first by insertion order. Acceptable; iter-13 K3-extension may add tie-break by `_title_overlap_boost`.
- **Anchor candidate has `_title_overlap_boost = 0` exactly** (resolved by name only, no title match): under `evidence_floor=0.05` does NOT qualify → no pin. Vanilla xQuAD applies. Per R2 audit recommendation (anchor alone ≠ "earned" pin without evidence).

**Rollback paths (rank-ordered, lightest first):**
1. **Disable slot-1 pin** — env flag `RAG_SLOT1_ANCHOR_PIN_ENABLED=false` in `STATIC_BODY`. xQuAD runs vanilla. Percentile demote remains active. **Reversible.**
2. **Revert demote to static 0.85** — env flag `RAG_SCORE_RANK_DEMOTE_SLOPE=0.0` (formula collapses to `factor = 1 − 0.0 × (1 − pct) = 1.0`); OR add legacy fallback `if slope == 0: factor = 0.85`. **Reversible — env-only.**
3. **Roll back to iter-11 binary exemption** — env flag `RAG_TITLE_OVERLAP_PERCENTILE=0` collapses Q5 to "any boost > 0 exempts" (iter-11 behavior). Loses Q5's q5-fix. **Reversible.**
4. **Code revert** — `git revert` the slot-pin + percentile-demote commit. Last resort; operator-approved.

---

### Task 35: R1 monitors + per-Kasten CE-distribution telemetry (empirical gate for iter-13 floor decision)

**Why:** R1 surfaced three industry-standard dynamic alternatives to lowering `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR` (per-Kasten p70 floor / NLI voting / conformal abstention). All three are deferred to iter-13+, but iter-12 must ship the **monitors** that decide which (if any) iter-13 should activate. Monitors are pure logging + 1 audit script — zero behavior change.

**Files:**
- Modify: `website/features/rag_pipeline/orchestrator.py` — emit `ce_score_distribution` log line per query in `_finalize_answer`
- Modify: `ops/scripts/eval_iter_03_playwright.py` — surface `retrieval_pool_size_initial`, `retrieval_pool_size_retry`, `retry_outcome_class`, `t_db_wait_ms`, `t_rerank_ms`, `t_synth_ms` in `verification_results.json` per query
- Modify: `website/features/rag_pipeline/orchestrator.py` — set `retry_outcome_class` enum on retry path
- New: `ops/scripts/audit_ce_distribution.py` — iter-13 empirical-gate runner
- Test: `tests/unit/rag/test_orchestrator_telemetry.py` (NEW)

**Position:** Phase 7 / extends Class S (Task 14). Lands together with the scoring fixes since all five monitors feed into `verification_results.json`.

- [ ] **Step 1: Write the failing test for `retry_outcome_class` enum.**

```python
# tests/unit/rag/test_orchestrator_telemetry.py — new
"""iter-12 Task 35: per-query telemetry for iter-13 empirical gate."""
import pytest
from website.features.rag_pipeline.orchestrator import RetryOutcomeClass, classify_retry_outcome


def test_classify_empty_pool():
    assert classify_retry_outcome(
        retrieved_count=0, retry_fired=True, timed_out=False,
        critic_verdict="retry_budget_exceeded",
    ) == RetryOutcomeClass.EMPTY_POOL


def test_classify_timeout():
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=True,
        critic_verdict="retry_budget_exceeded",
    ) == RetryOutcomeClass.TIMEOUT


def test_classify_floor_failed():
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=False,
        critic_verdict="unsupported_with_gold_skip",
    ) == RetryOutcomeClass.FLOOR_FAILED


def test_classify_success():
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=False, timed_out=False,
        critic_verdict="supported",
    ) == RetryOutcomeClass.SUCCESS


def test_classify_still_unsupported():
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=False,
        critic_verdict="unsupported_no_retry",
    ) == RetryOutcomeClass.STILL_UNSUPPORTED
```

- [ ] **Step 2: Run test to verify it fails.**

```bash
pytest tests/unit/rag/test_orchestrator_telemetry.py::test_classify_empty_pool -v
```

Expected: FAIL with `ImportError: cannot import name 'RetryOutcomeClass'` (enum doesn't exist yet).

- [ ] **Step 3: Implement enum + classifier.**

```python
# website/features/rag_pipeline/orchestrator.py — add at module top
import enum


class RetryOutcomeClass(str, enum.Enum):
    """iter-12 Task 35 — per-query retry outcome label for iter-13 gate.

    SUCCESS:           first pass produced supported answer; no retry
    EMPTY_POOL:        retrieval (initial or retry) returned 0 candidates
    TIMEOUT:           retry budget exceeded
    FLOOR_FAILED:      pool present but critic refused via floor gate
    STILL_UNSUPPORTED: retry produced no improvement
    """
    SUCCESS = "success"
    EMPTY_POOL = "empty_pool"
    TIMEOUT = "timeout"
    FLOOR_FAILED = "floor_failed"
    STILL_UNSUPPORTED = "still_unsupported"


def classify_retry_outcome(
    *, retrieved_count: int, retry_fired: bool, timed_out: bool,
    critic_verdict: str,
) -> RetryOutcomeClass:
    if retrieved_count == 0:
        return RetryOutcomeClass.EMPTY_POOL
    if timed_out:
        return RetryOutcomeClass.TIMEOUT
    if critic_verdict in ("unsupported_with_gold_skip",):
        return RetryOutcomeClass.FLOOR_FAILED
    if critic_verdict in ("unsupported_no_retry",) and retry_fired:
        return RetryOutcomeClass.STILL_UNSUPPORTED
    return RetryOutcomeClass.SUCCESS
```

- [ ] **Step 4: Run test to verify it passes.**

```bash
pytest tests/unit/rag/test_orchestrator_telemetry.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Wire `retry_outcome_class` into the per-query response.**

In `_finalize_answer` (`orchestrator.py:~989`), capture and emit:

```python
# After verdict is finalized
retry_outcome = classify_retry_outcome(
    retrieved_count=len(used_candidates),
    retry_fired=retry_count > 0,
    timed_out=(verdict == "retry_budget_exceeded"),
    critic_verdict=verdict,
)
result["retry_outcome_class"] = retry_outcome.value
```

- [ ] **Step 6: Emit `ce_score_distribution` log line per query.**

In `_finalize_answer`, BEFORE returning:

```python
ce_scores = [
    float(c.metadata.get("rerank_score", 0.0)) for c in pre_validation_candidates
]
if ce_scores:
    _log.info(
        "ce_score_distribution kasten_id=%s query_class=%s phase=%s "
        "n=%d top1=%.4f top3=%.4f median=%.4f p70=%.4f",
        kasten_id, getattr(query_class, "value", "unknown"),
        "retry" if retry_count > 0 else "initial",
        len(ce_scores),
        max(ce_scores),
        sorted(ce_scores, reverse=True)[2] if len(ce_scores) >= 3 else max(ce_scores),
        sorted(ce_scores)[len(ce_scores) // 2],
        sorted(ce_scores)[int(0.7 * len(ce_scores))] if len(ce_scores) >= 4 else max(ce_scores),
    )
```

- [ ] **Step 7: Surface monitor fields in `verification_results.json`.**

Edit `ops/scripts/eval_iter_03_playwright.py` `phase_rag_qa_chain` to include in each per-query result dict:

```python
result["retrieval_pool_size_initial"] = turn.get("pool_size_initial", 0)
result["retrieval_pool_size_retry"] = turn.get("pool_size_retry", 0)
result["retry_outcome_class"] = turn.get("retry_outcome_class", "success")
result["t_db_wait_ms"] = turn.get("t_db_wait_ms")  # may be None pre-Class P
result["t_rerank_ms"] = turn.get("t_rerank_ms")
result["t_synth_ms"] = turn.get("t_synth_ms")
```

The server-side `turn` payload must include those fields. Modify the SSE final-event payload in the API handler to expose them (already in iter-10 P17 logs; surface to JSON).

- [ ] **Step 8: Write the failing test for `audit_ce_distribution.py` gate logic.**

```python
# tests/unit/ops_scripts/test_audit_ce_distribution.py — new
import pytest
from ops.scripts.audit_ce_distribution import iter_13_a1_gate


def test_gate_activates_when_spread_high_and_min_low():
    samples = {
        "kasten_a": [0.45] * 50,
        "kasten_b": [0.75] * 50,
        "kasten_c": [0.70] * 50,
    }
    assert iter_13_a1_gate(samples) == "ACTIVATE_A1_PER_KASTEN_FLOOR"


def test_gate_closes_when_clustered():
    samples = {
        "kasten_a": [0.70] * 50,
        "kasten_b": [0.71] * 50,
        "kasten_c": [0.69] * 50,
    }
    assert iter_13_a1_gate(samples) == "CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT"


def test_gate_defers_when_insufficient_data():
    samples = {"kasten_a": [0.70] * 50, "kasten_b": [0.70] * 49}
    assert iter_13_a1_gate(samples) == "DEFER_TO_ITER_14_INSUFFICIENT_DATA"
```

- [ ] **Step 9: Run; verify failure.**

```bash
pytest tests/unit/ops_scripts/test_audit_ce_distribution.py -v
```

Expected: FAIL with import error.

- [ ] **Step 10: Implement audit script.**

```python
# ops/scripts/audit_ce_distribution.py — new
"""iter-12 Task 35: empirical gate for iter-13 per-Kasten floor decision.

Reads per-Kasten p70 of CE scores from the rolling 200-sample window
emitted by `ce_score_distribution` log lines. Decision rule:

  - spread > 0.05 AND any p70 < 0.65       -> ACTIVATE_A1_PER_KASTEN_FLOOR
  - spread <= 0.05 AND all p70 in [0.65, 0.75] -> CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT
  - <3 Kastens with >=50 samples           -> DEFER_TO_ITER_14_INSUFFICIENT_DATA
  - otherwise                              -> INCONCLUSIVE_LOG_AND_REPEAT_NEXT_ITER

Usage:
    python ops/scripts/audit_ce_distribution.py --logs droplet_logs.json \
                                                --output _audit/ce_gate.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from typing import Any


def iter_13_a1_gate(kasten_p70_samples: dict[str, list[float]]) -> str:
    qualified = {k: s for k, s in kasten_p70_samples.items() if len(s) >= 50}
    if len(qualified) < 3:
        return "DEFER_TO_ITER_14_INSUFFICIENT_DATA"
    p70s = []
    for samples in qualified.values():
        sorted_s = sorted(samples)
        p70s.append(sorted_s[int(0.7 * len(sorted_s))])
    spread = max(p70s) - min(p70s)
    if spread > 0.05 and min(p70s) < 0.65:
        return "ACTIVATE_A1_PER_KASTEN_FLOOR"
    if spread <= 0.05 and all(0.65 <= p <= 0.75 for p in p70s):
        return "CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT"
    return "INCONCLUSIVE_LOG_AND_REPEAT_NEXT_ITER"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True, help="JSONL of ce_score_distribution events")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    samples: dict[str, list[float]] = {}
    with open(args.logs) as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "ce_score_distribution":
                continue
            kasten = event.get("kasten_id")
            p70 = event.get("p70")
            if kasten and p70 is not None:
                samples.setdefault(kasten, []).append(float(p70))

    decision = iter_13_a1_gate(samples)
    out = {
        "decision": decision,
        "kasten_count": len(samples),
        "qualified_kasten_count": sum(1 for s in samples.values() if len(s) >= 50),
        "kasten_p70_summary": {
            k: {"n": len(s), "p70": sorted(s)[int(0.7 * len(s))] if len(s) >= 50 else None}
            for k, s in samples.items()
        },
    }
    with open(args.output, "w") as f:
        json.dump(out, f, indent=2)
    print(f"decision={decision} qualified_kastens={out['qualified_kasten_count']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 11: Run audit-gate tests.**

```bash
pytest tests/unit/ops_scripts/test_audit_ce_distribution.py -v
```

Expected: 3 passed.

- [ ] **Step 12: Run full unit-test suite to confirm no regressions.**

```bash
pytest tests/unit/rag/test_orchestrator_telemetry.py tests/unit/ops_scripts/ -q
```

Expected: all passing.

- [ ] **Step 13: Commit.**

```bash
git add website/features/rag_pipeline/orchestrator.py \
        ops/scripts/eval_iter_03_playwright.py \
        ops/scripts/audit_ce_distribution.py \
        tests/unit/rag/test_orchestrator_telemetry.py \
        tests/unit/ops_scripts/test_audit_ce_distribution.py
git commit -m "feat: r1 telemetry plus iter13 empirical gate"
```

#### Caveats & rollback

**Risk-mitigation:**
- **Telemetry-only — zero behavior change.** Five monitors are pure log emissions + JSON field additions; cannot regress runtime behavior.
- **Empirical gate is advisory** — `audit_ce_distribution.py` outputs a decision string; iter-13 plan author reads it. No code branches on the gate output during iter-12.
- **Sample window** (200 per Kasten) tuned for ~7 days of typical traffic; adjust via `--window` flag if traffic skewed.

**Edge cases:**
- **Sparse Kasten with <50 queries during iter-12 deployment:** gate emits `DEFER_TO_ITER_14_INSUFFICIENT_DATA`. iter-13 author may keep collecting OR accept the static floor for that phenotype.
- **All Kastens have empty pools (Class P deploy regression):** `ce_scores=[]` → log line skipped (Step 6 conditional); audit script's `samples` dict stays empty → DEFER decision.
- **Log line missing fields** (older log format): audit script's `event.get(...)` defaults to None → skipped row. Backwards-compatible.

**Rollback paths:**
1. **Disable monitor logging** — env flag `RAG_TELEMETRY_R1_ENABLED=false` would gate the log emission (optional; not required for safety since logs are inert).
2. **Remove JSON fields** — revert `ops/scripts/eval_iter_03_playwright.py` changes; legacy consumers unaffected (new fields are additive).
3. **Skip audit run** — don't call `audit_ce_distribution.py` post-iter; nothing breaks.
4. **Code revert** — `git revert` the telemetry commit. Last resort.

**Citations:**
- [TARG: Training-Free Adaptive Retrieval Gating arXiv:2511.09803](https://arxiv.org/html/2511.09803v1)
- [Elastic BEIR primer (per-corpus calibration)](https://www.elastic.co/search-labs/blog/evaluating-search-relevance-part-1)

---

### Task 36: post-iter audit script (one-shot evaluator combining scores + runtime/memory + failed-query forensic + monitor surfacing)

**Why:** the operator runs `eval_iter_03_playwright.py` then `score_rag_eval.py` then manually inspects logs / scores.md / verification_results.json to triage. **A single post-iter audit script** consolidates all checks into one report so iter-12+ findings are reproducible and auditable. Lands as `ops/scripts/post_iter_audit.py`. Idempotent — safe to re-run.

**Files:**
- New: `ops/scripts/post_iter_audit.py`
- New: `ops/scripts/_audit/templates/audit_report.md.j2` (Jinja2 template; if Jinja not available, plain f-string fallback)
- Test: `tests/unit/ops_scripts/test_post_iter_audit.py` (NEW)

**Position:** Phase 8 / Task 22 follow-up. Runs AFTER `score_rag_eval.py` produces `scores.md`. Operator invokes manually:
```bash
python ops/scripts/post_iter_audit.py --iter iter-12
```

- [ ] **Step 1: Write the failing test for the audit aggregator.**

```python
# tests/unit/ops_scripts/test_post_iter_audit.py — new
"""iter-12 Task 36: post-iter audit script."""
import json
import tempfile
from pathlib import Path
import pytest
from ops.scripts.post_iter_audit import run_audit, AuditFindings


def test_audit_aggregates_scores_and_failures(tmp_path):
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text(
        "# 06 Scorecard\n**Composite:** 88.50\n## Holistic monitoring (iter-12 trust-first)\n"
        "- accuracy_user_visible: 0.9231\n- over_refusal_rate: 0.0769\n"
        "- under_refusal_rate: 0.0500\n"
    )
    (iter_dir / "verification_results.json").write_text(json.dumps({
        "iter": "iter-12",
        "qa_summary": {"total": 14, "accuracy_user_visible": 0.9231, "n_scored": 13},
        "phases": [{
            "phase": "rag_qa_chain",
            "checks": [
                {"name": "Q-A q1", "passed": True, "detail": {
                    "qid": "q1", "expected": ["gh-zk-org-zk"],
                    "primary_citation": "gh-zk-org-zk", "refused": False, "over_refusal": False,
                    "gold_at_1": True, "retry_outcome_class": "success",
                    "t_db_wait_ms": 120, "t_rerank_ms": 80, "t_synth_ms": 1100,
                    "p_user_complete_ms": 8500,
                }},
                {"name": "Q-A q5", "passed": False, "detail": {
                    "qid": "q5", "expected": ["yt-walker", "nl-pragmatic"],
                    "primary_citation": "gh-zk-org-zk", "refused": False, "over_refusal": False,
                    "gold_at_1": False, "retry_outcome_class": "still_unsupported",
                    "t_db_wait_ms": 180, "t_rerank_ms": 120, "t_synth_ms": 2200,
                    "p_user_complete_ms": 14000,
                }},
            ],
        }],
    }))

    findings = run_audit(iter_dir)
    assert findings.composite == 88.50
    assert findings.accuracy_user_visible == 0.9231
    assert len(findings.failed_gold_at_1) == 1
    assert findings.failed_gold_at_1[0]["qid"] == "q5"
    assert findings.failed_gold_at_1[0]["primary_citation"] == "gh-zk-org-zk"
    assert findings.failed_gold_at_1[0]["retry_outcome_class"] == "still_unsupported"


def test_audit_handles_missing_files(tmp_path):
    iter_dir = tmp_path / "iter-empty"
    iter_dir.mkdir()
    findings = run_audit(iter_dir)
    assert findings.composite is None
    assert findings.failed_gold_at_1 == []


def test_audit_writes_report(tmp_path):
    iter_dir = tmp_path / "iter-12"
    iter_dir.mkdir()
    (iter_dir / "scores.md").write_text("**Composite:** 75.00\n")
    (iter_dir / "verification_results.json").write_text(json.dumps({
        "qa_summary": {"total": 1, "accuracy_user_visible": 0.5},
        "phases": [],
    }))
    findings = run_audit(iter_dir)
    report_path = iter_dir / "post_iter_audit.md"
    findings.write_report(report_path)
    assert report_path.exists()
    text = report_path.read_text()
    assert "Composite" in text and "75.00" in text
```

- [ ] **Step 2: Run; verify failure.**

```bash
pytest tests/unit/ops_scripts/test_post_iter_audit.py -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement audit script.**

```python
# ops/scripts/post_iter_audit.py — new
"""iter-12 Task 36: post-iter audit aggregator.

Reads scores.md + verification_results.json + droplet logs (if available)
and writes a single `post_iter_audit.md` report summarizing:

  1. Composite + headline metrics from scores.md
  2. Per-stage runtime + memory from droplet logs (P17 timing)
  3. Failed gold@1 queries with forensic detail
  4. Status of every monitor we added in iter-12 (Tasks 14, 25, 30, 31, 35)

Usage:
    python ops/scripts/post_iter_audit.py --iter iter-12
    python ops/scripts/post_iter_audit.py --iter-dir docs/rag_eval/common/knowledge-management/iter-12
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditFindings:
    composite: float | None = None
    accuracy_user_visible: float | None = None
    over_refusal_rate: float | None = None
    under_refusal_rate: float | None = None
    within_budget_rate: float | None = None
    failed_gold_at_1: list[dict] = field(default_factory=list)
    over_budget_queries: list[dict] = field(default_factory=list)
    refused_queries: list[dict] = field(default_factory=list)
    per_stage_timing: dict[str, dict[str, float]] = field(default_factory=dict)
    monitor_status: dict[str, str] = field(default_factory=dict)
    burst_502_rate: float | None = None
    burst_503_rate: float | None = None
    notes: list[str] = field(default_factory=list)

    def write_report(self, path: Path) -> None:
        lines = ["# Post-iter audit report", ""]
        lines.append(f"## 1. Scores")
        if self.composite is not None:
            lines.append(f"- **Composite:** {self.composite:.2f}")
        if self.accuracy_user_visible is not None:
            lines.append(f"- **accuracy_user_visible:** {self.accuracy_user_visible:.4f}")
        if self.over_refusal_rate is not None:
            lines.append(f"- **over_refusal_rate:** {self.over_refusal_rate:.4f}")
        if self.under_refusal_rate is not None:
            lines.append(f"- **under_refusal_rate:** {self.under_refusal_rate:.4f}")
        if self.within_budget_rate is not None:
            lines.append(f"- **within_budget_rate:** {self.within_budget_rate:.4f}")
        if self.burst_502_rate is not None:
            lines.append(f"- **burst_502_rate:** {self.burst_502_rate:.4f} (target 0.0)")
        if self.burst_503_rate is not None:
            lines.append(f"- **burst_503_rate:** {self.burst_503_rate:.4f} (target ≥0.08)")
        lines.append("")
        lines.append(f"## 2. Per-stage runtime + memory")
        if self.per_stage_timing:
            lines.append("| qid | t_db_wait_ms | t_rerank_ms | t_synth_ms | p_user_complete_ms |")
            lines.append("|---|---:|---:|---:|---:|")
            for qid, t in self.per_stage_timing.items():
                lines.append(
                    f"| {qid} | {t.get('t_db_wait_ms','—')} | {t.get('t_rerank_ms','—')} | "
                    f"{t.get('t_synth_ms','—')} | {t.get('p_user_complete_ms','—')} |"
                )
        else:
            lines.append("_No per-stage timing in verification_results.json — check Class P log artifact._")
        lines.append("")
        lines.append(f"## 3. Failed gold@1 queries (with forensic)")
        if not self.failed_gold_at_1:
            lines.append("_None._")
        else:
            for q in self.failed_gold_at_1:
                lines.append(f"### {q['qid']}")
                lines.append(f"- **expected:** `{q.get('expected')}`")
                lines.append(f"- **primary_citation:** `{q.get('primary_citation')}`")
                lines.append(f"- **refused:** {q.get('refused')}, **over_refusal:** {q.get('over_refusal')}")
                lines.append(f"- **retry_outcome_class:** `{q.get('retry_outcome_class')}`")
                lines.append(f"- **per-stage:** db={q.get('t_db_wait_ms')}ms rerank={q.get('t_rerank_ms')}ms synth={q.get('t_synth_ms')}ms total={q.get('p_user_complete_ms')}ms")
                lines.append(f"- **diagnosis:** {_diagnose(q)}")
                lines.append("")
        lines.append(f"## 4. Monitor status (iter-12 Tasks 14, 25, 30, 31, 35)")
        if self.monitor_status:
            for monitor, status in self.monitor_status.items():
                lines.append(f"- **{monitor}:** {status}")
        else:
            lines.append("_No monitor status surfaced — verify telemetry tasks landed._")
        lines.append("")
        if self.notes:
            lines.append(f"## 5. Operator notes")
            for n in self.notes:
                lines.append(f"- {n}")
        path.write_text("\n".join(lines))


def _diagnose(q: dict) -> str:
    """Plain-English diagnosis from per-query fields."""
    if q.get("retry_outcome_class") == "empty_pool":
        return "Retrieval returned empty pool — check Class P PATH_F deploy state and entity-resolve telemetry"
    if q.get("retry_outcome_class") == "timeout":
        return "Retry timed out — check `t_db_wait_ms` and Class P thread-pool saturation"
    if q.get("over_refusal"):
        return "Synth refused with gold retrieved — Q3 gate or floor check needed"
    if q.get("primary_citation") and q.get("expected") and q["primary_citation"] not in q["expected"]:
        return "Wrong primary picked — check Q5 percentile demote margin and slot-1 anchor pin"
    if q.get("refused"):
        return "Refused — check `coverage_blind` flag from Task 30 audit"
    return "Unknown — manual triage required"


def run_audit(iter_dir: Path) -> AuditFindings:
    findings = AuditFindings()

    scores_path = iter_dir / "scores.md"
    if scores_path.exists():
        text = scores_path.read_text()
        m = re.search(r"\*\*Composite:\*\*\s+([\d.]+)", text)
        if m:
            findings.composite = float(m.group(1))
        for key in ("accuracy_user_visible", "over_refusal_rate",
                    "under_refusal_rate", "within_budget_rate"):
            m = re.search(rf"{key}:\s+([\d.]+)", text)
            if m:
                setattr(findings, key, float(m.group(1)))
        m = re.search(r"502 rate.*?:\s+([\d.]+)", text)
        if m:
            findings.burst_502_rate = float(m.group(1))
        m = re.search(r"503 rate.*?:\s+([\d.]+)", text)
        if m:
            findings.burst_503_rate = float(m.group(1))

    verification_path = iter_dir / "verification_results.json"
    if verification_path.exists():
        data = json.loads(verification_path.read_text())
        for phase in data.get("phases", []):
            if phase.get("phase") != "rag_qa_chain":
                continue
            for check in phase.get("checks", []):
                detail = check.get("detail") or {}
                qid = detail.get("qid")
                if not qid:
                    continue
                if detail.get("t_db_wait_ms") is not None or detail.get("t_synth_ms") is not None:
                    findings.per_stage_timing[qid] = {
                        "t_db_wait_ms": detail.get("t_db_wait_ms"),
                        "t_rerank_ms": detail.get("t_rerank_ms"),
                        "t_synth_ms": detail.get("t_synth_ms"),
                        "p_user_complete_ms": detail.get("p_user_complete_ms"),
                    }
                expected = detail.get("expected") or []
                if expected and not detail.get("gold_at_1"):
                    findings.failed_gold_at_1.append({
                        "qid": qid,
                        "expected": expected,
                        "primary_citation": detail.get("primary_citation"),
                        "refused": detail.get("refused"),
                        "over_refusal": detail.get("over_refusal"),
                        "retry_outcome_class": detail.get("retry_outcome_class"),
                        "t_db_wait_ms": detail.get("t_db_wait_ms"),
                        "t_rerank_ms": detail.get("t_rerank_ms"),
                        "t_synth_ms": detail.get("t_synth_ms"),
                        "p_user_complete_ms": detail.get("p_user_complete_ms"),
                    })
                if detail.get("refused"):
                    findings.refused_queries.append({"qid": qid, "expected": expected})
                if detail.get("p_user_complete_ms") and detail.get("budget_ms") and \
                        detail["p_user_complete_ms"] > detail["budget_ms"]:
                    findings.over_budget_queries.append({
                        "qid": qid, "p_user_complete_ms": detail["p_user_complete_ms"],
                        "budget_ms": detail["budget_ms"],
                    })

    # Monitor status — check iter-12 telemetry tasks landed
    findings.monitor_status["Task 14 — accuracy_user_visible (Class S)"] = (
        "OK" if findings.accuracy_user_visible is not None else "MISSING — Class S not surfaced"
    )
    findings.monitor_status["Task 25 — primary_citation headline (I9)"] = (
        "OK" if scores_path.exists() and "primary_citation" in scores_path.read_text()
        else "MISSING — I9 retrieval_recall split not in scores.md"
    )
    findings.monitor_status["Task 30 — coverage_blind audit (R3 Tier-1)"] = (
        "OK" if (iter_dir / "_audit" / "coverage_blind_queries.json").exists()
        else "NOT RUN — operator must invoke audit_gold_expectations.py"
    )
    findings.monitor_status["Task 31 — anchor_seed_bandit telemetry (R4)"] = (
        "OK if log line `anchor_seed_bandit qid=...` present in droplet logs (manual check)"
    )
    findings.monitor_status["Task 35 — ce_score_distribution + retry_outcome_class (R1 telemetry)"] = (
        "OK" if any(t.get("retry_outcome_class") for q in findings.failed_gold_at_1 for t in [q])
        or findings.per_stage_timing
        else "MISSING — verify orchestrator emits the log line and verification_results includes the field"
    )

    return findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iter", help="iter name, e.g. iter-12")
    ap.add_argument("--iter-dir", help="full path to iter directory")
    args = ap.parse_args()
    if args.iter_dir:
        iter_dir = Path(args.iter_dir)
    elif args.iter:
        iter_dir = Path("docs/rag_eval/common/knowledge-management") / args.iter
    else:
        raise SystemExit("Provide --iter or --iter-dir")
    findings = run_audit(iter_dir)
    out_path = iter_dir / "post_iter_audit.md"
    findings.write_report(out_path)
    print(f"Wrote {out_path}")
    print(f"Composite: {findings.composite}, accuracy_user_visible: {findings.accuracy_user_visible}, "
          f"failed_gold_at_1: {len(findings.failed_gold_at_1)}, "
          f"refused: {len(findings.refused_queries)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run audit tests.**

```bash
pytest tests/unit/ops_scripts/test_post_iter_audit.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Smoke-test on iter-11 data.**

```bash
python ops/scripts/post_iter_audit.py --iter iter-11
```

Expected: writes `docs/rag_eval/common/knowledge-management/iter-11/post_iter_audit.md`; exit 0; failed_gold_at_1 count matches `0.5385 → 13 − 7 = 6 failures` (q5, q7, q10, q13 as user-visible failures + q3, q9 with N/A treatment depending on aggregator).

- [ ] **Step 6: Wire audit into Phase 8 / Task 22 sequence.**

Modify Task 22 / Step 1 instruction (already in PLAN) to also run:

```powershell
python ops\scripts\post_iter_audit.py --iter iter-12
```

The output `post_iter_audit.md` is checked into the iter folder alongside `scores.md`.

- [ ] **Step 7: Commit.**

```bash
git add ops/scripts/post_iter_audit.py \
        tests/unit/ops_scripts/test_post_iter_audit.py
git commit -m "feat: post iter audit aggregator script"
```

#### Caveats & rollback

**Risk-mitigation:**
- **Read-only script — zero runtime impact.** Does not modify production code paths.
- **Brittle scores.md regex parsing**: `_render_scores_md` template is fixed in iter-12 (Task 14); if iter-13 changes the template, the regex must be updated. Add a smoke test in iter-13 PLAN.
- **`per_stage_timing` empty when Class P logs not surfaced**: report explicitly states "_No per-stage timing in verification_results.json — check Class P log artifact._" so operator knows to dig deeper.
- **`monitor_status` is heuristic** (checks for field presence, not value correctness). Operator-supplemental review still needed.

**Edge cases:**
- **Missing `scores.md`**: `run_audit` returns `AuditFindings(composite=None, ...)`; report still written with placeholder text.
- **Empty `verification_results.json`**: same; `failed_gold_at_1=[]`.
- **`p_user_complete_ms` missing on a query**: per-stage timing row shows `—` for that field.
- **iter-12 with new fields not in iter-11 schema**: report degrades gracefully (`getattr(..., None)` everywhere).
- **Re-run on the same iter dir**: idempotent — overwrites `post_iter_audit.md`.

**Rollback paths:**
1. **Skip the audit run** — don't invoke `post_iter_audit.py`. iter-11 scoring semantics preserved. **Reversible — single-line skip.**
2. **Code revert** — `git revert` the audit-script commit. Last resort.

---

### Task 33: R1 — Q13 forensic finding + SKIP-rationale (no code change in iter-12)

**Why this task exists:** R1 verification produced a critical forensic finding that **q13's failure was NOT a critic-threshold issue**. Logging this as an explicit task (with no code body) prevents future iters from re-attempting the same wrong fix.

**Forensic finding (verified 2026-05-07 from `iter-11/verification_results.json:1149-1180` + droplet logs):**
- q13 (multi_hop, gold=`nl-the-pragmatic-engineer-t`)
- `latency_ms_server=14118 ms`, `retrieved_node_ids=[]`, `reranked_node_ids=[]`, `cited_node_ids=[]`
- `critic_verdict=retry_budget_exceeded`
- 14.1s server-time = first-pass retrieval + critic + STEP_BACK-mutation retry retrieval (`_RETRY_MUTATION[MULTI_HOP]=STEP_BACK`, `orchestrator.py:291`) + 12s `_RETRY_BUDGET_S` `asyncio.wait_for` timeout, hitting `TimeoutError` branch at `orchestrator.py:1033`
- **Both passes returned an empty pool. Gold node never entered the candidate set.**

**Why this means the proposal "lower `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR(LOOKUP)` 0.7→0.5" is WRONG:**

The proposal targets the **post-rerank gold-skip gate**. Q13 never reached that gate because retrieval had **zero candidates** to score. Lowering the floor cannot fix a query where retrieval itself failed.

**Q13's actual fix lives in iter-12's retrieval-stage tasks:**
- Class P (PATH_F) eliminates the sync-RPC blocking that caused first-pass retrieval to compete for thread budget against burst neighbours during eval
- Task 28 (R5 alias canonicalization) makes "Pragmatic Engineer" / "product-minded engineer" resolve to `nl-the-pragmatic-engineer-t` through the alias array even when the metadata extractor surfaces only fragments
- Task 29 (R6 confidence-thresholded extraction + cap-3) reduces noise entities competing for anchor-resolve RPC budget
- Task 32 (R2 slot-1 anchor pin) ensures any successfully-resolved anchor is never lost to xQuAD's slot-1 = argmax(rrf) collision

**Skip-rationale documented for iter-13+:**

R1 surfaced three industry-standard dynamic alternatives for **future** consideration. Each is good engineering but does NOT address q13's actual cause. They are deferred:

| Alternative | Citation | Why deferred |
|---|---|---|
| Per-Kasten quantile-calibrated rerank floor (replace static 0.7 with rolling p70 over Kasten's CE distribution) | [TARG arXiv:2511.09803](https://arxiv.org/html/2511.09803v1), [Elastic BEIR primer](https://www.elastic.co/search-labs/blog/evaluating-search-relevance-part-1) | Still touches a CLAUDE.md-protected knob (even dynamically). Doesn't address q13 (retrieval-empty). Needs CE-distribution audit per deployed Kasten before locking the rolling-window size. |
| Top-K NLI faithfulness voting (refuse only if NLI on top-3 cited claims < 0.5) | [RAGAS Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/), [aman.ai NLI primer](https://aman.ai/primers/ai/factuality-in-LLMs/) | Adds NLI head infra (~80-150 ms/query). iter-13 D3 carry-over already covers this. |
| Conformal selective abstention (calibrate coverage target per Kasten with held-out set) | [arXiv:2502.07255](https://arxiv.org/abs/2502.07255), [arXiv:2512.12844](https://arxiv.org/html/2512.12844v1), [arXiv:2511.17908](https://arxiv.org/html/2511.17908) | Requires labeled calibration set per Kasten (10-30 graded queries). iter-14+ scope. |
| Adaptive retry budget (`base + 0.5 × first_pass_pool_size`, capped 22s) | [TARG adaptive gating](https://arxiv.org/html/2511.09803v1), [AcuRank arXiv:2505.18512](https://arxiv.org/html/2505.18512v1) | MEDIUM confidence per R1 — R1 admitted it can't verify the 12s wall actually clipped a productive retry vs one that would have failed regardless. iter-13 carry-over. |

- [ ] **Step 1: ZERO CODE CHANGE in iter-12.** This task is documentation-only.

- [ ] **Step 2:** Append the deferred alternatives to `iter-12/RESEARCH.md` "iter-13 carry-overs" section under a new heading **"R1 deferred — dynamic floor / retry alternatives"** with the four rows above + citations. Future executor checking iter-13 plan will see the rationale.

- [ ] **Step 3:** Mark observation as `decision` in mem-vault: "Static `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR=0.7` REMAINS in iter-12. q13's failure was retrieval-empty (verified). Dynamic floor alternatives deferred to iter-13+ pending Kasten-CE-distribution audit."

- [ ] **Step 4:** Add an iter-13 acceptance gate (in iter-12 RESEARCH.md): **"Before authoring iter-13's per-Kasten floor task, run `ops/scripts/audit_ce_distribution.py` (NEW iter-13) over ≥3 deployed Kastens. If p70 of CE distribution differs > 0.05 between Kastens, dynamic floor is justified. If clusters within ±0.03 of 0.7, no-op static floor is correct — close the carry-over."**

**Confidence: HIGH** on the q13 retrieval-empty diagnosis (`verification_results.json` is canonical and unambiguous). **HIGH** on the SKIP-iter-12 verdict (proposed fix doesn't address the actual cause and would touch a protected knob). **MEDIUM** on the iter-13 deferral being the right answer — empirical CE-distribution audit may show a static floor is universally correct.

**Citations:**
- [TARG: Training-Free Adaptive Retrieval Gating arXiv:2511.09803](https://arxiv.org/html/2511.09803v1)
- [AcuRank: Uncertainty-Aware Adaptive Listwise Reranking arXiv:2505.18512](https://arxiv.org/html/2505.18512v1)
- [Beyond Confidence: Adaptive Abstention via Dual-Threshold Conformal Prediction arXiv:2502.07255](https://arxiv.org/abs/2502.07255)
- [Selective Conformal Risk Control arXiv:2512.12844](https://arxiv.org/html/2512.12844v1)
- [Principled Context Engineering for RAG arXiv:2511.17908](https://arxiv.org/html/2511.17908)
- [Know Your Limits: A Survey of Abstention in LLMs (TACL 2025)](https://aclanthology.org/2025.tacl-1.26.pdf)
- [RAGAS Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)

---

### Task 34: PRE-Phase-1 verification — confirm iter-11 final-eval flag state

**Why:** if Task 23 lands BEFORE Phase 1, the deploy will overwrite `compose/.env` with the new STATIC_BODY (which includes `RAG_ANCHOR_BOOST_ENABLED=false` for the Phase 1 posture). But the iter-11 forensic report attributed certain failures to "rolled-back state" — based on a manual operator edit that may have been wiped on previous pushes per the I1 finding. Before reasoning about iter-12 deltas, **verify what flags were actually live during iter-11 final eval**.

**Position:** **EXECUTE FIRST.** Despite the high task number (audit-derived ordering), this runs BEFORE Task 23's deploy, BEFORE Phase 1 PATH_F, BEFORE any code change. It is the first action of the iter-12 work.

- [ ] **Step 1:** Pull droplet logs from the iter-11 final-eval window (2026-05-05T20:43Z–20:55Z) via `gh workflow run read_recent_logs.yml --ref master`.
- [ ] **Step 2:** Search for `entity_anchor_resolve` log lines (only fire if anchor-boost was active) AND `anchor_seed_inject` log lines (only fire if seed injection was active).
- [ ] **Step 3:** If lines present → iter-11 final-eval ran with **anchor-boost ACTIVE**, NOT rolled back as previously assumed. Update iter-12 RESEARCH.md "iter-11 outcome that motivates iter-12" section with the corrected attribution. Per-query failure-mode attributions may need revision (e.g. q5 magnet might have fired with anchor-boost active, in which case Class A's iter-11 binary exemption was the bug, NOT the anchor flag).
- [ ] **Step 4:** If lines absent → rollback DID land (despite the workflow bug). No correction needed; baseline math is honest.
- [ ] **Step 5:** Either way, document the finding and proceed with Phase 1 sequencing (Task 23 → Task 24 → Task 33 [doc only] → Task 28-32 → Phase 1-8 in numerical order).
- [ ] **Step 6:** Record the finding as a `discovery` observation in mem-vault for future iter-12 / iter-13 forensics.

This step has zero code change; it's a forensic gate before Phase 1 starts so the iter-12 baseline math is honest. **Without this, Task 23 may inadvertently flip behavior compared to what iter-11 final eval actually measured, contaminating the iter-12 trajectory analysis.**

---

## Self-review checklist (executor: run before claiming done)

### Core class work (Phases 1-8)

- [ ] Class P (Phase 1) shipped FIRST and burst-12 passes BEFORE Phase 2 anchor-boost re-enable.
- [ ] Phase 1 and Phase 2 are SEPARATE deploys (operator confirms in chat between them).
- [ ] All 12 sync `supabase.rpc().execute()` sites are wrapped in `await rpc_call(...)`.
- [ ] `event_loop_lag_ms` p95 < 50 ms in `/api/health` after Phase 1.
- [ ] No `TBD`, `TODO`, `fill in later` placeholders in any task body.
- [ ] Type/method names consistent across tasks (`rpc_call`, `_top1_top2_gap`, `_apply_score_rank_demote`, `_tiebreak_key`, `_finalize_answer`, `_VAGUE_DISCOVERY_PATTERN`, `_aggregate_gold_metrics`, `_pick_anchor_pin`, `_demote_factor_for_candidate`, `canonicalize_node`, `EntityBlocklist`).
- [ ] All env flags added to `ops/.env.example` (Task 19) AND `STATIC_BODY` (Task 23); removed `RAG_SHORT_THEMATIC_THRESHOLD` (Class D-out); removed `RAG_SCORE_RANK_DEMOTE_FACTOR` (replaced by `RAG_SCORE_RANK_DEMOTE_SLOPE` in Task 32).
- [ ] Cross-class regression fixture passes after every retrieval-stage change in Phases 3 + 5 + Task 32 (Q5 percentile fixture + slot-1-pin fixture + percentile-demote fixture are new additions).
- [ ] No protected CLAUDE.md knob touched (`GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, rerank semaphore unchanged; `_PARTIAL_NO_RETRY_FLOOR=0.5` literal unchanged; `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR=0.7` literal unchanged — Task 33 documents the SKIP-rationale; only K3 confidence-gap fires before the floor logic).
- [ ] Composite weights file `composite_weights.yaml` is hash-locked.
- [ ] Final eval shows: composite ≥ 85, accuracy_user_visible ≥ 0.85, over_refusal_rate ≤ 0.10, under_refusal_rate ≤ 0.05, within_budget on p_user_complete_ms ≥ 0.85, burst 502 = 0%, zero worker OOMs.
- [ ] scores.md follows canonical template (no fix recommendations).

### Phase 9 audit-derived (Tasks 23-34)

- [ ] **Task 34** PRE-Phase-1 forensic verification ran BEFORE any other task; flag state of iter-11 final eval documented.
- [ ] **Task 23** `.github/workflows/deploy-droplet.yml` STATIC_BODY contains all 5 iter-11 carry-over knobs + 16 iter-12 knobs (Class P, K3, K4, Q5, Q7, R5, R6, R2 — total). CI drift-check job fails when `.env.example` and STATIC_BODY diverge.
- [ ] **Task 23** `compose/.env.local` overlay file mounted via second `--env-file` in blue/green compose; `tee` in workflow only writes `.env`, never `.env.local`.
- [ ] **Task 23** post-deploy SSH smoke test asserts the live container env matches STATIC_BODY for the 3-knob spot check (`RAG_ANCHOR_BOOST_ENABLED`, `RAG_EXECUTOR_MAX_WORKERS`, `RAG_RPC_GLOBAL_SEMAPHORE`).
- [ ] **Task 24** `vm.swappiness=1` persisted in `/etc/sysctl.d/99-zettelkasten.conf` on droplet; `sysctl vm.swappiness` returns 1 post-set; `proc_stats.vm_swap_kb` per worker drops below 200 over the next 100 queries.
- [ ] **Task 25** `_holistic_metrics` reads `primary_citation in expected` for `gold_at_1_unconditional`; `retrieval_recall_at_1` is a separate diagnostic; `_qa_summary` mirrors both AND applies E1.
- [ ] **Task 26** `score_rank_demote ... margin=...` log line emitted on every THEMATIC retrieval; scores.md surfaces p10/p50 margin distribution. (Note: Task 26's static 0.85→0.75 flip is SUPERSEDED by Task 32; only the telemetry survives.)
- [ ] **Task 28 (R5)** `kg_nodes.aliases text[]` + `summary_hash text` columns added; `rag_resolve_entity_anchors` returns `(node_id, matched_via)` and matches via name OR alias OR tag; backfill script ran for all existing zettels; new ingests trigger canonicalization on summary-hash change only.
- [ ] **Task 29 (R6)** entity extraction returns `{text, confidence}` per item; confidence floor 0.7; cap-3 by confidence DESC + tie-break; `kg_extraction_blocklist` table created; cold-start guard skips block when Kasten < 50 nodes; resolution success evicts row.
- [ ] **Task 30 (R3)** `cited_in_context` runtime guard added to `/api/summarize`; `gold_expectation_groundedness_check` pre-eval audit runs over all 14 KM-Kasten queries; flagged-as-coverage-blind queries are EXCLUDED from gold@1 numerator (E1-style N/A treatment).
- [ ] **Task 31 (R4 FULL SHIP)** `kg_kasten_metrics` extended with `seed_arm`, `seed_pool_bucket`, `seed_alpha`, `seed_beta`, `seed_total_pulls`, `seed_last_decay_at`, `bandit_disabled_at`, `bandit_disabled_reason` columns. Bandit module `anchor_seed_bandit.py` shipped with R4-followup mods 1-4: γ=0.98/day decay, S/M/L pool-size stratification, informative warm-start prior, per-Kasten kill switch column. Pre-flight `bandit_warm_start.py` script ran. Two-week ramp plan executed: Day 1-3 telemetry-only → Day 4-7 canary 1 → Day 8-14 canaries 2-3 → Day 15+ all-Kasten (operator-approved per acceptance gate). 5 pathology metrics surfaced at `/api/health`.
- [ ] **Task 32 (R2)** `_pick_anchor_pin` helper inserted before `_xquad_select` call site; cap pin to slot-1 only; `_demote_factor_for_candidate` percentile-derived (slope `0.20`); SUPERSEDES Task 26's static 0.85→0.75 flip; cross-class fixtures cover sparse/dense/single-topic/compare/anchor-mismatch.
- [ ] **Task 33 (R1)** documentation-only; deferred alternatives logged in `iter-12/RESEARCH.md` "iter-13 carry-overs"; mem-vault `decision` observation recorded.
- [ ] **Task 35 (R1 monitors)** `RetryOutcomeClass` enum + `classify_retry_outcome` helper added to `orchestrator.py`; `ce_score_distribution` log line emitted per query; `retrieval_pool_size_{initial,retry}` + `retry_outcome_class` + `t_db_wait_ms` + `t_rerank_ms` + `t_synth_ms` surfaced in `verification_results.json`; `audit_ce_distribution.py` empirical-gate runner shipped with 3 unit tests for ACTIVATE/CLOSE/DEFER decisions.
- [ ] **Task 36 (post-iter audit)** `post_iter_audit.py` aggregator script shipped; `AuditFindings.write_report` produces `post_iter_audit.md` consolidating scores + per-stage runtime/memory + failed gold@1 forensic + monitor status (Tasks 14, 25, 30, 31, 35); smoke-tested on iter-11 data; wired into Phase 8 / Task 22 sequence.

### Phase 9 caveats & rollback paths (Tasks 28-32 each have a "Caveats & rollback" subsection)

- [ ] **Task 28 (R5)** Caveats section covers: alias quality variance, GIN index bloat, bulk-resummarization storms, SQL injection mitigation (parameterized queries), language safety, rollback paths 1-4 (env flag → resolver revert → schema drop → code revert).
- [ ] **Task 29 (R6)** Caveats section covers: non-English confidence calibration, single-topic over-blocking override (≥10% Kasten-presence whitelist), cold-start guard, blocklist drift on content change, malformed JSON tolerance, rollback paths 1-5.
- [ ] **Task 30 (R3)** Caveats section covers: groundedness check operator-override (advisory not auto), NLI prompt versioning, false-positive on paraphrase, runtime guard cost <1ms, rollback paths 1-4.
- [ ] **Task 31 (R4)** Caveats section covers: DB-unreachable fail-open, cold-start over-fitting, concurrent-write atomicity (Postgres ON CONFLICT), pathology detection (5 alerts), multi-tenant isolation, edge cases for new/multi-language/single-author Kastens, rollback paths 1-4 (per-Kasten kill → global flag → schema drop → code revert).
- [ ] **Task 32 (R2)** Caveats section covers: slot-1 pin evidence-floor, percentile demote on tiny pools, slope knob safe ranges [0.10, 0.30], K3 confidence-gap interaction order, edge cases for resolved-but-not-in-pool / all-zero-rrf, rollback paths 1-4.

### iter-13 carry-overs (must be in iter-12/RESEARCH.md)

- [ ] D3 NLI citation post-validation (DeBERTa-v3 int8 ~180 MB)
- [ ] K5 telemetry / Langfuse-style per-Kasten observability
- [ ] K6 LLM gazetteer replacement for `vague_expander.py`
- [ ] PATH_C Postgres `rag_anchor_resolve_full` function (gated on pgvector#703 EXPLAIN ANALYZE validation)
- [ ] R1 dynamic floor alternatives (per-Kasten p70 / NLI voting / conformal abstention / adaptive retry budget) gated on `audit_ce_distribution.py` decision (`ACTIVATE_A1_PER_KASTEN_FLOOR` vs `CLOSE_CARRY_OVER_STATIC_FLOOR_CORRECT`)
- [ ] R3 Tier-2 (LettuceDetect span-NLI) + Tier-3 (RAGAS TestSet Generator corpus self-test)
- [ ] R4 bandit "extend canary → all" gate (Task 31 / Step 10 acceptance criteria)
- [ ] R5 cross-Kasten alias clustering + `matched_via` uplift attribution
- [ ] R6 per-language confidence threshold tuning (post-`query_lang_hint` data collection)
- [ ] Corpus-truth audit for q3/q7-shape gold expectations
