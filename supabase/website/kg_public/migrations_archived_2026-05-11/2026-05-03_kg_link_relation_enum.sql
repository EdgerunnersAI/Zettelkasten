-- iter-08 Phase 8: extend kg_links with relation_type enum for future
-- edge-weighted PageRank. Pure additive — does NOT touch existing
-- kg_links.relation TEXT column (which stores the shared-tag label).
-- Default 'shared_tag' to preserve current behaviour. iter-09 will populate
-- 'cites' / 'mentions' / 'co_occurs' from new ingestion logic.

create type kg_link_relation as enum ('shared_tag', 'cites', 'mentions', 'co_occurs');

alter table kg_links
    add column if not exists relation_type kg_link_relation default 'shared_tag' not null;

create index if not exists kg_links_relation_type_idx on kg_links(relation_type);

comment on column kg_links.relation_type is
  'Edge type for graph-aware retrieval. Default shared_tag for back-compat. '
  'Distinct from kg_links.relation which stores the shared-tag label.';
