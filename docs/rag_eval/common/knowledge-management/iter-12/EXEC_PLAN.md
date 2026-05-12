# iter-12 EXEC plan

**Branch:** `iter12-exec` off `master@f98475b` (post-drift-fix)
**Date:** 2026-05-12
**Goal:** Take a clean Naruto v2 workspace, re-ingest the iter-10/iter-11 7-zettel set against the same canonical URLs, rebuild the "Knowledge Management & Personal Productivity" Kasten, and prove iter-12 readiness by running the existing query set against the rebuilt Kasten.

This branch consolidates the post-PR-#11 / post-PR-#13 follow-up work into one reviewable PR.

## Scope

### Fix block (P-prefix = problems surfaced by Phase-8 live probe 2026-05-12)
- **P0 — JWT verify failure**: `mint_eval_jwt.py` produces a valid ES256 Supabase JWT, but `/api/me` returns 401 "Invalid token". Diagnose the JWKS-verify path in `website/api/auth.py` and fix.
- **P1 — Read-only file-KG mount**: `/app/website/features/knowledge_graph/content/graph.json` is on a read-only Docker mount; `_persist_file_node` catches OSError and silently sets `file_saved=False`. Either (a) provide a writable volume mount, or (b) deprecate the file-store path now that v2 is the source of truth — operator decides.
- **P3 — YouTube ingestion broken in prod**: yt-dlp datacenter-IP bot block; Piped pool TLS expired / connection failures. All 6 transcript tiers fail → metadata-only fallback (composite cap 75, confidence 0.20). Affects 4 of the 7 zettels.

### Refactor block
- **B1(a)** — delete one-shot v1 migration scripts (audit + remove `LEGACY (broken after 2026-05-11)` annotated scripts under `ops/scripts/`).
- **B1(b)** — port general-purpose v1 admin scripts to v2 schema. v2 = `core.*` + `content.*` + `rag.*` + `kg.*`. No `public.kg_*` references anywhere.

### Restoration block
- **V0** — list Naruto's full v2 zettel inventory; pattern-match against the 7 iter-11 node-ids; freeze the canonical URL set.
- **V1** — audit `content.canonical_zettels` + `content.workspace_zettels` + `content.canonical_chunks` for Naruto's workspace.
- **V2** — clean-slate wipe: DELETE every Naruto-owned row in `content.workspace_zettels`, the matching `content.canonical_chunks`, the matching `content.canonical_zettels` (after FK check), every `rag.kasten_zettels` linking Naruto's Kastens, and every `rag.kastens` row owned by Naruto. From UI side: confirm `/home` is empty.
- **I1** — re-ingest 7 URLs via `/api/summarize` with a working Naruto bearer (P0 fixed) and working YT pipeline (P3 fixed).
- **I2** — verify per zettel: row in `content.canonical_zettels`, ≥1 row in `content.canonical_chunks`, row in `content.workspace_zettels` for Naruto's workspace.
- **K1** — create `rag.kastens` row "Knowledge Management & Personal Productivity" + 7 `rag.kasten_zettels` linking rows.
- **R1** — readiness verdict: re-run `iter-11/queries.json` query set against the rebuilt Kasten as a sanity baseline before authorizing the iter-12 eval run.

## Member IDs (frozen from iter-11/queries.json)

| iter-11 node_id | source | content target |
|---|---|---|
| nl-the-pragmatic-engineer-t | newsletter | The Pragmatic Engineer |
| yt-effective-public-speakin | youtube | Patrick Winston — How to Speak (MIT) |
| yt-steve-jobs-2005-stanford | youtube | Steve Jobs 2005 Stanford commencement |
| yt-matt-walker-sleep-depriv | youtube | Matt Walker on sleep deprivation |
| yt-programming-workflow-is | youtube | Programming workflow is debugging cycle |
| web-transformative-tools-for | web | Andy Matuschak — "Transformative tools for thought" — `https://andymatuschak.org/` |
| gh-zk-org-zk | github | zk-org/zk CLI — `https://github.com/zk-org/zk` |

5 of 7 canonical URLs need to be re-derived (V0 step) — they live only in the DB, never quoted in eval docs.

## Memory rules in effect this PR
- **Merge:** Rebase & Merge only. No squash. (feedback_merge_strategy.md 2026-05-12)
- **Approvals:** explicit per-step approval for deploy / pricing / protected knob changes; phase transitions are autonomous.
- **Commits:** no `Co-Authored-By` trailers; short precise messages.
- **Scope:** anything not in this plan is a NEW decision and needs explicit approval before code.

## Order of operations

```
P0 fix → re-probe → unblocks I1
P1 fix → re-probe (file-store) OR deprecate-decision
P3 fix → unblocks YT ingestion for 4 of 7 zettels
B1(a)/B1(b) refactor → unblocks V0/V2/I1/I2/K1 scripts
V0 → V1 → V2 (clean slate) → I1 → I2 → K1 → R1
```
