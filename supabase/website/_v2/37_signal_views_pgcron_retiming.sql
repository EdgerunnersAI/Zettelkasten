-- Phase 8.5.B-3 retiming — research-3 audit (2026-05-10).
--
-- Original schedules in 36_signal_views_pgcron.sql were a literal mapping of
-- "viz nightly, retrieval hourly". The audit (research-3) flagged two issues:
--
-- 1. `15 3 * * *` UTC = 08:45 IST = India morning-commute spike for our
--    India-centric early users. Razorpay / Freshworks / Zoho schedule
--    analytics rollups around 20:00-22:00 UTC = 01:30-03:30 IST (genuine
--    night). Move to 20:37 UTC = 02:07 IST.
--
-- 2. `5 * * * *` collides with the `*/5` thundering herd shared by cloud-
--    provider system crons, Caddy log rotation, and most default tools.
--    Google SRE Book § Distributed Periodic Scheduling explicitly warns
--    against round-minute alignment. Move to `:17` (prime, off the crowd,
--    30-min spaced from the nightly `:37`).
--
-- pg_cron serial-execution guarantee covers overlapping-run protection
-- automatically (jobs queue, never spawn parallel; per citusdata/pg_cron).
-- Advisory-lock belt-and-suspenders deferred until ≥5k MAU.

DO $$
BEGIN
    PERFORM cron.unschedule('refresh_kg_viz_weights')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh_kg_viz_weights');
EXCEPTION WHEN OTHERS THEN
    NULL;
END$$;

DO $$
BEGIN
    PERFORM cron.unschedule('refresh_kasten_retrieval_signals')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh_kasten_retrieval_signals');
EXCEPTION WHEN OTHERS THEN
    NULL;
END$$;

-- Viz: 20:37 UTC nightly (02:07 IST = genuine India night; well clear of
-- partman_run_maintenance at 02:15 UTC; off-peak in EST/EU late-evening too)
SELECT cron.schedule(
    'refresh_kg_viz_weights',
    '37 20 * * *',
    $cron$SELECT kg.refresh_signal_mv('kg.kg_edge_viz_weights')$cron$
);

-- Retrieval: :17 past every hour (prime minute, off the :00/:05/:15/:30/:45
-- crowd; deliberately offset from the nightly :37 so they never collide)
SELECT cron.schedule(
    'refresh_kasten_retrieval_signals',
    '17 * * * *',
    $cron$SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')$cron$
);

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
