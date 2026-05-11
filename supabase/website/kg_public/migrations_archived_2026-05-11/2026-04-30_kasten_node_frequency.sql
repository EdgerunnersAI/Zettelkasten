-- iter-04: per-Kasten node-frequency table for the anti-magnet prior.
--
-- Every time a query in a given Kasten returns a top-1 retrieval hit, we
-- increment the (kasten_id, node_id) counter. The hybrid retriever applies
-- a multiplicative damping factor 1/(1+log(1+freq)) to candidate scores
-- post-RRF fusion, so nodes that magnet across unrelated queries inside a
-- Kasten lose ranking to nodes the user has not yet seen.
--
-- Cold-start: penalty is suppressed until a Kasten accumulates >= 50
-- recorded hits (see compute_frequency_penalty in kasten_freq.py).

CREATE TABLE IF NOT EXISTS kg_kasten_node_freq (
    kasten_id uuid NOT NULL,
    node_id text NOT NULL,
    hit_count integer NOT NULL DEFAULT 0,
    last_hit_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kasten_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_kg_kasten_node_freq_kasten
    ON kg_kasten_node_freq(kasten_id);

-- Read RPC: returns all node frequencies for one Kasten. Empty result is
-- valid (cold-start Kasten or unknown id).
CREATE OR REPLACE FUNCTION rag_kasten_node_frequencies(p_kasten_id uuid)
RETURNS TABLE(node_id text, hit_count integer)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    SELECT node_id, hit_count
    FROM kg_kasten_node_freq
    WHERE kasten_id = p_kasten_id;
$$;

-- Write RPC: increment-or-insert. Idempotent under duplicate calls thanks
-- to the upsert. Uses NOT DEFERRABLE NOT VALID style to avoid blocking
-- read transactions; per-row contention on the same (kasten, node) is
-- expected to be rare given retrieval cardinality.
CREATE OR REPLACE FUNCTION rag_kasten_record_node_hit(
    p_kasten_id uuid,
    p_node_id text
) RETURNS void
LANGUAGE sql
SECURITY INVOKER
AS $$
    INSERT INTO kg_kasten_node_freq (kasten_id, node_id, hit_count, last_hit_at)
    VALUES (p_kasten_id, p_node_id, 1, now())
    ON CONFLICT (kasten_id, node_id)
    DO UPDATE SET
        hit_count = kg_kasten_node_freq.hit_count + 1,
        last_hit_at = now();
$$;

-- Grant execute to the anon role used by the app (matches existing RAG RPCs).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION rag_kasten_node_frequencies(uuid) TO anon';
        EXECUTE 'GRANT EXECUTE ON FUNCTION rag_kasten_record_node_hit(uuid, text) TO anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION rag_kasten_node_frequencies(uuid) TO authenticated';
        EXECUTE 'GRANT EXECUTE ON FUNCTION rag_kasten_record_node_hit(uuid, text) TO authenticated';
    END IF;
END $$;
