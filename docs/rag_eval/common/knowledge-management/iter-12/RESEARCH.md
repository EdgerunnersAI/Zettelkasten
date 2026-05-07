# iter-12 Research Reference

This document is the consolidated research artefact for iter-12. Audiences:

1. **Future humans / agents looking back at iter-12** — to understand what we knew, what we tried, and what we deliberately rejected.
2. **The plan executor (subagent or human running [PLAN.md](PLAN.md))** — to look up rationale, edge cases, and "why not X" decisions when a phase task references them.

**Cross-reference:** [PLAN.md](PLAN.md) — implementation tasks. This file is the *why*; PLAN.md is the *how*.

**Sister artefacts (read before any phase):**
- `iter-11/scores.md` — final iter-11 metrics + per-query forensic
- `iter-11/RESEARCH.md` — Class A–F rationale + Phase 8 / Task 11 rollback decision (sync-blocking RPC root cause; iter-12 PATH_F mandate)
- `iter-11/verification_results.json` — per-query forensic source of truth
- `iter-10/scores.md`, `iter-10/RESEARCH.md` — iter-10 P3/P4/Item3/P9 baseline
- `CLAUDE.md` root — Critical Infra Decision Guardrails

---

## How the executor should use this file

Before implementing any PLAN.md phase:

1. Read the matching `Class` section here.
2. If a task is unclear, look up "Pitfalls" and "Cons NOT to take" — they capture every dead end already explored.
3. If a test fails in an unexpected way, check "Edge cases" for that class.
4. If you encounter a decision point not covered here, **stop and ask the user** rather than improvising. Beyond-plan decisions require explicit chat-confirmed approval per CLAUDE.md.

---

## iter-11 outcome that motivates iter-12

| Metric | iter-10 final | iter-11 final (post-rollback) | iter-12 target |
|---|---:|---:|---:|
| Composite | 66.10 | 60.26 | ≥ 85 |
| chunking | 40.43 | 40.43 | held |
| retrieval | 78.26 | 64.26 | ≥ 80 |
| reranking | 59.75 | 52.45 | ≥ 70 |
| synthesis | 67.87 | 65.92 | ≥ 75 |
| gold@1 unconditional (scores.md) | 0.6429 | 0.6154 | ≥ 0.85 |
| gold@1 user-visible (NEW iter-12 headline) | n/a | **0.5385** (manually computed) | ≥ 0.85 |
| within_budget rate | 0.6429 | 0.50 | ≥ 0.85 |
| burst 502 rate | 0.25 | 0.50 | 0% |
| RAGAS faithfulness | 97.14 | 87.86 | ≥ 85 (held) |
| RAGAS answer_relevancy | 80.00 | 85.71 | ≥ 80 (held) |

**Per-query failure mode after iter-11 (verified disk facts from `iter-11/verification_results.json`):**

| qid | class | gold@1 | budget | iter-11 failure root cause | iter-12 class |
|---|---|:-:|:-:|---|---|
| q1  | multi_hop | T | F | over-budget by 11.9 s; cumulative loop-blocking drift | **P** |
| q2  | lookup | T | F | over-budget by 11.0 s; **regression** from iter-10 (was within) | **P** |
| q3  | lookup | T | T | over-refusal: gold cited at top-1, critic said `unsupported_with_gold_skip` after `REFUSAL_PHRASE` substitution masked the draft | **Q3** |
| q4  | lookup | T | T | pass | none |
| q5  | thematic | F | T | wrong primary `gh-zk-org-zk` (the recurring magnet); iter-11 Class A title-overlap exemption with `>0.0` threshold accidentally exempted the magnet via incidental token-overlap | **Q5** |
| q6  | multi_hop | T | F | over-budget by 8.9 s; loop-blocking drift | **P** |
| q7  | thematic→**lookup** | F | T | router (gemini-2.5-flash-lite) classified as LOOKUP non-deterministically; Class D never fired (THEMATIC-gated); gazetteer dormant | **Q7** + **A1** |
| q8  | thematic | T | F | over-budget by 4 s; loop-blocking drift; gold-at-1 maintained | **P** |
| q9  | thematic | n/a | T | refusal-expected; correct refusal (Class E1 N/A applies) | scoring (**S**) |
| q10 | lookup | F | T | rolled-back anchor-boost; iter-11 Class C dormant under `RAG_ANCHOR_BOOST_ENABLED=false` | **P** (then re-enable) |
| q11 | lookup | T | F | over-budget by 2.4 s | **P** |
| q12 | thematic | T | F | over-budget by 4.4 s | **P** |
| q13 | multi_hop | F | F | catastrophic spike: `latency_ms_server=14118 ms` (10× normal), empty pool, `retry_budget_exceeded`. **Container OOM-killed mid-eval** (`docker inspect zettelkasten-green oom_killed=true`) | **P** + infra |
| q14 | multi_hop | T | T | pass | none |

**Headline insight:** iter-11 left a single dominant root cause with seven downstream symptoms. The 12+ sync `supabase.rpc().execute()` call sites in `website/features/rag_pipeline/retrieval/` block the asyncio event loop for 150–400 ms each. Under burst-12 (eval scenario) the 1-vCPU droplet's 5-thread default executor (`min(32, cpu_count+4)`) saturates, the rerank semaphore stalls, the SSE heartbeat misses, Caddy's `dial_timeout` trips, and Cloudflare passes a 502 through. The OOM kill of `zettelkasten-green` during the iter-11 final eval is the proof. **Class P (PATH_F) is the foundational fix.** Without it, every other knob compounds the problem instead of fixing it.

`latency_ms_server` (0.9–1.7 s for non-spike queries) is **not** end-to-end server time — it is the synth-after-TTFT timer (verdict H4 from iter-11/RESEARCH.md Class E2). The honest server total is `p_user_complete_ms` (28–60 s). iter-12 Class S renames the field.

---

## Iter-11 final-eval verification + 6-agent audit (2026-05-06)

After iter-11 closed, a 6-agent parallel research pass was run to validate every proposed iter-12 modification against:

