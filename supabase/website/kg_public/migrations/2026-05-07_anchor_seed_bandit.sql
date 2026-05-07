-- iter-12 Task 31 — prepend kg_kasten_metrics CREATE for alphabetical-order independence.
create table if not exists kg_kasten_metrics (
  id bigserial primary key,
  sandbox_id uuid not null,
  top1_node_id text not null,
  ts timestamptz not null default now()
);

create index if not exists kg_kasten_metrics_sandbox_ts_idx
  on kg_kasten_metrics(sandbox_id, ts desc);

-- iter-12 Task 31 R4: Thompson-sampling bandit for per-Kasten anchor-seed floor.
-- Additive extension to kg_kasten_metrics (Task 7 created the table).
-- Pool-size stratification: S<30, M<80, L≥80 (R4-followup mod 2).
-- Per-Kasten kill switch column (R4-followup mod 4).
-- Do NOT apply to live Supabase without operator approval.

-- ---------------------------------------------------------------------------
-- 1. New columns on kg_kasten_metrics
-- ---------------------------------------------------------------------------
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_arm numeric;

ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_pool_bucket text
  CHECK (seed_pool_bucket IS NULL OR seed_pool_bucket IN ('S','M','L'));

-- Informative warm-start prior (R4-followup mod 3): initialized from
-- historical static-0.30 win-rate via bandit_warm_start.py before first pull.
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_alpha numeric DEFAULT 1.0;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_beta  numeric DEFAULT 1.0;

-- Decay tracking: γ=0.98/day (R4-followup mod 1, NOT 0.9 weekly).
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_last_decay_at timestamptz;

-- Total pulls counter for cold-start gate (N_min=20).
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS seed_total_pulls int DEFAULT 0;

-- Per-Kasten kill switch — operator or auto-rollback sets bandit_disabled_at.
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS bandit_disabled_at     timestamptz NULL;
ALTER TABLE kg_kasten_metrics ADD COLUMN IF NOT EXISTS bandit_disabled_reason text        NULL;

-- ---------------------------------------------------------------------------
-- 2. Unique index: one posterior row per (user, kasten, arm, pool_bucket).
--    Partial (seed_arm IS NOT NULL) so the T7 top-1 metric rows are unaffected.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS kg_kasten_metrics_bandit_key
  ON kg_kasten_metrics(sandbox_id, top1_node_id, seed_arm, seed_pool_bucket)
  WHERE seed_arm IS NOT NULL;

-- NOTE: kg_kasten_metrics uses sandbox_id (= kasten UUID) + top1_node_id as
-- the natural composite key from T7.  The bandit keying is
-- (p_user_id, kasten_id, seed_arm, seed_pool_bucket).  We map:
--   p_user_id  → the owning user's UUID stored in kg_users.id (passed as arg)
--   kasten_id  → sandbox_id
-- A separate lightweight bandit table scoped just to bandit rows avoids
-- coupling with the T7 top-1 frequency rows.  Create it now:

CREATE TABLE IF NOT EXISTS kg_bandit_posteriors (
  id              bigserial PRIMARY KEY,
  p_user_id       uuid        NOT NULL,
  kasten_id       uuid        NOT NULL,
  seed_arm        numeric     NOT NULL,
  seed_pool_bucket text       NOT NULL CHECK (seed_pool_bucket IN ('S','M','L')),
  seed_alpha      numeric     NOT NULL DEFAULT 1.0,
  seed_beta       numeric     NOT NULL DEFAULT 1.0,
  seed_total_pulls int        NOT NULL DEFAULT 0,
  seed_last_decay_at timestamptz,
  bandit_disabled_at     timestamptz NULL,
  bandit_disabled_reason text        NULL,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS kg_bandit_posteriors_key
  ON kg_bandit_posteriors(p_user_id, kasten_id, seed_arm, seed_pool_bucket);

CREATE INDEX IF NOT EXISTS kg_bandit_posteriors_kasten_idx
  ON kg_bandit_posteriors(p_user_id, kasten_id, seed_pool_bucket);

-- ---------------------------------------------------------------------------
-- 3. SQL function: rag_bandit_read_arms
--    Reads all arm rows for a (user, kasten, pool_bucket).
--    Returns bandit_disabled_at alongside so Python can check kill switch.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rag_bandit_read_arms(
  p_user_id  uuid,
  p_kasten_id uuid,
  p_bucket   text
)
RETURNS TABLE (
  seed_arm          numeric,
  seed_alpha        numeric,
  seed_beta         numeric,
  seed_total_pulls  int,
  bandit_disabled_at timestamptz
)
LANGUAGE sql
STABLE
AS $$
  SELECT
    bp.seed_arm,
    bp.seed_alpha,
    bp.seed_beta,
    bp.seed_total_pulls,
    bp.bandit_disabled_at
  FROM kg_bandit_posteriors bp
  WHERE bp.p_user_id       = rag_bandit_read_arms.p_user_id
    AND bp.kasten_id        = rag_bandit_read_arms.p_kasten_id
    AND bp.seed_pool_bucket = rag_bandit_read_arms.p_bucket
  ORDER BY bp.seed_arm;
$$;

-- ---------------------------------------------------------------------------
-- 4. SQL function: rag_bandit_record_outcome
--    Atomic INSERT...ON CONFLICT...DO UPDATE (no SELECT-then-UPDATE race).
--    p_reward = 1 (seed survived rerank top-K) or 0 (dropped).
--    Increments α on success, β on failure; increments total_pulls always.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rag_bandit_record_outcome(
  p_user_id   uuid,
  p_kasten_id uuid,
  p_arm       numeric,
  p_bucket    text,
  p_reward    int  -- 1 = survived, 0 = dropped
)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO kg_bandit_posteriors
    (p_user_id, kasten_id, seed_arm, seed_pool_bucket,
     seed_alpha, seed_beta, seed_total_pulls, updated_at)
  VALUES (
    p_user_id, p_kasten_id, p_arm, p_bucket,
    1.0 + p_reward,         -- α: warm-start 1.0 + reward on first pull
    1.0 + (1 - p_reward),   -- β: warm-start 1.0 + non-reward on first pull
    1,
    now()
  )
  ON CONFLICT (p_user_id, kasten_id, seed_arm, seed_pool_bucket)
  DO UPDATE SET
    seed_alpha       = kg_bandit_posteriors.seed_alpha + EXCLUDED.seed_alpha - 1.0,
    seed_beta        = kg_bandit_posteriors.seed_beta  + EXCLUDED.seed_beta  - 1.0,
    seed_total_pulls = kg_bandit_posteriors.seed_total_pulls + 1,
    updated_at       = now();
$$;
