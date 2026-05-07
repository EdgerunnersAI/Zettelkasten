"""iter-12 Task 35: per-query telemetry — retry_outcome_class enum + classifier."""
import pytest


def test_classify_empty_pool():
    from website.features.rag_pipeline.orchestrator import (
        RetryOutcomeClass, classify_retry_outcome,
    )
    assert classify_retry_outcome(
        retrieved_count=0, retry_fired=True, timed_out=False,
        critic_verdict="retry_budget_exceeded",
    ) == RetryOutcomeClass.EMPTY_POOL


def test_classify_timeout():
    from website.features.rag_pipeline.orchestrator import (
        RetryOutcomeClass, classify_retry_outcome,
    )
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=True,
        critic_verdict="retry_budget_exceeded",
    ) == RetryOutcomeClass.TIMEOUT


def test_classify_floor_failed():
    from website.features.rag_pipeline.orchestrator import (
        RetryOutcomeClass, classify_retry_outcome,
    )
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=False,
        critic_verdict="unsupported_with_gold_skip",
    ) == RetryOutcomeClass.FLOOR_FAILED


def test_classify_success():
    from website.features.rag_pipeline.orchestrator import (
        RetryOutcomeClass, classify_retry_outcome,
    )
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=False, timed_out=False,
        critic_verdict="supported",
    ) == RetryOutcomeClass.SUCCESS


def test_classify_still_unsupported():
    from website.features.rag_pipeline.orchestrator import (
        RetryOutcomeClass, classify_retry_outcome,
    )
    assert classify_retry_outcome(
        retrieved_count=5, retry_fired=True, timed_out=False,
        critic_verdict="unsupported_no_retry",
    ) == RetryOutcomeClass.STILL_UNSUPPORTED