1. Repercussions across query phenotypes (LOOKUP / THEMATIC / MULTI_HOP / STEP_BACK / VAGUE / compare-intent / negation / proper-noun heavy / long sentence)
2. Repercussions across Kasten phenotypes (sparse ≤ 7, medium 14, dense 50+, single-author, single-source, single-topic, non-Latin)
3. Production-RAG industry standards (RAGAS, BEIR, Langfuse, AutoRAG-HP, ZeroEntropy, Pinecone, Weaviate, AWS Bedrock)
4. Cross-encoder calibration literature (sentence-transformers#1262, ZeroEntropy 2026, BEIR 2021)
5. FastAPI / Supabase / asyncio scaling on a 1-vCPU droplet (FastAPI docs, supabase-py issues #1306/#604/#798, pgvector#703, supavisor)
6. Hallucination/abstention literature (Google sufficient-context, AAAI 2026, VeriCite arXiv:2510.11394, ACL 2025 NLI)

**Convergent finding:** every static knob in iter-11 (`>= 0.20`, `0.5`, `0.7`, `25%`, `≤ 4 words`, `−0.1` offset) is over-fit to the KM Kasten benchmark. Cross-encoder scores are NOT comparable across queries/corpora ([ZeroEntropy 2026](https://www.zeroentropy.dev/articles/should-you-use-llms-for-reranking-a-deep-dive-into-pointwise-listwise-and-cross-encoders), [BEIR 2021](https://arxiv.org/abs/2104.08663)). iter-12 replaces 6+ static knobs with two dynamic primitives (**K3 confidence-gap**, **K4 bootstrap-per-Kasten**) and removes Class D's static gazetteer entirely.

**User ratification (chat 2026-05-06, "Looks good!"):** all six approval-required items from the audit are confirmed:
1. Replace `>= 0.20` static with **percentile-based** (no env knob).
2. **Remove** Class D static gazetteer (LLM expansion deferred to iter-13).
3. **Composite reweight** to `{trust 0.40, accuracy 0.30, retrieval 0.15, calibration 0.10, latency 0.05}`.
4. Class F continued operation **gated on D3 NLI ship** (D3 itself defers to iter-13).
5. PATH_F sized to `max_workers=8` (NOT 16) **plus** global `asyncio.Semaphore(8)` **plus** httpx `max_connections` 10→16.
6. **A1 few-shot router prompt** (8–12 balanced examples) ships alongside the Q7 regex.

---

## Architectural decisions baked in (CLAUDE.md guardrails — DO NOT touch)

`GUNICORN_WORKERS=2`, `--preload`, `FP32_VERIFY_ENABLED` top-3 only, `GUNICORN_TIMEOUT≥180s` (verified prod=240s), rerank semaphore + bounded queue (`RAG_QUEUE_MAX=8/worker`), SSE heartbeat wrapper, Caddy `read_timeout 240s`, schema-drift gate, `kg_users` allowlist gate, teal/amber color rule, BGE int8 reranker (no swap), threshold floors (`_PARTIAL_NO_RETRY_FLOOR=0.5` literal LOOKUP/VAGUE default — only the per-class effective value moves via Class F offset).

**iter-12 explicit operator approvals (chat 2026-05-06):**
- **Class P (PATH_F)** introduces a new `loop.set_default_executor(ThreadPoolExecutor(max_workers=8))` at FastAPI lifespan startup AND wraps every sync `supabase.rpc().execute()` in `await asyncio.to_thread(...)`. This is NOT one of CLAUDE.md's protected knobs (those concern gunicorn worker count, timeouts, and rerank semaphore — none of which are touched). Class P is additive.
- **Class W (composite reweight)** changes the headline composite formula. This is a scoring change, not an infra change; affects every prior iter's reported composite when read with the new weights. iter-12+ scores are computed with the new weights; iter-04..iter-11 stay locked at the old weights for historical comparability.
- **Class D removal** unships an iter-11 mechanism. Operator-approved because the gazetteer's hand-curated keys are over-fit to English/tech vocabulary; on any non-tech or non-English Kasten the gazetteer is provably worse than no expansion. iter-13 will replace it with a one-shot LLM expansion (K6, deferred).

---

## Class P — PATH_F (asyncio.to_thread + global semaphore + httpx pool) + restore anchor-boost

**Verdict: ✓ ship Phase 1 + Phase 2.**

**The problem (iter-11 final-eval):** `docker inspect zettelkasten-green oom_killed=true` during the eval window. 12+ sync `supabase.rpc().execute()` call sites block the asyncio event loop for 150–400 ms each. CPython 3.12's default executor is `min(32, cpu_count+4) = 5 threads/process` on a 1-vCPU droplet ([Python concurrent.futures docs](https://docs.python.org/3/library/concurrent.futures.html), retrieved 2026-05-06). Under burst-12, the loop saturates, heartbeat starves, Caddy's `dial_timeout` trips, Cloudflare returns 502. iter-11 first-run made it worse by activating 4 dormant RPCs (anchor-boost wiring patch); rolled back via `RAG_ANCHOR_BOOST_ENABLED=false`. iter-11 final-eval still showed 50% 502 rate post-rollback because the underlying sync-blocking is structural.

**The fix (industry-validated by 6-agent research):**

1. Wrap every `supabase.rpc(...).execute()` call site in `await asyncio.to_thread(...)`. Twelve sites in `website/features/rag_pipeline/retrieval/`:
   - `hybrid.py:395, 443, 579` (every retrieval — main hybrid + dense fallback + node-resolve)
   - `chunk_share.py:91` (every retrieval, behind TTL cache)
   - `kasten_freq.py:69, 100` (every retrieval)
   - `graph_score.py:73` (rerank stage)
   - `entity_anchor.py:50, 85` (per-entity loop, anchor-boost path)
   - `anchor_seed.py:22` (anchor-seed inject)
2. For per-entity loops in `entity_anchor.resolve_anchor_nodes`, fan out concurrently with `asyncio.gather(*[asyncio.to_thread(...) for e in entities])` wrapped in `asyncio.Semaphore(3)` per request to bound NER-explosion (10+ entities → still 3 concurrent).
3. At FastAPI lifespan startup, set `loop.set_default_executor(ThreadPoolExecutor(max_workers=8, thread_name_prefix="supa"))`. **NOT 16** — see sizing arithmetic below.
4. Module-level `_RPC_SEM = asyncio.Semaphore(8)` wrapping every `to_thread` call to bound DB-side fan-out across all concurrent requests (NOT just per-request).
5. Bump `httpx.Limits(max_connections=16, max_keepalive_connections=8)` in `website/core/supabase_kg/client.py:109`.
6. **Then** re-enable `RAG_ANCHOR_BOOST_ENABLED=true` AND `RAG_ANCHOR_SEED_INJECTION_ENABLED=true` in a **separate deploy** after staging burst-12 passes. iter-11's failure was wiring + flag-flip in one shot; iter-12 separates them.

**Sizing arithmetic (validated by research agent 4):**

| Constraint | Value | Implication |
|---|---|---|
| Droplet RAM | 2 GB | ~900 MB usable per worker after OS + BGE int8 |
| Per-thread cost | ~10 MB resident | 2 workers × 8 threads × 10 MB ≈ 160 MB ceiling — fits |
| httpx connection pool | `max_connections=10` (current) → 16 (proposed) | 8 in-flight RPC slots + 8 keepalive headroom |
| Supabase PostgREST | soft-cap ~30 backend / project tier | 2 workers × 16 = 32 — at the edge; bounded by `Semaphore(8)` global cap |
| GIL on 1 vCPU | I/O releases GIL | thread saturation is I/O-bound; threads ≥10 yield diminishing returns |
| CPython default | `min(32, cpu_count+4) = 5` | current behavior; expand to 8 |

**Why NOT 16:** the original RESEARCH.md proposal (16) ignores httpx pool exhaustion. With 16 threads but `max_connections=10`, threads queue on `httpx.PoolTimeout` (5 s default) → cascading 502. RAM also tighter at 2 × 16 × 10 MB = 320 MB.

**Why a global Semaphore in addition to executor sizing:** the executor caps **per-process** concurrency; the global Semaphore caps **across processes** in the same gunicorn worker (which has multiple coroutines per request through SSE streaming + retrieval + rerank). Without it, a fan-out compare-intent query with 5 entities can issue 5 RPCs simultaneously while another concurrent request issues 4 more — the executor accepts them but the DB/PostgREST sees a burst.

**Per-request `asyncio.Semaphore(3)` on entity-gather:** caps the gather fan-out when NER returns ≥ 4 entities (rare but plausible for "Compare Steve Jobs / Naval / Patrick Winston / Matt Walker..." style queries).

**Citations:**
- [FastAPI async docs](https://fastapi.tiangolo.com/async/) — sync-in-async blocks the loop
- [Python concurrent.futures docs](https://docs.python.org/3/library/concurrent.futures.html) — default executor sizing
- [Death and Gravity — limit concurrency in asyncio](https://death.andgravity.com/limit-concurrency) — bounded fan-out pattern
- [supabase-py issue #1306](https://github.com/supabase/supabase-py/issues/1306) — `acreate_client` blocked; iter-13+ scope
- [Supabase connection management](https://supabase.com/docs/guides/database/connection-management)
- [Caddy reverse_proxy directive](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) — 502 on dial_timeout

**Pitfalls / cons NOT to take:**
- DO NOT migrate to `acreate_client` — supabase-py#1306 still open in 2.24.0 (`ClientOptions` regression).
- DO NOT collapse RPCs into a single Postgres function in iter-12 — pgvector's HNSW/IVFFlat planner falls back to seq-scan on correlated LATERAL parameters ([pgvector#703](https://github.com/pgvector/pgvector/issues/703)). Defer to iter-13 with mandatory `EXPLAIN ANALYZE` validation.
- DO NOT raise `max_workers` > 8 — RAM ceiling and httpx pool exhaustion both bind.
- DO NOT skip the global semaphore — without it, burst-12 + compare-intent breaks the bound.
- DO NOT flip `RAG_ANCHOR_BOOST_ENABLED=true` in the same deploy as PATH_F. Two deploys, gated by staging burst test.

**Edge cases:**
- Slow Supabase (5 s+ RPC) — threads occupied 5 s; queue grows. Mitigation: per-RPC `httpx` timeout = `Caddy read_timeout (240 s) − 30 s` = 210 s; below that, threads naturally drain.
- Cold worker (post-restart, model warm-up) — `to_thread` doesn't fix CPU-bound warm-up. Acceptance: cold-start spike is a separate latency window covered by iter-12 Class W (within-budget metric).
- Connection-pool starvation — bumping `max_connections` to 16 prevents this; Semaphore(8) means in-flight RPCs ≤ 8 < 16 keepalive headroom.

**Where this lands:** [PLAN.md](PLAN.md) Phase 1 (PATH_F infra, anchor-boost OFF) + Phase 2 (re-enable anchor-boost in separate deploy).

---

## Class K3 — confidence-gap thresholds (replaces multiple static floors)

**Verdict: ✓ ship Phase 3.**

**The problem:** iter-08..iter-11 cumulative knob count: `_PARTIAL_NO_RETRY_FLOOR=0.5`, `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR=0.7`, `_TITLE_OVERLAP_EARNED_FLOOR=0.20` (proposed iter-12), Class F `−0.1` offset, magnet-spotter `≥ 25%`, `RAG_SHORT_THEMATIC_THRESHOLD=4 words`. Each is a static absolute calibrated against the KM Kasten's cross-encoder score distribution. Cross-encoder scores are NOT comparable across queries/corpora — every static threshold is brittle on a different Kasten ([ZeroEntropy 2026](https://www.zeroentropy.dev/articles/should-you-use-llms-for-reranking-a-deep-dive-into-pointwise-listwise-and-cross-encoders), [sentence-transformers#1262](https://github.com/UKPLab/sentence-transformers/issues/1262)).

**The fix:** introduce **confidence-gap** as a primitive. For any candidate-pool decision (skip-retry, magnet-demote-exemption, magnet-spotter), compute `top1_score / top2_score` (or `top1_score - top2_score` for absolute gap). When gap ≥ `_CONFIDENCE_GAP_FACTOR` (default `1.5x`), the top-1 is a clear winner and the gate skips. When gap < factor, the gate's normal logic applies. This replaces the absolute floors with a relative measure that self-tunes per query.

**Concrete iter-12 wiring:**

1. New helper `_top1_top2_gap(candidates: list[RetrievalCandidate]) -> float` in `hybrid.py`. Returns the ratio `top1.rrf_score / max(top2.rrf_score, 1e-9)`.
2. `_apply_score_rank_demote` (Class A): skip the entire gate when gap ≥ `_SCORE_RANK_GAP_BYPASS` (env: `RAG_SCORE_RANK_GAP_BYPASS=1.5`).
3. `should_skip_retry` (orchestrator.py): when gap ≥ `_RETRY_GAP_BYPASS` (env: `RAG_RETRY_GAP_BYPASS=1.5`) the partial/unsupported floors are bypassed (top-1 is grounded enough to skip retry regardless of absolute CE score).
4. Class F per-class offset stays in place but is **no longer the primary mechanism** — K3 confidence-gap fires first; offset is the fallback.

**Why `1.5x` ratio specifically:**
- [Weaviate Cross-Encoders as Reranker](https://weaviate.io/blog/cross-encoders-as-reranker) — production guidance: use score-gap rather than absolute thresholds.
- [Cluster-based Adaptive Retrieval (arXiv 2511.14769, 2025)](https://arxiv.org/html/2511.14769v1) — replaces fixed top-k with similarity-gap detection.
- 1.5× empirically separates "clear winner" from "rrf-tied" cases in the iter-09..iter-11 evals.

**Pitfalls / cons NOT to take:**
- DO NOT remove the static floors — keep them as fallback when gap < threshold.
- DO NOT use top-1 / top-3 (or any K beyond 2) — top-2 is the disambiguator; deeper noise.
- DO NOT lower the gap factor below 1.3 — too aggressive; would skip the gate on near-ties.

**Edge cases:**
- Single-candidate pool — gap is undefined (division by zero); fall through to absolute floors.
- Identical top-1/top-2 (`gap = 1.0`) — gate fires normally.
- Sparse Kasten where every score is high (e.g. all 0.85+) — gap may still be tight; absolute floor is the right tool here. Both work in tandem.

**Where this lands:** [PLAN.md](PLAN.md) Phase 3 / Task 6.

---

## Class K4 — per-Kasten bootstrap of magnet-spotter (replaces static 25%)

**Verdict: ✓ ship Phase 3.**

**The problem:** the `magnet-spotter` warning fires at `>= 25% top-1 share` over 14 queries — a static threshold tied to the KM Kasten's eval shape. On a sparse Kasten (< 10 queries) it's noisy; on a dense Kasten (> 100) it's loose; on a single-topic Kasten (all queries return the same chunk) it always fires.

**The fix:** maintain a per-Kasten rolling-window cache of top-1 frequencies (last `N=50` queries). Each Kasten's effective magnet-spotter threshold = `mean + 2 × stdev` over the rolling window. New zettels get measured; magnet-spotter is dynamic per Kasten.

**Implementation:**
1. Add `kg_kasten_metrics` Supabase table `(sandbox_id uuid, top1_node_id text, ts timestamptz)` — rolling per-query top-1 record.
2. New `KastenStats` helper in `website/features/rag_pipeline/observability/kasten_stats.py` — maintains in-memory rolling counter; periodically flushes to Supabase.
3. `magnet-spotter` reads from `KastenStats.bootstrap_threshold(sandbox_id)` instead of static `0.25`.

**Citations:**
- [AutoRAG-HP (Microsoft EMNLP 2024)](https://arxiv.org/abs/2406.19251) — online MAB for adaptive thresholds.
- [RAG in the Wild (arXiv 2507.20059, 2025)](https://arxiv.org/html/2507.20059) — no static threshold survives heterogeneous corpora.

**Pitfalls / cons NOT to take:**
- DO NOT bootstrap from the first query (insufficient data) — fall back to static `0.25` until N ≥ 20 queries.
- DO NOT use a smaller window than 50 — variance dominates.

**Where this lands:** [PLAN.md](PLAN.md) Phase 3 / Task 7.

---

## Class Q3 — gate skip-path on REFUSAL_PHRASE / has_valid_citation

**Verdict: ✓ ship Phase 4.**

**The problem (iter-11 q3):** "In Patrick Winston's MIT lecture on effective public speaking, what does he mean by 'verbal punctuation' and why does it matter?" — gold zettel `yt-effective-public-speakin` retrieved at top-1 AND cited as `primary_citation`, BUT critic emitted `unsupported_with_gold_skip` AND user saw `"I can't find that in your Zettels"`. This has been silently broken since iter-09 RES-1 introduced the gold-skip path (commit `82f202d`).

**Root cause** (verified by research agent 1 against `orchestrator.py:908-989`): the citation-validation block at L908-919 substitutes `REFUSAL_PHRASE` and clears `used_candidates` when `has_valid_citation` fails on the synth's draft. By the time the `unsupported_with_gold_skip` branch at L977-985 runs, `answer_text` is already `REFUSAL_PHRASE`; the branch wraps the refusal in the `_GOLD_RETRIEVED_DETAILS_TAG` ("Answer reflects retrieved sources") — masking a refusal as if it were a confident answer.

**The fix:** at `orchestrator.py:977`, gate the gold-skip path with:
```python
if (skip_reason == "unsupported_with_gold_skip"
    and answer_text != REFUSAL_PHRASE
    and has_valid_citation(generation.content or "", valid_ids)):
    # apply _GOLD_RETRIEVED_DETAILS_TAG
else:
    # fall through to unsupported_no_retry — honest "best draft" tag
```

**Why this matters cross-Kasten:** the bug fires whenever the synth model drops the `[id=...]` tag (common with shorter context windows / streaming truncation). The fix downgrades the silent mask to an honest "best draft" tag — better UX, more accurate scoring (q3-shape no longer counts as gold@1 = true while refused).

**Caveat:** the underlying corpus question — does `yt-effective-public-speakin` actually contain "verbal punctuation" verbatim? — is not solved by the orchestrator fix. q3 may still refuse honestly. The fix stops MASKING; it does not make q3 PASS unless the zettel content supports the phrase. Confidence: high on cause, medium on sufficiency.

**Iter-12 follow-up (new):** add a corpus-truth audit task to verify each iter's gold expectations match the actual zettel content. Out of iter-12 scope; logged as iter-13 carry-over.

**Pitfalls / cons NOT to take:**
- DO NOT remove the L908-919 citation-validation — it's the safety net against fabricated citation IDs.
- DO NOT loosen `has_valid_citation` to accept any close-match — strict-tag-only is correct.

**Where this lands:** [PLAN.md](PLAN.md) Phase 4 / Task 8.

---

## Class Q5 — percentile-based magnet exemption (replaces static `>= 0.20`)

**Verdict: ✓ ship Phase 5. Replaces the iter-11 Class A `> 0.0` exemption.**

**The problem (iter-11 q5):** "Across these zettels, what is the implicit theory of how a knowledge worker should structure a day..." retrieval put `gh-zk-org-zk` (the recurring magnet) at top-1 and as primary_citation. Class A's exemption at `hybrid.py:251-254` uses `_title_overlap_boost > 0.0`. Incidental token-overlap (≈0.05 from "knowledge"/"structure" tokens) exempted the magnet from BOTH the score-rank delta AND the title-secondary damp. iter-10's title-damp at floor 0.10 was the only knob keeping `gh-zk-org-zk` off top-1 for THEMATIC.

**Why `>= 0.20` static (the audit-superseded proposal) is wrong:**
- 0.083–0.20 is the calibration grey-zone; legitimate verbatim-but-imperfect titles ("AI agents" → "AI agents in 2026", boost ≈ 0.125) lose exemption.
- Static threshold is over-fit to the KM Kasten; opaque-title or single-author Kastens break.

**The fix (percentile-based, dynamic):**
1. In `_apply_score_rank_demote`, compute the **75th-percentile boost** over the query's candidate pool: `boost_p75 = percentile([c.metadata.get("_title_overlap_boost", 0.0) for c in candidates], 75)`.
2. Exemption fires only when `c._title_overlap_boost >= max(boost_p75, _TITLE_OVERLAP_DEMOTE_FLOOR)` (lower bound at the existing 0.10 floor).
3. Plus K3 score-gap bypass (skip the entire gate when `top1/top2 ≥ 1.5`).

**Why this generalizes:**
- In sparse pools where one candidate has full equality (boost ≈ 0.40) and others have 0.0, the 75th percentile is ≈ 0 → only the 0.40 candidate exempts. Correct.
- In dense pools with many partial matches, the 75th percentile rises naturally → only top-quartile boosts exempt. Correct.
- Self-tunes per query without an env knob; survives any Kasten phenotype.

**Class B name-overlap inversion bypass (iter-11):** apply the same percentile gate. The iter-11 `> 0` rule fires for MULTI_HOP too (per agent 1 row 8) — that wasn't intentional; iter-12 unifies both gates on the percentile primitive.

**Citations:**
- [DynamicRAG (arXiv 2505.07233, 2025)](https://arxiv.org/html/2505.07233v1) — query-conditioned adaptive reranking.
- [Tonellotto et al., Dynamic Trade-Off Prediction (arXiv 1610.02502)](https://arxiv.org/abs/1610.02502) — query-specific cutoff tuning.
- [Weaviate Cross-Encoders as Reranker](https://weaviate.io/blog/cross-encoders-as-reranker)

**Pitfalls / cons NOT to take:**
- DO NOT use a different percentile than 75 without staging A/B — 80 is too tight, 60 too loose.
- DO NOT remove the floor `max(p75, 0.10)` — single-token boosts < 0.10 should never exempt.
- DO NOT extend the percentile to anchor_nodes — anchor exemption stays unconditional (anchored = strong signal, not statistical).

**Edge cases:**
- All boosts identical (e.g. all 0.20) — percentile = 0.20 = candidate's own boost; exemption fires uniformly. Correct (or no-op).
- Empty boosts (no title overlap anywhere) — p75 = 0; floor 0.10 binds; nothing exempts. Correct.

**Where this lands:** [PLAN.md](PLAN.md) Phase 5 / Task 9.

---

## Class Q7 — router regex with 3 guards + A1 few-shot prompt

**Verdict: ✓ ship Phase 6.**

**The problem (iter-11 q7):** "Anything about commencement?" classified as LOOKUP by the LLM router (gemini-2.5-flash-lite, zero-shot, no examples in prompt). iter-11 Class D's gazetteer expansion was THEMATIC-gated → never fired. Router code (`router.py`) is **unchanged since iter-09 commit `97b05fc`**; the apparent "regression" is LLM nondeterminism.

**The fix (two parts):**

**Part 1: deterministic regex override** at `router.py:151-171` BEFORE the LLM-fallback word-count rule:
```python
_VAGUE_DISCOVERY_PATTERN = re.compile(
    r"^\s*(anything|something|stuff|things|info|notes?)"
    r"\s+(about|on|regarding|re|around|related to)\b",
    re.IGNORECASE,
)
```
**With three guards** (audit-mandated):
1. **Negation guard:** `(?!.*\b(not|isn'?t|except|excluding|without)\b)` — closes "Anything NOT about X" false-trigger that would expand the negated subject.
2. **Length guard:** `len(query.split()) < 25` — preserves iter-09 long-query MULTI_HOP upgrade.
3. **Proper-noun guard:** skip if query contains a capitalised non-leading token OR a year token `\b(19|20)\d{2}\b` — preserves precision for "Anything about Stanford 2005" (LOOKUP shape).

**Part 2: A1 few-shot router prompt.** Add 8–12 balanced labeled examples (2 per class) to `_ROUTER_PROMPT` at `router.py:16`. Eliminates LLM noise structurally for ALL classes, not just q7. Cost: ~250 prompt tokens/call (cache absorbs repeats).

**Bump `ROUTER_VERSION` v3 → v4** at `router.py:21` to invalidate the 24h `_ROUTER_CACHE` (otherwise stale `lookup` responses persist for 24h after deploy).

**Why both parts:**
- Regex catches ~5% of high-precision shapes deterministically (vague-discovery).
- Few-shot lifts the 60–70% the LLM still handles. Industry-standard hybrid pattern ([LlamaIndex Routers](https://docs.llamaindex.ai/en/stable/module_guides/querying/router/), [TDS Routing in RAG](https://towardsdatascience.com/routing-in-rag-driven-applications-a685460a7220/), [NVIDIA llm-router](https://github.com/NVIDIA-AI-Blueprints/llm-router), all retrieved 2026-05-06).

**Citations:**
- [Prompting Guide — Classification](https://www.promptingguide.ai/prompts/classification) — few-shot for classification, 2-3 examples per class, balanced.
- [The Few-shot Dilemma (arXiv 2509.13196, 2026)](https://arxiv.org/html/2509.13196v1) — keep examples ≤12, balanced.
- [Patronus AI Agent Routing](https://www.patronus.ai/ai-agent-development/ai-agent-routing) — production routers use confidence gating with rule fallback.

**Pitfalls / cons NOT to take:**
- DO NOT broaden the discovery regex to bare `\babout\b` — would catch "What did Jobs say about Stanford?" (LOOKUP) and contaminate q2/q11.
- DO NOT widen the rewriter gate from THEMATIC to LOOKUP — Class D was scoped to THEMATIC for a reason; the regex routes q7 to VAGUE which already runs gazetteer at `transformer.py:38-44`.
- DO NOT skip the `ROUTER_VERSION` bump — stale cache will swallow the fix.
- DO NOT use more than 12 few-shot examples — accuracy degrades past that point per arXiv 2509.13196.

**Edge cases:**
- "Got anything on X?" / "Show me notes about X" — regex misses (anchored `^anything|something|...`). Falls through to LLM (now improved by A1 few-shot). Acceptable.
- Misspelled "anything bout commencement" — regex misses; LLM with few-shot handles it.

**Where this lands:** [PLAN.md](PLAN.md) Phase 6 / Tasks 11–12.

---

## Class D-out — REMOVE static gazetteer

**Verdict: ✓ ship Phase 6.**

**The problem:** iter-11 Class D added `expand_vague` (a hand-curated gazetteer at `vague_expander.py`) to short-THEMATIC queries. The keys are over-fit to English/tech vocabulary (`commencement → graduation/stanford/...`). On any non-tech or non-English Kasten the gazetteer is provably WORSE than no expansion (audit agent 6: "breaks on cooking Kasten / non-Latin / domain-mismatch").

**The fix:** remove the static gazetteer call from `transformer.py:62-69`. Short-THEMATIC queries that would have triggered Class D now route through the **VAGUE branch** (which Class Q7 routes them to via the regex override). The VAGUE branch at `transformer.py:38-44` ALREADY runs `expand_vague` — but that itself is also static. iter-13 will replace `expand_vague` with K6 (one-shot LLM expansion gated on detection of a short-THEMATIC/VAGUE query). For iter-12, the gazetteer keys remain in `vague_expander.py` because VAGUE-branch consumers still call them; the change is just removing the SECOND call from THEMATIC.

**iter-13 carry-over (K6):**
- Replace `expand_vague` with a one-shot LLM call to gemini-2.5-flash-lite when short-VAGUE/THEMATIC fires.
- Delete `vague_expander.py:_GAZETTEER` and content-token logic.
- Cost: ~200 ms latency + ~$0.0001/query (small flash-lite call).

**Pitfalls / cons NOT to take:**
- DO NOT remove `vague_expander.py` outright in iter-12 — VAGUE-branch consumers still depend on it. Removal is a clean iter-13 task.
- DO NOT add gazetteer keys for new domains (cooking, finance, etc.) — that's the over-fitting trap iter-11 fell into.

**Where this lands:** [PLAN.md](PLAN.md) Phase 6 / Task 13.

---

## Class S — scoring fixes (E1, q9, over-refusal, latency rename, cites-render, per-class breakdown)

**Verdict: ✓ ship Phase 7.**

**The problem (iter-11):** five inconsistencies between `scores.md` (`gold@1 = 0.6154`) and `timing_report.md` (`end_to_end_gold_at_1 = 0.7143`) for the SAME run. Manual computation: `gold_at_1_user_visible = 0.5385 (7/13)`.

| # | Symptom | Source | Fix |
|---|---|---|---|
| 1 | scores.md vs timing_report disagree on gold@1 | `_holistic_metrics` applies E1; `_qa_summary` (`eval_iter_03_playwright.py:1444-1467`) does not | Add E1 exclusion to `_qa_summary` |
| 2 | q9 `gold_at_1=true` despite `primary_citation=null` | hardcoded special-case at `eval_iter_03_playwright.py:1238-1239` | Set `expected_empty=True`; do not set `gold_at_1` for refusal-expected |
| 3 | q3 counted as gold@1 pass while refused | neither aggregator deducts `over_refusal` | Add `gold_at_1_user_visible = gold_at_1 AND NOT over_refusal AND NOT refused` headline |
| 4 | `latency_ms_server` 1-2 s vs `p_user` 30-50 s | misnamed: synth-after-TTFT timer (verdict H4) | Rename to `latency_ms_synth_after_ttft`; derive `latency_ms_server_total` from `p_user_complete_ms` |
| 5 | q3 row shows `cites: 1` while answer is refusal | render path never checks `refused` | Render `cites: 0 (refused)` when `refused=True` |
| 6 | (NEW) no per-class breakdown; aggregate hides class regressions | render path only emits aggregate metrics | Per-class table for every metric |

**New iter-12 headline metrics** (replacing the iter-11 set):

```
accuracy_user_visible      = count(gold_at_1 AND NOT over_refusal AND NOT refused) / n_scored  (E1-conditioned)
gold_at_k                  = recall@K for K∈{1,3,8} (E1-conditioned)
faithfulness               = RAGAS LLM-as-judge
answer_relevancy           = RAGAS LLM-as-judge
over_refusal_rate          = count(refused AND gold_in_retrieved) / n_scored
under_refusal_rate         = count(answered AND faithfulness<0.5) / n_answered
p_user_complete_ms p50/p95 = end-to-end TTLT
within_budget              = on p_user_complete_ms (NOT on latency_ms_server)
```

Per-class breakdown table (LOOKUP / THEMATIC / MULTI_HOP / STEP_BACK / VAGUE / ADVERSARIAL_NEGATIVE) for every headline metric.

**Citations:**
- [RAGAS available metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
- [Langfuse RAG observability and evals (2025)](https://langfuse.com/blog/2025-10-28-rag-observability-and-evals)
- [Evidently RAG evaluation guide](https://www.evidentlyai.com/llm-guide/rag-evaluation)
- [Cleanlab Hallucination Detection benchmarking](https://cleanlab.ai/blog/rag-tlm-hallucination-benchmarking/)
- [TACL Survey of Abstention](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00754/131566/)

**Pitfalls / cons NOT to take:**
- DO NOT widen E1 N/A treatment to other "soft fail" cases — `expected=[]` is a precise contract.
- DO NOT silently drop refusal-expected rows from the report — the operator wants the count visible.
- DO NOT keep `latency_ms_server` as the legacy field — every reader will get confused; full rename, even though it touches every prior iter's reader code.

**Where this lands:** [PLAN.md](PLAN.md) Phase 7 / Tasks 14–17.

---

## Class W — composite reweight to trust-first formula

**Verdict: ✓ ship Phase 7.**

**The problem:** current composite weights `{chunking: 0.10, retrieval: 0.25, reranking: 0.20, synthesis: 0.45}` reward stage diagnostics but conflate retrieval-stage metrics with user-visible outcome. `synthesis` at 0.45 means a confident hallucination scores high if `answer_relevancy` is high — Goodhart-gameable.

**The fix:** new composite formula (RAGAS/Langfuse 2025 industry consensus):
```
trust       0.40   # max(0, faithfulness − under_refusal_rate)
accuracy    0.30   # accuracy_user_visible
retrieval   0.15   # 0.5*gold@1 + 0.3*gold@3 + 0.2*gold@8 (E1-conditioned)
calibration 0.10   # 1 − over_refusal_rate (penalizes refuse-everything strategy)
latency     0.05   # within_budget_rate on p_user_complete_ms
```

Live in `docs/rag_eval/_config/composite_weights.yaml`, hash-locked. iter-04..iter-11 stay locked at the OLD weights for historical comparability; iter-12+ uses the new weights.

**Why these weights:**
- `trust + accuracy = 70%` matches "what customers feel" (Cleanlab/Langfuse 2025 dashboards).
- `retrieval` capped at 15% prevents retrieval over-tuning at synthesis expense.
- `calibration` is the explicit anti-Goodhart guard against #3 (refuse-everything strategy → high accuracy_user_visible but high over_refusal_rate cancels out).
- `latency` weight intentionally low — once within budget, marginal latency improvements don't move the customer experience needle.

**Pitfalls / cons NOT to take:**
- DO NOT recompute prior iters' composite under new weights for headline comparison — they were not designed to optimize against this formula. Document both for historical context.
- DO NOT add more terms — five is the upper limit for legibility (RAGAS is four).

**Where this lands:** [PLAN.md](PLAN.md) Phase 7 / Task 18.

---

## Already-merged context (background for executor)

| Commit | Subject | Status |
|---|---|---|
| `b059a5a` | Phase 1/Task 2 wiring patch (per-entity loop + query_metadata thread-through) | iter-11 first run; rolled back via env flag |
| `04a67e9` | feat: anchor exemption on magnet gate plus log (Class A) | iter-11 final; superseded by Q5 percentile |
| `2e8c5ca` | feat: name overlap override on thematic tiebreak (Class B) | iter-11 final; superseded by Q5 percentile |
| `f6f223b` | feat: short thematic gets vague expansion path (Class D) | iter-11 final; **REMOVED** in iter-12 Class D-out |
| `700813b` | feat: class conditional critic threshold offsets (Class F) | iter-11 final; kept but operation gated on iter-13 D3 NLI |
| `1df96e6` | scoring E1 / q9 N/A handling (Class E1) | iter-11 final; extended in iter-12 Class S |
| `2c09295` | per-stage timing logs (P17) | iter-10 final; iter-12 surfaces in `verification_results.json` |

iter-11 final-eval evidence (used by iter-12 audit):
- Container OOM kill: `docker inspect zettelkasten-green oom_killed=true` (run `25416765805`)
- Burst 502 rate 50% (6/12 of burst pressure check)
- q13 catastrophic spike: `latency_ms_server=14118 ms`, empty pool, `retry_budget_exceeded`
- 6/14 queries regressed within-budget (q1, q2, q6, q8, q11, q12) — cumulative loop-blocking drift

---

## Quick-reference: env flags introduced or modified by iter-12

| Flag | Default | Phase / Task | Purpose |
|---|---|---|---|
| `RAG_EXECUTOR_MAX_WORKERS` | `8` | 1 / 1 | Class P — ThreadPoolExecutor sizing for `to_thread` |
| `RAG_RPC_GLOBAL_SEMAPHORE` | `8` | 1 / 1 | Class P — global DB-side concurrency cap |
| `RAG_HTTPX_MAX_CONNECTIONS` | `16` | 1 / 1 | Class P — supabase-py httpx pool bump |
| `RAG_HTTPX_MAX_KEEPALIVE` | `8` | 1 / 1 | Class P — httpx keepalive headroom |
| `RAG_ENTITY_GATHER_SEMAPHORE` | `3` | 1 / 1 | Class P — per-request entity-gather fan-out cap |
| `RAG_ANCHOR_BOOST_ENABLED` | `false` (Phase 1) → `true` (Phase 2) | 1 / 1 + 2 / 5 | Class P — anchor-boost re-enable |
| `RAG_ANCHOR_SEED_INJECTION_ENABLED` | `false` (Phase 1) → `true` (Phase 2) | 1 / 1 + 2 / 5 | Class P — anchor-seed inject re-enable |
| `RAG_SCORE_RANK_GAP_BYPASS` | `1.5` | 3 / 6 | Class K3 — confidence-gap factor for magnet gate |
| `RAG_RETRY_GAP_BYPASS` | `1.5` | 3 / 6 | Class K3 — confidence-gap for skip-retry |
| `RAG_KASTEN_BOOTSTRAP_WINDOW` | `50` | 3 / 7 | Class K4 — rolling-window size for magnet-spotter |
| `RAG_TITLE_OVERLAP_PERCENTILE` | `75` | 5 / 9 | Class Q5 — percentile for title-overlap exemption |
| `RAG_TITLE_OVERLAP_FLOOR_FALLBACK` | `0.10` | 5 / 9 | Class Q5 — lower bound on percentile (the existing iter-10 floor) |
| `RAG_ROUTER_VERSION` | `v4` (iter-12 bump) | 6 / 11 | Class Q7 — invalidates iter-11 cached router responses |

`RAG_SHORT_THEMATIC_THRESHOLD` (iter-11) is **removed** — Class D-out unships the THEMATIC-gated gazetteer.

`RAG_PARTIAL_NO_RETRY_FLOOR_OFFSET_THEMATIC` and `RAG_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR_OFFSET_THEMATIC` (iter-11 Class F) **remain** but are the fallback path now that Class K3 fires first.

---

## Quick-reference: Supabase migrations introduced by iter-12

| Migration | Phase / Task | Purpose |
|---|---|---|
| `kg_kasten_metrics` table | 3 / 7 | Class K4 — rolling per-Kasten top-1 frequency record |

DDL skeleton (full migration goes in `supabase/migrations/iter12_kg_kasten_metrics.sql`):
```sql
create table if not exists kg_kasten_metrics (
  id bigserial primary key,
  sandbox_id uuid not null,
  top1_node_id text not null,
  ts timestamptz not null default now()
);
create index if not exists kg_kasten_metrics_sandbox_ts_idx on kg_kasten_metrics(sandbox_id, ts desc);
```

Background-prune job (delete rows older than 90 days) added to `kg-jobs` cron.

---

## Success criteria

iter-12 final eval MUST hit ALL of:

| Metric | Target | Source |
|---|---|---|
| Composite (new weights) | ≥ 85 | Approved by user |
| accuracy_user_visible | ≥ 0.85 | Approved by user (NEW headline; replaces gold@1_unconditional) |
| over_refusal_rate | ≤ 0.10 | New iter-12 metric |
| under_refusal_rate | ≤ 0.05 | New iter-12 metric |
| RAGAS faithfulness | ≥ 85 | Held |
| RAGAS answer_relevancy | ≥ 80 | Held |
| within_budget on p_user_complete_ms | ≥ 0.85 | Implicit |
| Burst 503 rate | ≥ 0.08 | Held from iter-10 |
| Burst 502 rate | 0% | Multi-user safety; iter-12 PATH_F should close this |
| Per-query failures (q1–q14, q9 expected_empty) | 0 | All non-adversarial queries pass |
| Worker OOM events during eval | 0 | Multi-user safety |
| `event_loop_lag_ms` p95 (new instrumentation) | < 50 ms | Class P validation |

**Projected accuracy_user_visible trajectory** (one fix at a time, no regression):

| After fix | accuracy_user_visible | within_budget | Comments |
|---|---:|---:|---|
| iter-11 baseline | 0.5385 | 0.50 | (E1 + over-refusal applied to verified rows) |
| + Class P (PATH_F + anchor-boost re-enable) | 0.69 | 0.85 | recovers q10 + within_budget on q1/q2/q6/q8/q11/q12 + closes q13 spike |
| + Class K3 + K4 (gap bypass + bootstrap) | 0.77 | 0.85 | softens magnet-spotter + retry-skip on clear winners |
| + Class Q3 (gate skip-path fix) | 0.85 | 0.85 | recovers q3 from over-refusal |
| + Class Q5 (percentile exemption) | 0.92 | 0.85 | recovers q5 + de-magnetises THEMATIC |
| + Class Q7 + A1 (regex + few-shot) | 1.00 | 0.85 | recovers q7 |
| + Class S (scoring fixes) | 1.00 | 0.85 | accurate metric reporting |
| + Class W (composite reweight) | 1.00 | 0.85 | trust-first headline |

The exact trajectory assumes no regressions; the cross-class regression fixture + new per-Kasten phenotype fixtures are the safety net.

---

## iter-13 carry-overs (out of iter-12 scope)

### Original carry-overs (from round-1 author)

| Item | Why deferred | Trigger condition |
|---|---|---|
| **D3 NLI citation post-validation** (DeBERTa-v3 int8 ~180 MB) | Adds new model dependency; staging memory test required | Required before any further Class F floor relaxation; required if `under_refusal_rate` remains > 0.05 after iter-12 |
| **K5 per-Kasten telemetry emission** | Langfuse-style observability layer; needs separate infra design | Required before scaling to non-KM Kastens in production |
| **K6 LLM gazetteer replacement** | Cost analysis + LLM call latency budgeting | Replaces static `vague_expander.py` once iter-12's reduced gazetteer footprint is validated |
| **PATH_C Postgres function collapse** | pgvector seq-scan trap (issue #703) requires `EXPLAIN ANALYZE` validation | Only if PATH_F latency proves insufficient (`event_loop_lag_ms` p95 > 50 ms despite sized executor) |
| **Corpus-truth audit** for q3-shape | Out of scope for orchestrator fix | Verifies each iter's gold expectations match actual zettel content |
| **`latency_ms_synth_after_ttft` deprecation** | Keep for one iter for transition | Drop after iter-13 readers are migrated |

### iter-12-derived carry-overs (from audit rounds 1+2 + R1-R6 research)

| Item | Source | Trigger condition for iter-13 |
|---|---|---|
| **R1 dynamic floor alternatives** (per-Kasten p70 / NLI voting / conformal abstention / adaptive retry budget) | Task 33 + Task 35 audit script | `audit_ce_distribution.py` decision = `ACTIVATE_A1_PER_KASTEN_FLOOR`. Otherwise close. |
| **R3 Tier-2 monitors** (LettuceDetect / ModernBERT span-NLI ~150 MB) | Task 30 deferral | If iter-12 `under_refusal_rate > 0.05` AND droplet RAM headroom > 250 MB after Class P stable |
| **R3 Tier-3 monitors** (RAGAS TestSet Generator per-Kasten corpus self-test) | Task 30 deferral | After flash-lite quota planning + cost analysis (~5k tokens/zettel × Kasten count) |
| **R4 bandit "extend canary → all" gate** | Task 31 / Step 10 | All 5 acceptance criteria hold for ≥7 days on canaries 1+2+3 (`accuracy_user_visible ≥ baseline+0.5pp`, zero auto-rollbacks, ≥2 arms with effective pulls ≥30, posterior entropy <1.0 nats on ≥1 canary, operator review) |
| **R4 bandit pathology resolution** | Task 31 / Step 8 | If any of 5 alerts fire (`posterior_mode_flips_24h>3`, `posterior_entropy>1.3 nats` after 200 pulls, `min/max arm pulls < 0.05`, decision latency p99 > 5 ms, DB upsert conflict > 5%) |
| **R5 cross-Kasten alias clustering + `matched_via` uplift attribution** | Task 28 deferral | After ≥30 days of `matched_via` log data; Phase F `audit_alias_uplift.py` script |
| **R6 per-language confidence threshold tuning** | Task 29 deferral | After ≥30 days of `query_lang_hint` data; per-language calibration set |
| **R6 dynamic per-Kasten confidence floor** | Task 29 deferral | If iter-12 `over_refusal_rate > 0.10` on any specific Kasten phenotype |
| **R2 demote-slope sensitivity calibration** | Task 32 caveat | If iter-12 `score_rank_demote ... margin=...` log shows `p10 < 0.05` on ≥5% of THEMATIC queries → tune slope OR introduce per-Kasten override |
| **Task 23 STATIC_BODY → operator-override governance model** | Task 23 ops rollout | After ≥1 emergency rollback uses `compose/.env.local` overlay; codify the operator-override pattern in runbook |
| **Task 31 `bandit_warm_start.py` automation** | Task 31 / Step 6 | Convert one-shot script to scheduled cron when ≥3 Kastens cross n_min=20 threshold |
| **Task 36 audit-script reporting expansion** | Task 36 caveat | Add per-class breakdown table, monitor-pass/fail traffic-light, RAGAS Tier-2 NLI section once D3 lands |
| **R1 corpus-truth audit (q3-shape)** | Task 33 + Task 30 | Verify each iter's gold expectations match actual zettel content; merges with original "Corpus-truth audit" line above |

### Acceptance criteria for closing iter-13 carry-overs

For each carry-over above, iter-13 PLAN's "Phase 0 / Task 1" must explicitly cite:
1. Whether the trigger condition fired during iter-12 (yes/no with telemetry citation)
2. If yes — the iter-13 task that addresses it
3. If no — explicit `CLOSE` decision with rationale

This prevents indefinite carry-over accumulation across iters.

---

## Task 34 PRE-Phase-1 forensic finding (2026-05-07)

**gh run consulted:** `25495521151` (workflow `read_recent_logs.yml`, triggered 2026-05-07T12:23Z, `--since 2026-05-05T20:40:00Z --lines 2000`)

**Log line counts in the iter-11 final-eval window (2026-05-05T20:40Z onward):**

| Diagnostic log line | Count in window |
|---|---:|
| `entity_anchor_resolve` | 9 |
| `anchor_seed_inject` | 1 |

**Verdict: anchor-boost ACTIVE — the iter-11 rollback DID NOT land.**

**Evidence summary:**

1. The current `compose/.env` on the production droplet (as of 2026-05-07) does NOT contain `RAG_ANCHOR_BOOST_ENABLED` or `RAG_ANCHOR_SEED_INJECTION_ENABLED`. When absent, `hybrid.py:37-39` and `hybrid.py:46-48` default both flags to `"true"`.
2. The windowed docker log dump (`--since 2026-05-05T20:40:00Z`) — which covers the iter-11 final-eval window — contains 9 occurrences of `entity_anchor_resolve` and 1 occurrence of `anchor_seed_inject`. Both log strings are only emitted when the respective flags are active: `entity_anchor_resolve` from `entity_anchor.py:67` fires inside `resolve_anchor_nodes`, which is only called when `_ANCHOR_BOOST_ENABLED=true`; `anchor_seed_inject` from `hybrid.py:546` fires only when `_ANCHOR_SEED_ENABLED=true` and the `_should_inject_anchor_seeds` gate passes.
3. The iter-11 rollback instruction (iter-11/RESEARCH.md §Phase 8 root cause, line 202) was to manually `echo RAG_ANCHOR_BOOST_ENABLED=false >> /opt/zettelkasten/compose/.env` and restart. The Task 23 forensic finding (PLAN.md, referenced in iter-12 RESEARCH.md §iter-13 carry-overs) is that `deploy-droplet.yml`'s `tee /opt/zettelkasten/compose/.env` is unconditional — any push to master after the manual edit would have wiped it. The iter-11 final-eval commits (`cb5457d`, `700813b`, `fe3e3d7`, `2e8c5ca`, `04a67e9`, `7877315`) were all pushed to master on 2026-05-05 between 09:14Z and 09:30Z UTC, and the eval ran at 20:43Z–20:55Z on the same day. The manual `=false` edit — if it was ever applied — was wiped by one of those pushes, and the flag was not present in `compose/.env` at eval time (hence defaulting to `true`).

**Correction note for "iter-11 outcome that motivates iter-12" section:**

The per-query failure-mode table in that section attributes q10's failure to "rolled-back anchor-boost; iter-11 Class C dormant under `RAG_ANCHOR_BOOST_ENABLED=false`". This attribution is INCORRECT. The anchor-boost and anchor-seed-injection were ACTIVE during the iter-11 final eval. q10's `gold@1=false` therefore reflects a genuine retrieval failure with anchor-boost active — meaning the anchor wiring (`b059a5a`) did not fix q10 even when running. The Class C per-entity loop resolved 0 anchors for q10's entities (the anchor nodes were likely absent or title-mismatched in the KG at that point). Implication: iter-12 Class P's plan to "re-enable anchor-boost" (Phase 2) is operationally moot — it was never disabled. Phase 2 should instead focus on verifying the anchor-boost boost path is actually injecting correctly for q10's entity set, and the real fix for q10 is PATH_F's `asyncio.to_thread` wrapping (so the per-entity RPC loop doesn't saturate the event loop and time out silently). The iter-12 Phase 1 posture (`RAG_ANCHOR_BOOST_ENABLED=false` in STATIC_BODY) will be the FIRST TIME the flag is actually false in production — not a rollback restoration.

Additionally: the q5 failure-mode attribution ("iter-11 Class A title-overlap exemption with `>0.0` threshold accidentally exempted the magnet via incidental token-overlap") remains valid regardless of anchor-boost state, since Q5's mechanism (title-overlap exemption in `_apply_score_rank_demote`) is independent of the anchor flags.

**No change to Class P priority:** PATH_F (asyncio.to_thread) is still the critical fix — the 50% burst 502 rate and the OOM kill (`docker inspect zettelkasten-green oom_killed=true`, confirmed in this run) both occurred WITH anchor-boost active. The sync-blocking RPCs from both the anchor-resolve loop and the main hybrid path are the structural cause, regardless of flag state.

---

## Class P implementation note — semaphore acquisition order (2026-05-07)

PLAN.md L201-205 sketches the rpc_call wrapper with `_RPC_SEM` (global) as the outer context manager and `request_sem` (per-request) as the inner. The implemented code in `_async_helpers.py` reverses that nesting (`request_sem` outer, `_RPC_SEM` inner). The reversal avoids priority inversion: with PLAN's order, a coroutine that holds a scarce global slot can be blocked waiting on its own per-request gate, idling a global slot. With the implemented order, the cheap per-request slot is held while the scarce global slot is awaited — the standard "acquire cheap before scarce" pattern. Effective concurrency under steady state is identical (`min(request_sem, _RPC_SEM)`); only queueing dynamics differ. User-approved deviation 2026-05-07.

---

## Phase 1 deploy incident (2026-05-07)

Initial Phase 1 push (`051a732` → origin/master) failed at the Docker Compose
startup step. Commit `cdc1959` (Task 23) added `/opt/zettelkasten/compose/.env.local`
as a second `env_file` entry on both compose files. Docker Compose v2's default
behavior is `required: true` for every env_file entry; the file did not exist
on the droplet, so blue and green both failed to start, and the rollback to the
prior green also failed (same missing file). Caddy lost both upstreams;
zettelkasten.in served 502 for approximately 8 minutes (first failure
2026-05-07T14:06:44Z → touch + rerun triggered 14:14:23Z).

Recovery (A+B, user-approved):
- A: SSH to droplet via local `zettelkasten_deploy` key; `touch /opt/zettelkasten/compose/.env.local`
  as the `deploy` user (owns the compose dir — no sudo needed); mode 600; re-ran
  the failed GHA deploy run `25500631032`. Service restored.
- B: Hotfix commit changes the env_file entries to the explicit `path:` /
  `required:` form so `.env.local` is optional. Docker Compose v5.1.1 on the
  droplet fully supports `required: false`. Future droplet rebuilds with no
  `.env.local` will start cleanly. Tests (`test_compose_blue_has_env_local_overlay`,
  `test_compose_green_has_env_local_overlay`) still pass — both use a substring
  check for `.env.local` which is present in the new `path:` syntax.

Lesson: any `env_file` entry added to a compose file should be tested against a
fresh droplet snapshot OR explicitly marked `required: false`. Add to the iter-13
PLAN authoring checklist.

---

## Phase 1 burst-12 probe result (2026-05-07, post-incident-recovery)

- Run: `python ops/scripts/burst_pressure_probe.py --concurrency 12 --duration 60 --target https://zettelkasten.in`
- Total requests: 2059
- 502 rate: 0.0
- 503 rate: 0.0 (retry_after expected)
- Pre-burst /api/health event_loop_lag.p95_ms: N/A (single post-burst snapshot)
- Post-burst event_loop_lag.p95_ms: 0.574
- Latency p50/p95/p99 (ms): 157.0 / 1062.0 / 1844.0
- Verdict: PASS
- Phase 2 gate: PROCEED
- Master HEAD at probe time: 255cb64

---

## Phase 2 anchor flip (2026-05-07)

Phase 1 burst-12 probe PASS: total=2059, 502_rate=0, post-burst event_loop_lag.p95=0.574 ms
(see prior section). Anchor flags flipped to true in STATIC_BODY this commit.

Pre-flip state on droplet (commit 255cb64):
- RAG_ANCHOR_BOOST_ENABLED=false
- RAG_ANCHOR_SEED_INJECTION_ENABLED=false

Post-flip state (master HEAD after this commit):
- RAG_ANCHOR_BOOST_ENABLED=true
- RAG_ANCHOR_SEED_INJECTION_ENABLED=true

Per Task 34 forensic finding, this is the FIRST genuinely Phase 2 (since iter-11
final eval ran with anchor-boost active even though operator believed it rolled
back — workflow STATIC_BODY now controls the value definitively).

---

## Phase 2 burst-12 probe result (2026-05-07)

After anchor-boost flip (master HEAD: 3d440c0):
- Total requests: 2005
- 502 rate: 0.0
- 503 rate: 0.0
- Post-burst event_loop_lag.p95: 4.098 ms
- Latency p50/p95/p99: 125.0 / 1328.0 / 4359.0 ms
- Verdict: PASS
- Phase 3 gate: PROCEED

Note: q10 single-query smoke test skipped per operator instruction (2026-05-07):
"I'll run iter-12 myself" — eval is operator-driven. Phase 2 burst probe is
sufficient validation for the Phase 3 gate decision.

---

## Task 33 — Q13 forensic finding + SKIP rationale (2026-05-07)

**Verified from `iter-11/verification_results.json:1149-1180` + droplet logs:**
q13 (multi_hop, gold=`nl-the-pragmatic-engineer-t`) failed not because of the
critic floor, but because retrieval was empty. `latency_ms_server=14118 ms`,
`retrieved_node_ids=[]`, `reranked_node_ids=[]`, `cited_node_ids=[]`,
`critic_verdict=retry_budget_exceeded`. STEP_BACK-mutation retry hit the 12s
`_RETRY_BUDGET_S` `asyncio.wait_for` timeout.

**Why "lower `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR(LOOKUP)` 0.7→0.5" is wrong:**
the proposal targets the post-rerank gold-skip gate. q13 never reached that
gate — retrieval had zero candidates to score. Lowering the floor cannot fix
a query where retrieval itself failed.

**q13's actual fixes live in iter-12 retrieval-stage tasks:**
- Class P (PATH_F) eliminates sync-RPC blocking that competed for thread
  budget against burst neighbours during eval (T1).
- Task 28 (R5 alias canonicalization) makes "Pragmatic Engineer" /
  "product-minded engineer" resolve to `nl-the-pragmatic-engineer-t` through
  the alias array even when the metadata extractor surfaces only fragments.
- Task 29 (R6 confidence-thresholded extraction + cap-3) reduces noise
  entities competing for anchor-resolve RPC budget.
- Task 32 (R2 slot-1 anchor pin) ensures any successfully-resolved anchor is
  never lost to xQuAD's slot-1 = argmax(rrf) collision.

**iter-13+ deferred alternatives (good engineering, but DOES NOT address q13):**

| Alternative | Trigger condition for iter-13 |
|---|---|
| Per-Kasten quantile-calibrated rerank floor (rolling p70) | `audit_ce_distribution.py` decision = `ACTIVATE_A1_PER_KASTEN_FLOOR` (T35 monitor). Otherwise close. |
| Top-K NLI faithfulness voting | iter-12 D3 carry-over (~150 MB DeBERTa); ship if `under_refusal_rate > 0.05` after iter-12 |
| Conformal selective abstention (per-Kasten coverage target) | iter-14+: requires labeled calibration set per Kasten (10-30 graded queries) |
| Adaptive retry budget (`base + 0.5 × first_pass_pool_size`, capped 22s) | MEDIUM confidence per R1; iter-13 carry-over |

**Static `_UNSUPPORTED_WITH_GOLD_SKIP_FLOOR=0.7` REMAINS in iter-12** (CLAUDE.md
guardrail). q13's failure was retrieval-empty, verified. Confidence: HIGH on
diagnosis; HIGH on SKIP-iter-12 verdict; MEDIUM on iter-13 deferral being the
right answer (empirical CE-distribution audit may show static floor is
universally correct).

---

## Task 26 closure (2026-05-07)

Task 26's static `0.85→0.75` demote-factor flip was SUPERSEDED by Task 32's
percentile-derived demote slope (`RAG_SCORE_RANK_DEMOTE_SLOPE=0.20`, factor
clamped in [0.70, 0.90]). Task 26's telemetry survived: `_apply_score_rank_demote`
emits `score_rank_demote class=... n_cands=... slope=... post_top1=... post_top2=... margin=...`
on every gated call. iter-13 can mine the margin distribution from droplet
logs to validate that p10 margin > 0.05 on THEMATIC queries.

---

## Task 36 cross-reference for operator (2026-05-07)

After running `score_rag_eval.py --iter-dir docs/rag_eval/common/knowledge-management/iter-12`,
also run `python ops/scripts/post_iter_audit.py --iter iter-12`. Output:
`iter-12/post_iter_audit.md`. The "Live env-during-eval" section relies on a
pre-populated capture file at `iter-12/_audit/live_env_capture.txt`; populate
via:

```bash
# Git Bash on the machine with droplet SSH access
ssh deploy@<droplet> "docker exec zettelkasten-\$(cat /opt/zettelkasten/deploy/active_color) env" \
    | grep -E "^RAG_" > docs/rag_eval/common/knowledge-management/iter-12/_audit/live_env_capture.txt
```

If SSH is unavailable, the script falls back to the most recent successful
`deploy-droplet.yml` workflow log (requires `gh` CLI authenticated).
