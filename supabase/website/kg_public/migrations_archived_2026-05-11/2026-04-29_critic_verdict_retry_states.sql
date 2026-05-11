-- 2026-04-29 iter-03 §B
-- Expand chat_messages.critic_verdict CHECK constraint to include the
-- retried_low_confidence state introduced by spec 2A.2.
--
-- Symptom: every multi-hop query that ended up with the 2nd-pass
-- low-confidence draft path raised
--   APIError: violates check constraint "chat_messages_critic_verdict_check"
-- on session_store.append_assistant_message → 500 to client. The
-- application code (website/features/rag_pipeline/types.py:111) and the
-- orchestrator have always emitted retried_low_confidence; the DB
-- constraint was never updated.
--
-- This migration: drop + recreate the CHECK with all six allowed verdict
-- values. Idempotent because the new constraint name matches what
-- 004_chat_sessions.sql created.

ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_critic_verdict_check;

ALTER TABLE chat_messages
    ADD CONSTRAINT chat_messages_critic_verdict_check
    CHECK (
        critic_verdict IS NULL
        OR critic_verdict IN (
            'supported',
            'partial',
            'unsupported',
            'retried_supported',
            'retried_still_bad',
            'retried_low_confidence'
        )
    );
