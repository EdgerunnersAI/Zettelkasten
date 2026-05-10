# DB v2 Purge — Phase 8 Closeout Complete (2026-05-11)

## Final state at end of Phase 8

- 2 users (Naruto + Zoro), both UUID-authed.
- 3 zettels backfilled to Naruto's workspace at Phase 5.
- All v2 schemas (`core, content, kg, rag, pipelines, billing`) live and populated.
- Legacy `public.*` tables and 7 v1 RPCs dropped (Phases 6, 7.2; 6 retained `public.pricing_*` tables + 6 RPCs deferred to post-T15 verification per operator decision 2026-05-11).
- `website/core/supabase_kg/` directory **NOT yet deleted** — deferred with the T6 DROP, gated on T15 final regression verification passing first.
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

Plus prior commits from other agent: `8704391` (T8 H1 avatar v2 port), `481c0aa` (T4c+4d graph endpoints retired), `fd6e2fd` (T4c graph/query 410).

## Operator-approved scope decisions during execution

1. **B+ atomic migration for T3** (operator approved 2026-05-11): instead of "rename or raise RuntimeError" on `get_supabase_scope`, migrated all 5 production callers (nexus + bulk_import + service.py) atomically in same task and deleted the function. Industry-standard per Hyrum Wright SWE@Google ch.15 + LaunchDarkly tech-debt 2024 + Tideways. Used the no-push window as cheapest-possible-moment.

2. **T6 DROP deferred** (operator decision 2026-05-11): defer drop of 6 retained `public.pricing_*` tables + 6 RPCs until T15 final regression verification passes. Standard expand→migrate→contract discipline (Fowler 2014, Hodgson 2023). Same deferral applies to `website/core/supabase_kg/` directory delete.

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

- **T6 DROP** — drop 6 retained `public.pricing_*` tables + 6 RPCs (gated on T15 verification)
- **T7-dir-delete** — delete `website/core/supabase_kg/` directory (paired with T6)
- **Phase 9** — pricing enforcement (multi-period schema + dual-write + shadow mode); see `phase-9-pricing-enforcement-plan.md`
- **Caddy `@maintenance` matcher staging-flip test** — operator manual, post-deploy

## Final acceptance test (queued)

`docs/db-v2/final-acceptance-test-plan.md` — Claude in Chrome on the live site, ingesting URLs from `docs/research/Chintan_Testing.md` as Naruto.
