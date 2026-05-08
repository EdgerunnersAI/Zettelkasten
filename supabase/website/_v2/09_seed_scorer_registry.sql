-- Seed the data-driven retrieval scorer registry.

INSERT INTO rag.retrieval_scorer_registry (scorer_name, impl_class, supported_inputs, description)
VALUES
    ('semantic', 'website.features.rag_pipeline.retrieval.hybrid.SemanticScorer', '{"embedding": true}'::jsonb, 'Dense semantic similarity'),
    ('fts', 'website.features.rag_pipeline.retrieval.hybrid.FullTextScorer', '{"query_text": true}'::jsonb, 'Postgres full-text search'),
    ('kg_graph', 'website.features.rag_pipeline.retrieval.graph_score.GraphScorer', '{"kg": true}'::jsonb, 'Knowledge graph neighborhood score'),
    ('chunk_share', 'website.features.rag_pipeline.retrieval.chunk_share.ChunkShareScorer', '{"chunks": true}'::jsonb, 'Shared chunk overlap'),
    ('kasten_frequency', 'website.features.rag_pipeline.retrieval.kasten_freq.KastenFrequencyScorer', '{"kasten": true}'::jsonb, 'Kasten frequency prior'),
    ('recency', 'website.features.rag_pipeline.retrieval.hybrid.RecencyScorer', '{"timestamp": true}'::jsonb, 'Freshness signal'),
    ('entity_anchor', 'website.features.rag_pipeline.retrieval.hybrid.EntityAnchorScorer', '{"entities": true}'::jsonb, 'Entity anchor expansion'),
    ('anchor_bandit', 'website.features.rag_pipeline.retrieval.hybrid.AnchorBanditScorer', '{"bandit": true}'::jsonb, 'Anchor-seed exploration prior')
ON CONFLICT (scorer_name) DO UPDATE
SET impl_class = EXCLUDED.impl_class,
    supported_inputs = EXCLUDED.supported_inputs,
    description = EXCLUDED.description;

INSERT INTO rag.retrieval_scorer_version (scorer_name, version_id, params, notes, created_by)
SELECT scorer_name, 'v1', '{}'::jsonb, 'Initial DB v2 seed', 'migration'
  FROM rag.retrieval_scorer_registry
ON CONFLICT (scorer_name, version_id) DO NOTHING;

INSERT INTO rag.retrieval_pipeline_config (environment, scorer_name, version_id, enabled, weight, updated_by)
VALUES
    ('dev', 'semantic', 'v1', true, 1.00, 'migration'),
    ('dev', 'fts', 'v1', true, 0.35, 'migration'),
    ('dev', 'kg_graph', 'v1', true, 0.25, 'migration'),
    ('dev', 'chunk_share', 'v1', true, 0.20, 'migration'),
    ('dev', 'kasten_frequency', 'v1', true, 0.20, 'migration'),
    ('dev', 'recency', 'v1', true, 0.10, 'migration'),
    ('dev', 'entity_anchor', 'v1', true, 0.15, 'migration'),
    ('dev', 'anchor_bandit', 'v1', false, 0.05, 'migration'),
    ('staging', 'semantic', 'v1', true, 1.00, 'migration'),
    ('staging', 'fts', 'v1', true, 0.35, 'migration'),
    ('staging', 'kg_graph', 'v1', true, 0.25, 'migration'),
    ('staging', 'chunk_share', 'v1', true, 0.20, 'migration'),
    ('staging', 'kasten_frequency', 'v1', true, 0.20, 'migration'),
    ('staging', 'recency', 'v1', true, 0.10, 'migration'),
    ('staging', 'entity_anchor', 'v1', true, 0.15, 'migration'),
    ('staging', 'anchor_bandit', 'v1', false, 0.05, 'migration'),
    ('prod', 'semantic', 'v1', true, 1.00, 'migration'),
    ('prod', 'fts', 'v1', true, 0.35, 'migration'),
    ('prod', 'kg_graph', 'v1', true, 0.25, 'migration'),
    ('prod', 'chunk_share', 'v1', true, 0.20, 'migration'),
    ('prod', 'kasten_frequency', 'v1', true, 0.20, 'migration'),
    ('prod', 'recency', 'v1', true, 0.10, 'migration'),
    ('prod', 'entity_anchor', 'v1', true, 0.15, 'migration'),
    ('prod', 'anchor_bandit', 'v1', false, 0.05, 'migration')
ON CONFLICT (environment, scorer_name) DO UPDATE
SET version_id = EXCLUDED.version_id,
    enabled = EXCLUDED.enabled,
    weight = EXCLUDED.weight,
    updated_at = now(),
    updated_by = EXCLUDED.updated_by;

