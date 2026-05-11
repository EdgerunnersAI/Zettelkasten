-- iter-12 R6: per-Kasten negative-resolution cache (DNS-style).
-- Tracks entities that consistently fail to resolve to any anchor node,
-- blocking them from triggering RPC calls until a hit evicts the row.
CREATE TABLE IF NOT EXISTS kg_extraction_blocklist (
  sandbox_id uuid NOT NULL,
  entity_text_norm text NOT NULL,                  -- lower(trim(text))
  consecutive_misses int NOT NULL DEFAULT 0,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  blocked_until timestamptz,                        -- NULL = active block
  PRIMARY KEY (sandbox_id, entity_text_norm)
);
CREATE INDEX IF NOT EXISTS kg_extraction_blocklist_active_idx
  ON kg_extraction_blocklist (sandbox_id, blocked_until);
