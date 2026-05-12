-- DB v2 bootstrap extensions and migration tracker.
-- Apply first. HNSW indexes are intentionally excluded until 10_hnsw_indexes.sql.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
-- pg_partman 5.4.1+ requires the target schema to exist BEFORE CREATE EXTENSION
-- (upstream issue #842). Schema-first ordering is the only supported path.
CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
CREATE EXTENSION IF NOT EXISTS pg_cron;

CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core._migrations_applied (
    name             text PRIMARY KEY,
    applied_at       timestamptz NOT NULL DEFAULT now(),
    checksum         text NOT NULL,
    applied_by       text,
    deploy_git_sha   text,
    deploy_id        text,
    deploy_actor     text,
    runner_hostname  text
);

