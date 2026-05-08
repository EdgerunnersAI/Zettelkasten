-- Audit-trail columns for _migrations_applied (iter-03 §1C.4 atomic group #1).
-- Lets every applied migration row name the deploy that ran it: git SHA,
-- workflow run id, the GH actor who triggered the deploy, and the runner
-- hostname (separate from applied_by which historically stored hostname).

ALTER TABLE _migrations_applied
  ADD COLUMN IF NOT EXISTS deploy_git_sha   TEXT,
  ADD COLUMN IF NOT EXISTS deploy_id        TEXT,
  ADD COLUMN IF NOT EXISTS deploy_actor     TEXT,
  ADD COLUMN IF NOT EXISTS runner_hostname  TEXT;

-- Backfill runner_hostname from existing applied_by so historical rows
-- have a value in the new column.
UPDATE _migrations_applied
   SET runner_hostname = applied_by
 WHERE runner_hostname IS NULL;
