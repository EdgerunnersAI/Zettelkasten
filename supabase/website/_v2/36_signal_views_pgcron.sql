-- Phase 8.5.B-3 — pg_cron schedules for signal MV refresh.
--
-- Cadence split (locked Item 3 — research-2):
--   - viz nightly       (24h staleness OK; viz aesthetics)
--   - retrieval hourly  (constant-improvement loop; user-feedback-driven boost)
--
-- pg_cron 1.6.4 confirmed available; 1 existing job (partman_run_maintenance).
-- Each cron entry calls kg.refresh_signal_mv(text), which logs to kg.mv_refresh_log.
--
-- Why pg_cron instead of GitHub Actions:
--   - GH Actions cron has documented drift (15min–several hours) + auto-disables after 60d.
--   - Old recompute_usage_edges.yml has been failing nightly since at least 2026-05-01
--     (root cause: empty SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY secrets in workflow).
--   - pg_cron runs inside Postgres with superuser privs — zero auth surface.

-- Idempotent: cron.schedule overwrites if jobname exists in pg_cron 1.5+ (RETURNING jobid).
-- We use a guarded unschedule + reschedule for clarity.
DO $$
BEGIN
    PERFORM cron.unschedule('refresh_kg_viz_weights')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh_kg_viz_weights');
EXCEPTION WHEN OTHERS THEN
    -- unschedule raises if jobname doesn't exist on older pg_cron versions; ignore.
    NULL;
END$$;

DO $$
BEGIN
    PERFORM cron.unschedule('refresh_kasten_retrieval_signals')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh_kasten_retrieval_signals');
EXCEPTION WHEN OTHERS THEN
    NULL;
END$$;

-- Viz: nightly at 03:15 UTC (avoids partman_run_maintenance at 02:15)
SELECT cron.schedule(
    'refresh_kg_viz_weights',
    '15 3 * * *',
    $cron$SELECT kg.refresh_signal_mv('kg.kg_edge_viz_weights')$cron$
);

-- Retrieval: every hour at :05 (avoids top-of-hour traffic spike)
SELECT cron.schedule(
    'refresh_kasten_retrieval_signals',
    '5 * * * *',
    $cron$SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')$cron$
);

-- Confirm both registered
DO $$
DECLARE
    n_jobs integer;
BEGIN
    SELECT count(*) INTO n_jobs FROM cron.job
     WHERE jobname IN ('refresh_kg_viz_weights', 'refresh_kasten_retrieval_signals');
    IF n_jobs <> 2 THEN
        RAISE EXCEPTION 'expected 2 signal-MV cron jobs, found %', n_jobs;
    END IF;
END$$;

NOTIFY pgrst, 'reload schema';
