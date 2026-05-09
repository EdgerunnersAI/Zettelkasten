# DB v2 Purge — Tech Debt Tracker

Open items added during the v2 purge that have explicit sunset triggers.

## ACL-001: legacy `node_id` back-compat alias on Candidate (added Phase 2.4.0, 2026-05-09)

**Description:** `website/features/rag_pipeline/retrieval/candidate_model.py` exposes
a `node_id` derived property on every Candidate variant + `candidate_to_legacy_dict()`
projects typed Candidates into the legacy v1 dict shape. Both exist so the v2 RPC
migration in Phase 2.4 doesn't have to refactor `_dedup_and_fuse` / RRF fusion /
bandit posterior updates simultaneously.

**Sunset trigger:** when every consumer in `website/features/rag_pipeline/` accesses
Candidate fields by their typed names (`canonical_chunk_id`, `kg_node_id`,
`canonical_zettel_id`) instead of the `node_id` alias, delete:
- the `node_id` property on each `*Candidate` subclass
- `candidate_to_legacy_dict()`
- the `chunk_from_v2_row(... default_rrf_score=...)` knob (RRF fusion will own its own
  per-source rank arithmetic natively)

**Sunset due:** Phase 7 hardening (post-soak, before legacy DROP migration).

**Industry rationale:** ACL pattern (Microsoft / AWS Prescriptive Guidance) is intentionally
temporary. Permanent ACLs are the dominant ACL failure mode (Thoughtworks, AWS).

**Owner:** v2 purge executor.

**References:**
- Microsoft Azure ACL pattern: https://learn.microsoft.com/en-us/azure/architecture/patterns/anti-corruption-layer
- AWS Prescriptive Guidance ACL: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/acl.html
- Thoughtworks Strangler Fig: https://www.thoughtworks.com/en-us/insights/articles/embracing-strangler-fig-pattern-legacy-modernization-part-one
