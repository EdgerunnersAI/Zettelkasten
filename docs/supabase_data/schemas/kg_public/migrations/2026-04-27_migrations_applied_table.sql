-- ============================================================================
-- 2026-04-27_migrations_applied_table.sql
--
-- Bootstrap table for the apply_migrations.py runner (D-1).
--
-- Tracks which migration files have been applied to this database, in lexical
-- order, exactly once. The runner self-bootstraps this table on a fresh DB,
-- but committing it as a regular migration keeps the schema reproducible from
-- this directory alone.
-- ============================================================================

CREATE TABLE IF NOT EXISTS _migrations_applied (
  name        TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  checksum    TEXT NOT NULL,
  applied_by  TEXT
);
