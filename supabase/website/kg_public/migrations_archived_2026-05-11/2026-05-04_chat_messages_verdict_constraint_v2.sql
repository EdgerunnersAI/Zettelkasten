-- iter-09 RES-7 + RES-1: drop+recreate chat_messages_critic_verdict_check
-- to allow new verdict strings shipped iter-08..iter-09 without breaking
-- existing rows. Backwards-compatible: validates new inserts only.
BEGIN;

ALTER TABLE chat_messages
    DROP CONSTRAINT IF EXISTS chat_messages_critic_verdict_check;

ALTER TABLE chat_messages
    ADD CONSTRAINT chat_messages_critic_verdict_check
    CHECK (
        critic_verdict IS NULL OR critic_verdict IN (
            'supported',
            'partial',
            'unsupported',
            'retried_supported',
            'retried_low_confidence',
            'retry_failed',
            'retry_skipped_dejavu',
            'unsupported_no_retry',
            'partial_with_gold_skip',
            'retry_budget_exceeded',
            'unsupported_with_gold_skip'
        )
    ) NOT VALID;

ALTER TABLE chat_messages
    VALIDATE CONSTRAINT chat_messages_critic_verdict_check;

COMMIT;
