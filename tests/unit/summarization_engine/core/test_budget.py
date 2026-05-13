"""Unit tests for the per-request LLM call budget (3-call cap)."""
from __future__ import annotations

import asyncio

import pytest

from website.features.summarization_engine.core.budget import (
    Budget,
    BudgetExceeded,
    _BUDGET,
    budget_scope,
    get_budget,
    llm_budget_dep,
)


def test_consume_decrements():
    b = Budget(limit=3, summarizer="t")
    b.consume(role="dense_verify")
    b.consume(role="summarizer")
    assert b.used == 2
    assert b.overrun_attempts == 0


def test_consume_raises_on_fourth_call():
    b = Budget(limit=3, summarizer="t")
    b.consume(role="dense_verify")
    b.consume(role="summarizer")
    b.consume(role="patch")
    with pytest.raises(BudgetExceeded):
        b.consume(role="extra")
    assert b.used == 3
    assert b.overrun_attempts == 1


def test_get_budget_returns_noop_default_outside_scope():
    # Reset the ContextVar so we simulate "no scope set" cleanly.
    tok = _BUDGET.set(None)
    try:
        b = get_budget()
        assert isinstance(b, Budget)
        assert b.summarizer == "test"
        # Default budget should still enforce — it's a Budget, just lazy.
        b.consume(role="x")
        assert b.used == 1
    finally:
        _BUDGET.reset(tok)


@pytest.mark.asyncio
async def test_budget_scope_sets_and_resets():
    # Ensure outer scope is clean.
    tok = _BUDGET.set(None)
    try:
        async with budget_scope(summarizer="youtube") as b:
            inner = get_budget()
            assert inner is b
            assert inner.summarizer == "youtube"
            b.consume(role="dense_verify")
            assert b.used == 1
        # After exit, ContextVar is reset to prior value (None).
        assert _BUDGET.get() is None
    finally:
        _BUDGET.reset(tok)


@pytest.mark.asyncio
async def test_llm_budget_dep_async_generator():
    tok = _BUDGET.set(None)
    try:
        agen = llm_budget_dep(summarizer="github")
        b = await agen.__anext__()
        assert isinstance(b, Budget)
        assert b.summarizer == "github"
        b.consume(role="summarizer")
        # Close the async generator (mimics FastAPI cleanup).
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        assert _BUDGET.get() is None
    finally:
        _BUDGET.reset(tok)


@pytest.mark.asyncio
async def test_concurrent_tasks_have_independent_budgets():
    """ContextVar isolation: two concurrent asyncio Tasks each get their own
    Budget; consuming in one MUST NOT affect the other.
    """
    results: dict[str, int] = {}

    async def worker(name: str, calls: int) -> None:
        async with budget_scope(summarizer=name) as b:
            for _ in range(calls):
                b.consume(role="r")
                await asyncio.sleep(0)  # yield to interleave
            results[name] = b.used

    await asyncio.gather(
        worker("a", 3),
        worker("b", 2),
        worker("c", 1),
    )
    assert results == {"a": 3, "b": 2, "c": 1}


@pytest.mark.asyncio
async def test_concurrent_overrun_in_one_does_not_affect_other():
    async def overrunner() -> int:
        async with budget_scope(summarizer="bad") as b:
            b.consume(role="r")
            b.consume(role="r")
            b.consume(role="r")
            try:
                b.consume(role="r")
            except BudgetExceeded:
                return b.overrun_attempts
            return -1

    async def well_behaved() -> int:
        async with budget_scope(summarizer="good") as b:
            b.consume(role="r")
            return b.used

    over, good = await asyncio.gather(overrunner(), well_behaved())
    assert over == 1
    assert good == 1
