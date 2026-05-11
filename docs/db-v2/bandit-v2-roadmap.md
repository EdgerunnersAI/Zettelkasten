# Bandit v2 Roadmap

**Status:** parked / informational. Not blocking. Annotated 2026-05-11.

## What changed in v1 → v2

The v1 KG carried a posterior-storage table `public.kg_bandit_posteriors` that
let three scheduled jobs persist per-(query_class, kasten_archetype) Thompson-
sampling state across runs:

- `ops/scripts/bandit_decay_job.py` — periodic posterior decay
- `ops/scripts/bandit_health_check.py` — drift monitor
- `ops/scripts/bandit_warm_start.py` — cold-start primer

Phase 6 (commit `e168b38`, 2026-05-10) dropped `public.kg_bandit_posteriors`
along with the rest of `public.kg_*`. The v2 schema does not expose an
equivalent posterior-storage table; it exposes only an **RPC surface**:

- `rag.bandit_read_arms(...)` — read-only arm state
- `rag.bandit_record_outcome(...)` — record one observation

This is a stateless, transactional API — fine for online single-observation
updates but **not** a substitute for batched decay / health / warm-start work
that needs to scan and rewrite many rows.

The three v1 scripts were annotated as LEGACY in commit `d2dff7d` (2026-05-11)
since none of them are scheduled by `.github/workflows/`. They survive in the
tree as breadcrumbs.

## Decision space (for a future iteration)

When per-tenant bandit math returns to scope (target gate per Phase 8.5.B-4
research: ≥1k reward events per `(query_class, kasten_archetype)` cell — see
mem-vault decision `zrUWPShYIYieiXSXi1uzh-Ml`), there are three viable shapes:

### Option A — Restore posterior table in v2 schema

Add `kg.bandit_posteriors` (or `rag.bandit_posteriors`) with the v1 shape:
`(query_class, kasten_archetype, alpha, beta, last_updated_at, ...)`. RLS-gated
to service-role + admin. The three v1 scripts port verbatim (rewrite the
`.schema(...)`).

Pros: closest to v1; easy port.

Cons: re-introduces a table that's only used by background jobs (not the
hot retrieval path). Adds schema surface for what's effectively a key-value
store. RLS on a singleton-ish table is awkward.

### Option B — RPC-only with external state

Persist posteriors in a separate KV store (Redis on the droplet, or Supabase
Edge KV) and expose via RPC. Keeps the schema clean; concentrates bandit
state where it belongs (hot read path).

Pros: no schema bloat; matches the "v2 RPC-only surface" instinct of the
current rag_bandit_* design.

Cons: adds a dependency (Redis or KV layer). Operational overhead for a
feature that may not ship.

### Option C — Drop bandit math entirely

Stay on the raw-count boost (Phase 8.5.B-4 `_compute_kasten_signal_boost`)
indefinitely. Industry pattern at <10k events per tenant (Cohere Rerank-4,
Voyage AI, Glean — none ship per-tenant bandits at this scale).

Pros: zero new infra; matches industry consensus for small tenants.

Cons: gives up the bandit option for high-traffic tenants if and when
they appear.

## Recommendation

**Pick C until the phase-transition gate fires** (≥1k events per cell,
golden-set nDCG plateau, offline counterfactual lift). Until then, raw-count
boost + `_KASTEN_SIGNAL_COLD_START_THRESHOLD` is sufficient and matches the
industry-standard sparse-feedback heuristic.

When the gate fires, prefer **Option A** (table in v2 schema) over Option B
(external KV) because the operator burden of an extra service is
disproportionate for a single-bandit use case.

## Citations

- Phase 8.5.B-4 raw-count boost decision: mem-vault `zrUWPShYIYieiXSXi1uzh-Ml`
- v1 → v2 surface gap: commit `d2dff7d` (LEGACY annotations on the three scripts)
- v1 DROP: commit `e168b38`, `supabase/website/_v2/15_drop_legacy_tables.sql:163`
- Industry pattern at sparse-feedback scale: Cohere Rerank-4 (2024), Voyage AI
  (2024), Glean retrieval blog (2024–2025), Stanford Thompson-Sampling Tutorial
  (N ≥ 30 floor), Xiang & West KDD 2022, arXiv 2510.26284 (2025)
