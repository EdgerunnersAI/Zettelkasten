# Test Plan — `kg_features`

Strategy ref: `docs/research/Full_Features_Test_Strategy1.md` §`kg_features`.
Module path: `website/features/kg_features/` (analytics + embeddings; retrieval/nl_query/entity_extractor deleted Phase 8.0-H7).
Risk tier: **High** (consumed by `/api/graph` + persistence).

## Minor modules / sub-flows

| Sub-module | File / surface |
|---|---|
| Graph metrics | `analytics.py` — `compute_graph_metrics`, `_build_networkx_graph`, `_compute_with_fallback` (PageRank, Louvain seed=42, betweenness k=min(100,len), closeness) |
| Embedding generation | `embeddings.py` — `_normalize_embedding`, `generate_embedding`, `generate_embeddings_batch`, `should_create_semantic_link` (>0.75), `find_similar_nodes` (`match_kg_nodes` RPC) |

## Tasks

| ID | P | Task | Rationale |
|---|---|---|---|
| KF-01 | P1 | Deterministic fixtures: seed=42 Louvain stable across runs (golden snapshot) | Reproducibility |
| KF-02 | P1 | Embedding failure-mode contract: empty text → `[]`; pool None → `[]`; provider 429/net → `[]` (not exception); batch preserves length on partial failure | Robustness |
| KF-03 | P1 | `find_similar_nodes` RPC authz: scope by `user_id`; cross-tenant denial w/ UUID-leak assertion | API1:2023 |
| KF-04 | P1 | L2 normalization: `np.linalg.norm(result) ≈ 1.0` ±1e-6; zero-vector input safe (no div-by-zero) | Correctness |
| KF-05 | P2 | Edge cases: empty graph → zero metrics; 1-node → `{id:1.0}`; disconnected → correct `num_components` | Boundary correctness |
| KF-06 | P2 | Property-based (Hypothesis + `hypothesis-networkx`): sum(pagerank)≈1.0, betweenness ∈ [0,1], num_communities ≤ num_nodes | Invariants |
| KF-07 | P2 | Fallback path: monkeypatch `nx.pagerank` raise → zeroed dict + logger.warning; other metrics still computed | Resilience |
| KF-08 | P2 | Perf baseline: 1k/5k/10k nodes — `compute_graph_metrics` < budget (e.g. 2s @ 5k); regression-gated | Scalability |
| KF-09 | P2 | Dim mismatch handling: 512-dim mocked → warn but return L2'd vector | Provider drift |
| KF-10 | P2 | Threshold boundary: `should_create_semantic_link(0.75)` → False (strict >); 0.7500001 → True | Pin to prevent silent flip |
| KF-11 | P3 | Determinism tolerance: same input twice via mocked pool — cosine ≥ 0.9999 | Acknowledge provider non-determinism |
| KF-12 | P3 | Batch concurrency: 100 parallel `generate_embedding` under pool contention — no key starvation | Concurrency |
| KF-13 | P3 | Quota-exhaustion chaos: all keys cooling → returns `[]`, no exception propagated | Failure isolation |
| KF-14 | P3 | Malformed-graph fuzzing (duplicate edges, self-loops, NaN groups) | Edge |

## Execution order
KF-01 → KF-04 → KF-02 → KF-03 → KF-10 → KF-05 → KF-07 → KF-06 → KF-09 → KF-08 → KF-11 → KF-12 → KF-13 → KF-14

## Industry standards (≤5y)
- Hypothesis: A new approach (JOSS 2019, canonical)
- hypothesis-networkx
- OpenAI Embedding Determinism forum
- Zilliz Embedding Normalization Best Practices
- OWASP API1:2023 BOLA

## Live-test policy
Mocked Gemini + Supabase in CI. `--live` against staging only.
