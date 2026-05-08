-- ============================================================================
-- Nexus provider-ingest schema
-- Creates provider account, OAuth state, ingest run, and ingested artifact
-- tables with indexes, updated_at management, compatibility migrations,
-- and RLS policies.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- Provider accounts ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.nexus_provider_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.kg_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('youtube', 'github', 'reddit', 'twitter')),
    account_id TEXT,
    account_username TEXT,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_type TEXT NOT NULL DEFAULT 'Bearer',
    scopes TEXT[] NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at TIMESTAMPTZ,
    last_imported_at TIMESTAMPTZ,
    UNIQUE (user_id, provider)
);

COMMENT ON TABLE public.nexus_provider_accounts IS
    'Encrypted OAuth credentials for Nexus source providers; user_id maps to kg_users.id.';


-- OAuth states ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.nexus_oauth_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_digest TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('youtube', 'github', 'reddit', 'twitter')),
    auth_user_sub TEXT NOT NULL,
    redirect_path TEXT DEFAULT '/home/nexus',
    code_verifier TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, state_digest)
);

COMMENT ON TABLE public.nexus_oauth_states IS
    'Short-lived OAuth state records for CSRF protection and PKCE handoff.';


-- Ingest runs ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.nexus_ingest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.kg_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('youtube', 'github', 'reddit', 'twitter')),
    provider_account_id UUID REFERENCES public.nexus_provider_accounts(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'completed', 'partial_success', 'failed', 'cancelled')
    ),
    total_artifacts INTEGER NOT NULL DEFAULT 0 CHECK (total_artifacts >= 0),
    imported_count INTEGER NOT NULL DEFAULT 0 CHECK (imported_count >= 0),
    skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.nexus_ingest_runs IS
    'Operational records for each Nexus provider import run (supports partial_success).';


-- Ingested artifacts ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.nexus_ingested_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.kg_users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('youtube', 'github', 'reddit', 'twitter')),
    provider_account_id UUID REFERENCES public.nexus_provider_accounts(id) ON DELETE SET NULL,
    run_id UUID REFERENCES public.nexus_ingest_runs(id) ON DELETE SET NULL,
    -- Backward compatibility alias used by older workers; keep nullable.
    ingest_run_id UUID REFERENCES public.nexus_ingest_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'imported', 'skipped', 'failed')
    ),
    error_message TEXT,
    node_id TEXT,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source_type TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider, external_id)
);

COMMENT ON TABLE public.nexus_ingested_artifacts IS
    'Per-artifact ingest outcomes (status/error/node_id) captured during Nexus runs.';
COMMENT ON COLUMN public.nexus_ingested_artifacts.run_id IS
    'Canonical run reference used by runtime writes.';
COMMENT ON COLUMN public.nexus_ingested_artifacts.ingest_run_id IS
    'Legacy alias retained for compatibility; run_id is canonical.';


-- Compatibility migrations ---------------------------------------------------

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD COLUMN IF NOT EXISTS run_id UUID;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD COLUMN IF NOT EXISTS ingest_run_id UUID;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD COLUMN IF NOT EXISTS node_id TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'nexus_ingested_artifacts'
          AND column_name = 'ingest_run_id'
    ) THEN
        UPDATE public.nexus_ingested_artifacts
        SET run_id = ingest_run_id
        WHERE run_id IS NULL
          AND ingest_run_id IS NOT NULL;
    END IF;
END;
$$;

UPDATE public.nexus_ingested_artifacts
SET status = 'pending'
WHERE status IS NULL;

ALTER TABLE IF EXISTS public.nexus_provider_accounts
    DROP CONSTRAINT IF EXISTS nexus_provider_accounts_user_id_fkey;
ALTER TABLE IF EXISTS public.nexus_provider_accounts
    ADD CONSTRAINT nexus_provider_accounts_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.kg_users(id) ON DELETE CASCADE NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingest_runs
    DROP CONSTRAINT IF EXISTS nexus_ingest_runs_user_id_fkey;
ALTER TABLE IF EXISTS public.nexus_ingest_runs
    ADD CONSTRAINT nexus_ingest_runs_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.kg_users(id) ON DELETE CASCADE NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    DROP CONSTRAINT IF EXISTS nexus_ingested_artifacts_user_id_fkey;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD CONSTRAINT nexus_ingested_artifacts_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES public.kg_users(id) ON DELETE CASCADE NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingest_runs
    DROP CONSTRAINT IF EXISTS nexus_ingest_runs_status_check;
