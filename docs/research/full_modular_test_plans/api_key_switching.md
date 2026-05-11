# Test Plan — `api_key_switching`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`api_key_switching`.
Module path: `website/features/api_key_switching/`.
Risk tier: **High** (shared dep for summarization + RAG + KG).

## Minor modules / sub-flows

| Sub-module | File |
|---|---|
| Pool bootstrap | `__init__.py` exports, env/api_env discovery |
| Cooldowns + selection + retry | `key_pool.py` (or equivalent) |
| Content-aware routing | `routing.py` |

## Tasks

| ID | P | Task | Rationale |
|---|---|---|---|
| KP-01 | P1 | Bootstrap: missing `api_env`, malformed line, empty file, `GEMINI_API_KEY` legacy fallback, `/etc/secrets/api_env` precedence | Bootstrap failure takes down 3 products |
| KP-02 | P1 | Key-first traversal order (key1→key2→…→downgrade) | Locked decision in CLAUDE.md |
| KP-03 | P1 | Concurrent pool contention: N workers requesting key under cooldown — race-free selection | Pool integrity |
| KP-04 | P1 | 429 → next key (same model) before model downgrade — verify cascade not flipped | Cost + quality invariant |
| KP-05 | P2 | Cooldown TTL boundaries + expiry under clock skew | Resilience |
| KP-06 | P2 | Content-aware routing thresholds (`routing.py`): short → flash-lite first | Quota preservation |
| KP-07 | P2 | All-keys-exhausted → raw-fallback `is_raw_fallback=True` (not 500) | Graceful degradation |
| KP-08 | P2 | Secret never logged / never in tracebacks / never in metrics | Secret hygiene |
| KP-09 | P2 | `--preload` COW friendliness: pool immutable post-fork (Phase 1A invariant) | Protected knob |
| KP-10 | P3 | Hot-reload of `api_env` without restart (if supported) | Ops convenience |
| KP-11 | P3 | Synthetic quota-exhaustion canary metric | Observability polish |

## Execution order
KP-01 → KP-02 → KP-03 → KP-04 → KP-07 → KP-08 → KP-09 → KP-05 → KP-06 → KP-10 → KP-11

## Industry standards (≤5y)
- Portkey LLM rate-limit (2024)
- agentgateway multi-key cascade (2025)
- agent-chaos LLM rate-limit injectors (2025)
- AWS Reliability Pillar — bulkhead pattern
- Azure Transient Fault Handling

## Live-test policy
All mocked in CI. `--live` runs against staging Gemini keys only; never burn production keys.
