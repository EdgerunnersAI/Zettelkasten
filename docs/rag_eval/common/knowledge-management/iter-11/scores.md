# 06 Scorecard

**Composite:** 60.26  (weights={'chunking': 0.1, 'retrieval': 0.25, 'reranking': 0.2, 'synthesis': 0.45}, hash=422403f9d47a)

## Components
- chunking:    40.43
- retrieval:   64.26
- reranking:   52.45
- synthesis:   65.92

## RAGAS sidecar (0..100)
- faithfulness:      87.86
- answer_relevancy:  85.71

## Latency
- p50: 34149 ms
- p95: 42846 ms

## Coverage
- total queries:        14
- refusal-expected:     1
- eval_divergence:      False

## Holistic monitoring (iter-04)
- gold@1 (unconditional):  0.6154
- gold@1 within budget:    0.3077
- gold@1 not applicable:   1 (refusal-expected)
- gold@3: 0.6923    gold@8: 0.7692
- within_budget_rate: 0.5
- refused_count: 5

### critic_verdict distribution
- supported: 5
- partial: 4
- unsupported_no_retry: 3
- unsupported_with_gold_skip: 1
- retry_budget_exceeded: 1

### query_class distribution
- lookup: 6
- multi_hop: 4
- thematic: 4

### magnet-spotter (>=25% top-1 share)
- ⚠️ gh-zk-org-zk: top-1 in 3/14 queries

### burst pressure
- by_status: {'502': 6, '503': 6}
- 503 rate (target ≥0.08): 0.5
- 502 rate (target 0.0):  0.5

## Per-query (RAGAS overall is dataset-level)

| qid | retrieval | rerank | gold_in_retrieved | cites |
|---|---:|---:|:-:|---:|
| q1 | 100.0 | 66.7 | ✓ | 4 |
| q2 | 100.0 | 100.0 | ✓ | 1 |
| q3 | 100.0 | 100.0 | ✓ | 1 |
| q4 | 80.0 | 80.7 | ✓ | 1 |
| q5 | 68.0 | 33.6 | ✓ | 3 |
| q6 | 86.7 | 68.5 | ✓ | 3 |
| q7 | 0.0 | 0.0 | — | 0 |
| q8 | 85.0 | 48.2 | ✓ | 4 |
| q9 | 0.0 | 0.0 | — | 0 |
| q10 | 0.0 | 0.0 | — | 0 |
| q11 | 100.0 | 100.0 | ✓ | 1 |
| q12 | 80.0 | 41.7 | ✓ | 3 |
| q13 | 0.0 | 20.0 | — | 0 |
| q14 | 100.0 | 75.0 | ✓ | 2 |