ALTER TABLE IF EXISTS public.nexus_ingest_runs
    ADD CONSTRAINT nexus_ingest_runs_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'partial_success', 'failed', 'cancelled')) NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    DROP CONSTRAINT IF EXISTS nexus_ingested_artifacts_status_check;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD CONSTRAINT nexus_ingested_artifacts_status_check
    CHECK (status IN ('pending', 'imported', 'skipped', 'failed')) NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    DROP CONSTRAINT IF EXISTS nexus_ingested_artifacts_run_id_fkey;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD CONSTRAINT nexus_ingested_artifacts_run_id_fkey
    FOREIGN KEY (run_id) REFERENCES public.nexus_ingest_runs(id) ON DELETE SET NULL NOT VALID;

ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    DROP CONSTRAINT IF EXISTS nexus_ingested_artifacts_ingest_run_id_fkey;
ALTER TABLE IF EXISTS public.nexus_ingested_artifacts
    ADD CONSTRAINT nexus_ingested_artifacts_ingest_run_id_fkey
    FOREIGN KEY (ingest_run_id) REFERENCES public.nexus_ingest_runs(id) ON DELETE SET NULL NOT VALID;


-- Indexes --------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_nexus_provider_accounts_user_provider
    ON public.nexus_provider_accounts (user_id, provider);

CREATE INDEX IF NOT EXISTS idx_nexus_provider_accounts_expires_at
    ON public.nexus_provider_accounts (expires_at)
    WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_oauth_states_provider_digest
    ON public.nexus_oauth_states (provider, state_digest);

