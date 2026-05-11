# DB v2 Purge — Phase 8 Closeout Complete (2026-05-11)

## Final state at end of Phase 8

- 2 users (Naruto + Zoro), both UUID-authed.
- 3 zettels backfilled to Naruto's workspace at Phase 5.
- All v2 schemas (`core, content, kg, rag, pipelines, billing`) live and populated.
- Legacy `public.*` tables and v1 RPCs dropped (Phases 6, 7.2, 8.0.6). The 6 retained `public.pricing_*` tables + 6 RPCs were dropped in `31_drop_legacy_pricing.sql` (applied 2026-05-11) after the pre-T6 audit confirmed zero live website refs.
- `website/core/supabase_kg/` directory **deleted** in Phase 8.0.6 (paired with the T6 DROP).
- `website/features/kg_features/` partial cleanup landed: `retrieval, nl_query, entity_extractor` deleted; `analytics, embeddings` retained as pure-compute helpers (allow-listed via CI guard).

## Phase 8 commits (all on master, NOT pushed yet)

| SHA      | Subject                                                         |
|----------|-----------------------------------------------------------------|
| 0e88b1d  | feat(v2): port pricing_active_plan to billing schema (8.0.1)    |
| 6a2ab17  | refactor(v2): user_pricing/repository uses billing schema only (8.0.2) |
| ddfc2c8  | refactor(v2): retire get_supabase_scope v1 surface (8.0.3 B+)   |
| 8401f9e  | docs: phase-8 v2 purge closeout plan + acceptance test plan     |
| a9636b7  | refactor(v2): routes.py delete v1 fallbacks for /me /graph /zettel (8.0.4-abe) |
| a40ac07  | refactor(v2): blocklist uses pipelines.extraction_blocklist (8.0-H5) |
| 0c88799  | refactor(v2): kasten_stats emits OTel + structured logs (8.0-H6) |
| 5178d24  | fix(v2): sandbox member serializer reads canonical_zettels embed (8.0-H9) |
| 73c2e35  | test(v2): fix MintedUser.email + harden cross-tenant uuid-leak assertions |
| ac1ed35  | refactor(v2): kg_features hybrid cleanup + CI guard (8.0-H7)    |
| b387d78  | docs(v2): nexus service module headers point at pipelines.* (8.0-H8) |
| 48e64e1  | docs: sweep stale kg_users/kg_nodes comments in website/ (8.0-H10) |
| 663b63a  | docs(v2): phase-8 closeout + phase-9 plan (8.0.7-docs)          |
| 6d262ac  | ops(v2): patch verify_v2_e2e.py — drop public.kg_* refs (8.0.7-ops) |
| (F2-1)   | ops(v2): pre-T6 fix tests + write 31_drop_legacy_pricing.sql    |
| (F2-2)   | ops(v2): drop 6 public.pricing_* + 6 RPCs + delete supabase_kg (8.0.6) |
| (F2-3)   | docs(v2): annotate legacy ops scripts + finalize closeout (8.0.7-final) |

Plus prior commits from other agent: `8704391` (T8 H1 avatar v2 port), `481c0aa` (T4c+4d graph endpoints retired), `fd6e2fd` (T4c graph/query 410).

## Operator-approved scope decisions during execution

1. **B+ atomic migration for T3** (operator approved 2026-05-11): instead of "rename or raise RuntimeError" on `get_supabase_scope`, migrated all 5 production callers (nexus + bulk_import + service.py) atomically in same task and deleted the function. Industry-standard per Hyrum Wright SWE@Google ch.15 + LaunchDarkly tech-debt 2024 + Tideways. Used the no-push window as cheapest-possible-moment.

2. **T6 DROP applied 2026-05-11** (after pre-T6 audit + final regression sweep): drop of 6 retained `public.pricing_*` tables + 6 RPCs landed in `supabase/website/_v2/31_drop_legacy_pricing.sql`; `website/core/supabase_kg/` directory deleted in the same commit. Originally deferred per operator decision; cleared once the audit confirmed zero live website refs and 4 pre-DROP blockers (1 v2-ported test + 2 importorskip-gated live tests + 6 ops-script annotations) were resolved.

3. **T11 hybrid cleanup** (operator approved D-then-X scout, 2026-05-11): partial delete of `kg_features/` — only the 3 broken modules (retrieval, nl_query, entity_extractor); kept analytics + embeddings as pure-compute helpers; CI guard with explicit allow-list documents the 2 known importers.

## Phase summary

- Phase 0: pre-flight (9/9 sub-units)
- Phase 1: v2 RPC surface (1.A-D, 8 RPCs + alias table)
- Phase 2: rag_pipeline Bucket-B (7 sub-tasks; service.py atomic swap deferred to Phase 8.0.3)
- Phase 3: other Bucket-B (6 sub-tasks)
- Phase 4: read-path API handlers (4 routes)
- Phase 5: fresh start with 2 users + Naruto backfill
- Phase 6: DESTRUCTIVE drop of 30 legacy objects
- Phase 7: hardening + kasten member-sharing (7.2-deferred folded in)
- Phase 8: closeout (pricing migration + service.py B+ atomic swap + persist.py cleanup + endpoint deletes + nexus header sweep + kg_features hybrid cleanup + cross-tenant test harden)

## Deferred to future iterations

- **6 ops/scripts annotated as LEGACY (broken after 2026-05-11)** — `apply_kg_recommendations.py`, `audit_gold_expectations.py`, `check_corpus_drift.py`, `rag_eval_loop.py`, `score_rag_eval.py`, `lib/rag_eval_kasten.py`. They still import `website.core.supabase_kg` and now fail at runtime; revive by porting `get_supabase_client` calls to `get_v2_client()` from `website.core.supabase_v2.client` in a follow-up iteration.
- **2 live integration tests `importorskip`-gated** — `tests/integration/rag_pipeline/test_cross_source_retrieval.py` and `test_conflict_resolution.py`. Both exercise v1 KG (`kg_users`) semantics that no longer exist on v2; kept discoverable for future v2 port (skipped at collection on the missing import).
- **Phase 9** — pricing enforcement (multi-period schema + dual-write + shadow mode); see `phase-9-pricing-enforcement-plan.md`
- **Caddy `@maintenance` matcher staging-flip test** — operator manual, post-deploy

## Final acceptance test (queued)

`docs/db-v2/final-acceptance-test-plan.md` — Claude in Chrome on the live site, ingesting URLs from `docs/research/Chintan_Testing.md` as Naruto.
