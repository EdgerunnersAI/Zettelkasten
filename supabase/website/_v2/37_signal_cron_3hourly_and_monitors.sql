-- Phase 8.5.B-3-monitors — operator-approved changes 2026-05-10:
--
-- 1. Retrieval MV cron: hourly → 3-hourly. Reduces refresh count from 24/day
--    to 8/day at our 10–15 user volume; matches event-arrival sparsity.
-- 2. Add monitor views so we can determine optimal timing for THIS user
--    base from real data instead of generic SRE advice. Re-evaluate the
--    cron schedule after 14 days of data.
--
-- Viz cron unchanged at 15 3 * * * per current commit (f713352).

-- ---------------------------------------------------------------------------
-- 1) Retrieval MV cron: hourly → 3-hourly
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    PERFORM cron.alter_job(jobid, schedule := '5 */3 * * *')
      FROM cron.job
     WHERE jobname = 'refresh_kasten_retrieval_signals';
EXCEPTION WHEN OTHERS THEN
    -- Job not yet scheduled (e.g. fresh DB); fall through to schedule.
    NULL;
END$$;

-- Idempotent fallback if alter_job didn't find the job (fresh DB scenario)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM cron.job WHERE jobname = 'refresh_kasten_retrieval_signals'
    ) THEN
        PERFORM cron.schedule(
            'refresh_kasten_retrieval_signals',
            '5 */3 * * *',
            $cron$SELECT kg.refresh_signal_mv('rag.kasten_retrieval_edge_signals')$cron$
        );
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- 2) Monitor view: event traffic by hour-of-day (UTC + IST projection)
--    Used to determine empirical low-traffic window; refreshed on read.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW kg.event_traffic_by_hour AS
    SELECT
        EXTRACT(HOUR FROM created_at AT TIME ZONE 'UTC')::smallint  AS hour_utc,
        EXTRACT(HOUR FROM created_at AT TIME ZONE 'Asia/Kolkata')::smallint AS hour_ist,
        COUNT(*)                                                     AS event_count,
        COUNT(DISTINCT user_id)                                      AS distinct_users,
        COUNT(DISTINCT workspace_id)                                 AS distinct_workspaces,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '7 days')   AS last_7d,
        COUNT(*) FILTER (WHERE created_at > now() - INTERVAL '30 days')  AS last_30d
      FROM rag.retrieval_feedback_events
     WHERE created_at > now() - INTERVAL '90 days'
     GROUP BY hour_utc, hour_ist
     ORDER BY hour_utc;

GRANT SELECT ON kg.event_traffic_by_hour TO authenticated, service_role;
COMMENT ON VIEW kg.event_traffic_by_hour IS
    'Phase 8.5.B-3-monitors: empirical event-arrival distribution by hour-of-day. '
    'Use to determine optimal cron timing for current user base after 14d of data.';

-- ---------------------------------------------------------------------------
-- 3) Monitor view: signal MV refresh health
--    Joins cron.job + kg.mv_refresh_log to surface scheduled-vs-actual cadence
--    and the lag between expected and observed refresh.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW kg.signal_mv_refresh_health AS
    SELECT
        l.mv_name,
        c.jobname,
        c.schedule,
        c.active                                                AS cron_active,
        l.refreshed_at                                          AS last_refreshed_at,
        l.refresh_ms                                            AS last_refresh_ms,
        EXTRACT(EPOCH FROM (now() - l.refreshed_at))::integer   AS seconds_since_refresh,
        CASE
            WHEN c.schedule = '15 3 * * *'   THEN 86400  -- daily
            WHEN c.schedule = '5 */3 * * *'  THEN 10800  -- 3-hourly
            WHEN c.schedule = '5 * * * *'    THEN 3600   -- hourly (legacy)
            ELSE NULL
        END                                                     AS expected_cadence_seconds,
        CASE
            WHEN c.schedule = '15 3 * * *'
                 AND now() - l.refreshed_at > INTERVAL '25 hours'  THEN 'STALE'
            WHEN c.schedule = '5 */3 * * *'
                 AND now() - l.refreshed_at > INTERVAL '4 hours'   THEN 'STALE'
            WHEN c.schedule = '5 * * * *'
                 AND now() - l.refreshed_at > INTERVAL '2 hours'   THEN 'STALE'
            ELSE 'FRESH'
        END                                                     AS health_status
      FROM kg.mv_refresh_log l
      LEFT JOIN cron.job c
        ON (l.mv_name = 'kg.kg_edge_viz_weights'
              AND c.jobname = 'refresh_kg_viz_weights')
        OR (l.mv_name = 'rag.kasten_retrieval_edge_signals'
              AND c.jobname = 'refresh_kasten_retrieval_signals');

GRANT SELECT ON kg.signal_mv_refresh_health TO authenticated, service_role;
COMMENT ON VIEW kg.signal_mv_refresh_health IS
    'Phase 8.5.B-3-monitors: per-MV refresh health. STALE means observed lag '
    'exceeds expected cadence + slop. Alarm trigger for ops.';

-- ---------------------------------------------------------------------------
-- 4) Helper function: recommend low-traffic window from observed events
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION kg.recommended_cron_low_traffic_hour(
    p_min_events_for_recommendation integer DEFAULT 100
) RETURNS TABLE(
    hour_utc      smallint,
    hour_ist      smallint,
    event_count   bigint,
    confidence    text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    WITH totals AS (
        SELECT SUM(t.event_count) AS total FROM kg.event_traffic_by_hour t
    )
    SELECT
        t.hour_utc,
        t.hour_ist,
        t.event_count,
        CASE
            WHEN (SELECT total FROM totals) < p_min_events_for_recommendation
                THEN 'INSUFFICIENT_DATA'
            WHEN t.event_count = 0 THEN 'HIGH'
            WHEN t.event_count <= (SELECT total / 24 FROM totals) / 4 THEN 'HIGH'
            WHEN t.event_count <= (SELECT total / 24 FROM totals) / 2 THEN 'MEDIUM'
            ELSE 'LOW'
        END AS confidence
      FROM kg.event_traffic_by_hour t
     ORDER BY t.event_count ASC, t.hour_utc ASC
     LIMIT 5;
$$;

REVOKE ALL ON FUNCTION kg.recommended_cron_low_traffic_hour(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kg.recommended_cron_low_traffic_hour(integer) TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';