CREATE INDEX IF NOT EXISTS idx_nexus_oauth_states_user_expires
    ON public.nexus_oauth_states (auth_user_sub, expires_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_oauth_states_unconsumed_expires
    ON public.nexus_oauth_states (expires_at)
    WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_ingest_runs_user_provider_started
    ON public.nexus_ingest_runs (user_id, provider, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingest_runs_user_started
    ON public.nexus_ingest_runs (user_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingest_runs_account_started
    ON public.nexus_ingest_runs (provider_account_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingest_runs_status_started
    ON public.nexus_ingest_runs (status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_user_provider
    ON public.nexus_ingested_artifacts (user_id, provider, imported_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_account
    ON public.nexus_ingested_artifacts (provider_account_id, imported_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_run
    ON public.nexus_ingested_artifacts (run_id);

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_ingest_run_legacy
    ON public.nexus_ingested_artifacts (ingest_run_id)
    WHERE ingest_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_status_imported
    ON public.nexus_ingested_artifacts (status, imported_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_ingested_artifacts_url
    ON public.nexus_ingested_artifacts (user_id, url);


-- updated_at triggers --------------------------------------------------------

DROP TRIGGER IF EXISTS trg_nexus_provider_accounts_updated_at
    ON public.nexus_provider_accounts;
CREATE TRIGGER trg_nexus_provider_accounts_updated_at
    BEFORE UPDATE ON public.nexus_provider_accounts
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

DROP TRIGGER IF EXISTS trg_nexus_ingest_runs_updated_at
    ON public.nexus_ingest_runs;
CREATE TRIGGER trg_nexus_ingest_runs_updated_at
    BEFORE UPDATE ON public.nexus_ingest_runs
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

DROP TRIGGER IF EXISTS trg_nexus_ingested_artifacts_updated_at
    ON public.nexus_ingested_artifacts;
CREATE TRIGGER trg_nexus_ingested_artifacts_updated_at
    BEFORE UPDATE ON public.nexus_ingested_artifacts
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


-- Row-level security ---------------------------------------------------------

ALTER TABLE public.nexus_provider_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nexus_oauth_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nexus_ingest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nexus_ingested_artifacts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS nexus_provider_accounts_select ON public.nexus_provider_accounts;
CREATE POLICY nexus_provider_accounts_select ON public.nexus_provider_accounts
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_provider_accounts_insert ON public.nexus_provider_accounts;
CREATE POLICY nexus_provider_accounts_insert ON public.nexus_provider_accounts
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_provider_accounts_update ON public.nexus_provider_accounts;
CREATE POLICY nexus_provider_accounts_update ON public.nexus_provider_accounts
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_provider_accounts_delete ON public.nexus_provider_accounts;
CREATE POLICY nexus_provider_accounts_delete ON public.nexus_provider_accounts
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_oauth_states_select ON public.nexus_oauth_states;
CREATE POLICY nexus_oauth_states_select ON public.nexus_oauth_states
    FOR SELECT USING ((SELECT auth.uid())::text = auth_user_sub);

DROP POLICY IF EXISTS nexus_oauth_states_insert ON public.nexus_oauth_states;
CREATE POLICY nexus_oauth_states_insert ON public.nexus_oauth_states
    FOR INSERT WITH CHECK ((SELECT auth.uid())::text = auth_user_sub);

DROP POLICY IF EXISTS nexus_oauth_states_update ON public.nexus_oauth_states;
CREATE POLICY nexus_oauth_states_update ON public.nexus_oauth_states
    FOR UPDATE USING ((SELECT auth.uid())::text = auth_user_sub);

DROP POLICY IF EXISTS nexus_oauth_states_delete ON public.nexus_oauth_states;
CREATE POLICY nexus_oauth_states_delete ON public.nexus_oauth_states
    FOR DELETE USING ((SELECT auth.uid())::text = auth_user_sub);

DROP POLICY IF EXISTS nexus_ingest_runs_select ON public.nexus_ingest_runs;
CREATE POLICY nexus_ingest_runs_select ON public.nexus_ingest_runs
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingest_runs_insert ON public.nexus_ingest_runs;
CREATE POLICY nexus_ingest_runs_insert ON public.nexus_ingest_runs
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingest_runs_update ON public.nexus_ingest_runs;
CREATE POLICY nexus_ingest_runs_update ON public.nexus_ingest_runs
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingest_runs_delete ON public.nexus_ingest_runs;
CREATE POLICY nexus_ingest_runs_delete ON public.nexus_ingest_runs
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingested_artifacts_select ON public.nexus_ingested_artifacts;
CREATE POLICY nexus_ingested_artifacts_select ON public.nexus_ingested_artifacts
    FOR SELECT USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingested_artifacts_insert ON public.nexus_ingested_artifacts;
CREATE POLICY nexus_ingested_artifacts_insert ON public.nexus_ingested_artifacts
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingested_artifacts_update ON public.nexus_ingested_artifacts;
CREATE POLICY nexus_ingested_artifacts_update ON public.nexus_ingested_artifacts
    FOR UPDATE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_ingested_artifacts_delete ON public.nexus_ingested_artifacts;
CREATE POLICY nexus_ingested_artifacts_delete ON public.nexus_ingested_artifacts
    FOR DELETE USING (
        EXISTS (
            SELECT 1
            FROM public.kg_users u
            WHERE u.id = user_id
              AND u.render_user_id = (SELECT auth.uid())::text
        )
    );

DROP POLICY IF EXISTS nexus_provider_accounts_service_all ON public.nexus_provider_accounts;
CREATE POLICY nexus_provider_accounts_service_all ON public.nexus_provider_accounts
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS nexus_oauth_states_service_all ON public.nexus_oauth_states;
CREATE POLICY nexus_oauth_states_service_all ON public.nexus_oauth_states
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS nexus_ingest_runs_service_all ON public.nexus_ingest_runs;
CREATE POLICY nexus_ingest_runs_service_all ON public.nexus_ingest_runs
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );

DROP POLICY IF EXISTS nexus_ingested_artifacts_service_all ON public.nexus_ingested_artifacts;
CREATE POLICY nexus_ingested_artifacts_service_all ON public.nexus_ingested_artifacts
    FOR ALL USING (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    )
    WITH CHECK (
        current_setting('request.jwt.claims', true)::jsonb ->> 'role' = 'service_role'
    );


-- Storage + bloat controls ---------------------------------------------------
-- Lower autovacuum thresholds for high-churn Nexus tables to avoid bloat.

ALTER TABLE public.nexus_oauth_states SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01
);

ALTER TABLE public.nexus_ingest_runs SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

ALTER TABLE public.nexus_ingested_artifacts SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
