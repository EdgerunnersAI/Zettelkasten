-- Phase 8.0 Rev+: migrate stale 39_introspect manifest row to repeatable form.
--
-- Context: 39_introspect_auth_users_dependents.sql was applied to prod from an
-- uncommitted working-tree variant, leaving core._migrations_applied with a
-- checksum that no longer matches any committed file. Re-classifying the
-- migration as a Repeatable (R__) means apply_migrations.py's repeatable-loop
-- will re-record it under its new name (R__introspect_auth_users_dependents.sql)
-- on next deploy. This versioned migration deletes the now-orphan row so the
-- versioned-loop stops failing with CHECKSUM MISMATCH.
--
-- Ordering invariant (verified in apply_migrations.py main()):
--   versioned-loop (this file runs)  →  repeatable-loop (R__ file runs)
-- so the DELETE here lands before R__'s INSERT.

DELETE FROM core._migrations_applied
 WHERE name = '39_introspect_auth_users_dependents.sql';

NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
