# Full Modular Test Plans — Rollup Index

Per-module test specs derived from `docs/research/Full_Features_Test_Strategy1.md` and verified against actual code in `website/features/`. Consolidated from 5 parallel research subagents (2026-05-11).

**Total: 158 tasks across 14 majors and 1 sub-route (`/api/graph`).**

## Module index

| Module | File | Tasks | P1 / P2 / P3 |
|---|---|---|---|
| summarization_engine | [summarization_engine.md](summarization_engine.md) | 13 | 7 / 5 / 1 |
| rag_pipeline | [rag_pipeline.md](rag_pipeline.md) | 15 | 6 / 7 / 2 |
| api_key_switching | [api_key_switching.md](api_key_switching.md) | 11 | 4 / 5 / 2 |
| knowledge_graph (UI + /api/graph) | [knowledge_graph.md](knowledge_graph.md) | 15 | 6 / 6 / 3 |
| kg_features (analytics + embeddings) | [kg_features.md](kg_features.md) | 14 | 4 / 6 / 4 |
| user_pricing | [user_pricing.md](user_pricing.md) | 34 | 26 / 8 / 0 |
| user_auth | [user_auth.md](user_auth.md) | 8 | 5 / 2 / 1 |
| user_home | [user_home.md](user_home.md) | 8 | 4 / 3 / 1 |
| user_zettels | [user_zettels.md](user_zettels.md) | 7 | 3 / 3 / 1 |
| user_kastens | [user_kastens.md](user_kastens.md) | 6 | 3 / 2 / 1 |
| user_rag | [user_rag.md](user_rag.md) | 10 | 6 / 3 / 1 |
| header | [header.md](header.md) | 6 | 3 / 2 / 1 |
| browser_cache | [browser_cache.md](browser_cache.md) | 7 | 3 / 4 / 0 |
| web_monitor | [web_monitor.md](web_monitor.md) | 14 | 5 / 5 / 4 |
| **TOTAL** | | **168** | **85 / 61 / 22** |

> Counts updated after final spec materialization. Some module counts grew slightly during write-up (added explicit Phase-9 prep + observability hygiene items).

## Wave execution order (operator-approved)

| Wave | Modules | ETA |
|---|---|---|
| A — Payments + Auth | user_pricing → user_auth → browser_cache | weeks 1–3 |
| B — RAG + Kastens + Zettels | rag_pipeline → user_kastens → user_zettels → user_rag | weeks 3–6 |
| C — Summ + KeyPool + KG | summarization_engine → api_key_switching → knowledge_graph → kg_features | weeks 6–9 |
| D — UI shell + Monitor | user_home → header → web_monitor | weeks 9–11 |
| P2 sweep | all modules | weeks 11–14 |
| P3 polish | all modules | weeks 14–16 |
| Chaos + synthetic monitoring | rag_pipeline, user_pricing, summarization_engine, web_monitor | weeks 16–18 |

## Locked operator policies

- **Live-test policy**: mocked default in CI; `--live` against staging; production read-only allowed for `GET` smoke endpoints only (see each spec for explicit exceptions).
- **Phase-9 prep**: pending `xfail` tests added now for fail-closed entitlement enforcement (`UP-29`) and multi-period reset (`UP-29` companion).
- **Pre-DROP CI gate**: build fails if any code references retired `public.pricing_*` tables (`UP-26`).
- **graph.json schema gate**: CI schema-validates committed asset (`KG-03`).
- **Reuse `tests/integration/v2/conftest.py`** `mint_user` + UUID-leak pattern for all BOLA tasks.

## Skill-driven execution (per operator note 2026-05-11)

1. `superpowers:writing-plans` produces the implementation plan that drives execution.
2. `superpowers:subagent-driven-development` dispatches one Implementation subagent per task, with TDD enforced.
3. `superpowers:test-driven-development` per task: write red test → minimal pass → refactor → verify.
4. `superpowers:verification-before-completion` before each task is marked done.

## MCP / Chrome assignments

| MCP / tool | Used for |
|---|---|
| `mcp__Claude_in_Chrome` | P1 SSE happy-path + reconnect, auth callback e2e, KG render, share/member UX, color-rule scans — major UI-visible flows only |
| `mcp__Claude_Preview` | Visual regression baselines for `user_home`, `user_kastens`, KG modal |
| `mcp__github` | CI workflow assertions, pre-DROP grep gate, golden-md5 check, schema gate |
| `mcp__scheduled-tasks` | 5-min dashboard refresh during active waves; alert canary heartbeat (WM-11) |
| `mcp__digitalocean_apps` | Synthetic prod read-only canaries from outside the droplet |
| `mcp__plugin_mem-vault_mem-vault` | save_observation for every locked decision; smart_outline before reading any unfamiliar file |
| `respx` / `responses` (pytest) | All HTTP-mocked outbound (Razorpay, Slack, Gemini, Supabase) in CI |

## Dashboard

5-minute scheduled task `zettel-test-plan-dashboard` fires every 5 min during active waves (cron `*/5 * * * *`). Reads state from `.claude/test-plan-state.json`. Disables when implementation is complete or paused.
