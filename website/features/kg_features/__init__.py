"""kg_features — partial cleanup landed 2026-05-11 (Phase 8.0 H7).

Active modules (kept; pure-compute, no DB):
- `analytics` - graph metrics (NetworkX); used by /api/graph
- `embeddings` - Gemini embedding helper; used by persist.py + retrieval

Retired modules (deleted; referenced dropped v1 tables / RPCs):
- `retrieval` - v1 hybrid_search + expand_subgraph against dropped kg_nodes/kg_links
- `nl_query` - v1 NL->SQL translator
- `entity_extractor` - v1 entity-canonicalization helper

Per understandlegacycode.com 2024 + LaunchDarkly 2024 + ConfigCat 2024-01-30 +
Hyrum Wright SWE@Google ch.15: hard-delete with git history as the archive.
Future v2 retrieval lives in `website/features/rag_pipeline/retrieval/`.
"""
