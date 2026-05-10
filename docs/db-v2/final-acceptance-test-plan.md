# DB v2 Purge — Final Acceptance Test Plan (queued by operator 2026-05-10)

After Phase 8 closes AND production deploy is verified live, execute the following acceptance test before declaring the v2 purge complete.

## Scope

End-to-end exerciser via **Claude in Chrome** against the LIVE deployed site.

## Steps

1. **Login as Naruto** on https://zettelkasten.in/ using `naruto@zettelkasten.local` / `Naruto2026!`.
2. **Submit 2-3 links** from `docs/research/Chintan_Testing.md` — pick a YouTube URL + a Reddit URL for source-type coverage. Suggested defaults:
   - YouTube: `https://www.youtube.com/watch?v=hhjhU5MXZOo` (35m, "The Strangest Drug Ever Studied")
   - YouTube short: `https://www.youtube.com/watch?v=O7FIiYsVy3U` (3m, "brother may I have some oats")
   - Reddit: `https://www.reddit.com/r/explainlikeimfive/comments/1d86ux/eli5_marxism/`
3. **Resolve any errors** that surface during ingestion (UI errors, 4xx/5xx, stuck-pending state).
4. **Verify per-step pipeline execution** for each Zettel:
   - URL extraction (right metadata captured)
   - Gemini summarization (summary present + ai_summary_engine_version stamped)
   - Source-type detection (correct: youtube / reddit)
   - Embedding generation (halfvec(768))
   - KG entity extraction (kg_nodes populated)
   - Chunk membership (workspace_chunk_membership rows)
   - Workspace overlay (ai_summary, user_tags)
5. **Supabase backend per-module audit:**
   - `content.canonical_zettels` — new row inserted; normalized_url + content_hash dedup unique key holds
   - `content.workspace_zettels` — workspace overlay created for Naruto's workspace
   - `content.canonical_chunks` — chunks created with halfvec embedding
   - `content.workspace_chunk_membership` — workspace ↔ chunk join rows
   - `kg.kg_nodes` — entities extracted (person/place/concept entities)
   - `kg.chunk_node_mentions` — chunk↔entity mentions
   - `kg.kg_node_aliases` — extractor aliases populated (if applicable)
   - `kg.kg_edges` — entity relationships (if extracted)
   - `pipelines.pipeline_runs` — pipeline run row with `kind='nexus_ingest'` or similar status='completed'
   - `pipelines.pipeline_run_items` — per-step item status
   - `rag.retrieval_signal_weights` — weights table not perturbed (recompute is async cron, not synchronous on ingest)
   - `billing.pricing_subscriptions` / `billing.pricing_plan_entitlements` — UNTOUCHED (no auto-subscribe)
6. **Pass criteria:** every expected module fired correctly; no broken modules; all expected DB rows landed; no orphan / partial rows.
7. **Fail criteria:** any module that should have fired didn't; any orphan row (e.g. canonical_chunks without workspace_chunk_membership); any 5xx during ingest; any silent quality regression vs documented v2 expectations.

## Sequencing

Cannot run until:
- Phase 8.1 closes (persist.py refactor + supabase_kg/ deletion)
- Phase 8.2 closes (verify_v2_e2e.py "PURE v2" verdict)
- Phase 8.3 closes (CLAUDE.md updated)
- Phase 8.4 closes (final notes)
- Production deploy verified (`curl https://zettelkasten.in/api/health` → 200)
- DB_SCHEMA_VERSION=v2 confirmed in production env (currently the default per env files)
