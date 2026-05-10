# DB v2 Purge — Tech Debt Tracker

Open items added during the v2 purge that have explicit sunset triggers.

(Currently empty — all tracked items have been retired.)

## Closed

### ACL-001 — Candidate.node_id alias + candidate_to_legacy_dict (CLOSED 2026-05-10)

Closed in commit 8.5.R4-cleanup. Audit (Phase 8.5.R4) found zero consumers of
the legacy `node_id` property / `candidate_to_legacy_dict` projector / the
`default_rrf_score` knob in `chunk_from_v2_row`/`entity_from_v2_row`/
`doc_from_v2_row`. All three were deleted. The architectural fitness ratchet
test (`tests/architecture/test_acl_001_sunset.py`) was retired alongside.

The 3 files originally flagged by the AST-walk ratchet
(`retrieval/graph_score.py`, `orchestrator.py`, `rerank/cascade.py`) were
false positives — they access `RetrievalCandidate.node_id` from
`website/features/rag_pipeline/types.py`, a first-class Pydantic field used
by `Citation.node_id` + `AnswerTurn.retrieved_node_ids` + frontend citation
chips. Renaming would have broken externally-observable behaviour. The
ratchet's substring-match wasn't AST-resolving the receiver type.

Industry pattern: Microsoft GraphRAG / LlamaIndex / Pinecone / Weaviate —
typed candidate fields per kind, never collapsed into a single string id.
