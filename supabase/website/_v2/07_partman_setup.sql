-- DB v2 pg_partman setup for core.usage_events.

DO $$
BEGIN
    PERFORM partman.create_parent(
        p_parent_table := 'core.usage_events',
        p_control := 'occurred_at',
        p_type := 'range',
        p_interval := '1 month',
        p_premake := 3
    );
EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = '42710' THEN
        RAISE NOTICE 'partman parent already configured for core.usage_events';
    ELSE
        RAISE;
    END IF;
END
$$;

DO $$
BEGIN
    PERFORM cron.schedule(
        'partman_run_maintenance',
        '15 2 * * *',
        $cron$SELECT partman.run_maintenance();$cron$
    );
EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'cron job partman_run_maintenance already exists';
END
$$;
