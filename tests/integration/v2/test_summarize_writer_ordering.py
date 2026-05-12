"""SE-04: writer ordering — entitlement consumed only after successful persist.

Per the WAVE-C discovery (`docs/superpowers/plans/2026-05-12-wave-c-discovery.md`
SE-04) the contract has two layers:

  * Inside ``SupabaseWriter.write`` (``website/features/summarization_engine
    /writers/supabase.py:104-157``): ``_resolve_workspace_id`` MUST be called
    BEFORE the upsert RPC. If the user has no default workspace we raise
    ``_UnknownWorkspaceError`` and skip persistence entirely.
  * In the legacy ``/api/summarize`` route (``website/api/routes.py:806``):
    ``consume_entitlement`` MUST run only AFTER ``persist_summarized_result``
    returns. If the persist raises, the entitlement debit must NOT happen
    (otherwise users get billed for failed summaries).

We verify both via call-order recording, plus the inverse: a persist failure
short-circuits the consume call.

Anti-pattern guard: per CLAUDE.md pricing-module authority, we do NOT seed
entitlements, do NOT call ``billing.pricing_consume_entitlement``, and do
NOT alter SQL bodies. We just record call ordering at the Python boundary.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from website.features.summarization_engine.core.errors import WriterError
from website.features.summarization_engine.core.models import (
    SourceType,
    SummaryMetadata,
)


def _make_summary_result():
    """Minimal SummaryResult-shaped object for the writer."""
    metadata = SummaryMetadata(
        source_type=SourceType.WEB,
        url="https://example.com/article",
        extraction_confidence="high",
        confidence_reason="ok",
        total_tokens_used=100,
        total_latency_ms=200,
    )
    # SupabaseWriter calls .model_dump on the full result; build a tiny
    # stand-in with the fields the writer actually reads.
    return SimpleNamespace(
        mini_title="Test Title",
        brief_summary="Brief.",
        detailed_summary=[],
        tags=["test"],
        metadata=metadata,
        model_dump=lambda mode="json": {
            "mini_title": "Test Title",
            "brief_summary": "Brief.",
            "detailed_summary": [],
            "tags": ["test"],
        },
    )


# --- writer-internal ordering: workspace resolved BEFORE persist ----------


@pytest.mark.asyncio
async def test_writer_resolves_workspace_before_upsert() -> None:
    """``SupabaseWriter.write`` must call ``_resolve_workspace_id`` before
    the canonical-zettel upsert. Otherwise the upsert could succeed against
    a NULL workspace_id (the v2 NOT NULL constraint would catch it, but the
    canonical row could already be inserted as an orphan)."""
    from website.features.summarization_engine.writers.supabase import (
        SupabaseWriter,
    )

    call_order: list[str] = []
    workspace_id = uuid.uuid4()
    canonical_id = uuid.uuid4()

    fake_core = MagicMock()
    def _resolve(_uid):
        call_order.append("resolve_workspace")
        return workspace_id
    fake_core.get_default_workspace_id.side_effect = _resolve

    fake_repo = MagicMock()
    def _upsert(*_a, **_kw):
        call_order.append("upsert_canonical")
        return SimpleNamespace(
            canonical_zettel_id=canonical_id,
            workspace_zettel_id=uuid.uuid4(),
            was_new=True,
        )
    fake_repo.upsert_canonical_zettel.side_effect = _upsert

    writer = SupabaseWriter(repository=fake_repo, core_repo=fake_core)
    result = _make_summary_result()
    user_id = uuid.uuid4()

    out = await writer.write(result, user_id=user_id)

    assert call_order == ["resolve_workspace", "upsert_canonical"], (
        f"writer call order broken: {call_order}"
    )
    assert out["status"] == "created"
    assert out["node_id"] == str(canonical_id)


@pytest.mark.asyncio
async def test_writer_fails_loudly_when_user_has_no_workspace() -> None:
    """Profile with no default workspace → _UnknownWorkspaceError, no upsert.

    Validates the "fail loud" branch in ``_resolve_workspace_id``. Without
    this guard the writer would attempt an INSERT with NULL workspace_id and
    leak a half-state on a constraint violation.
    """
    from website.features.summarization_engine.writers.supabase import (
        SupabaseWriter,
        _UnknownWorkspaceError,
    )

    fake_core = MagicMock()
    fake_core.get_default_workspace_id.return_value = None

    fake_repo = MagicMock()
    fake_repo.upsert_canonical_zettel.side_effect = AssertionError(
        "must NOT be called when workspace resolution fails"
    )

    writer = SupabaseWriter(repository=fake_repo, core_repo=fake_core)
    with pytest.raises(_UnknownWorkspaceError):
        await writer.write(_make_summary_result(), user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_writer_wraps_persist_exception_in_writer_error() -> None:
    """If the upsert raises, the writer wraps in ``WriterError`` and DOES
    NOT silently swallow it. Callers (the route) MUST see the failure so the
    entitlement debit is skipped."""
    from website.features.summarization_engine.writers.supabase import (
        SupabaseWriter,
    )

    fake_core = MagicMock()
    fake_core.get_default_workspace_id.return_value = uuid.uuid4()
    fake_repo = MagicMock()
    fake_repo.upsert_canonical_zettel.side_effect = RuntimeError("db down")

    writer = SupabaseWriter(repository=fake_repo, core_repo=fake_core)
    with pytest.raises(WriterError):
        await writer.write(_make_summary_result(), user_id=uuid.uuid4())


# --- route-level ordering: consume_entitlement AFTER persist --------------


@pytest.mark.asyncio
async def test_route_consumes_entitlement_after_persist_success(monkeypatch):
    """Patch the four collaborators of the legacy ``/api/summarize`` handler
    and verify the call order:

        require_entitlement → summarize_url → persist_summarized_result
        → consume_entitlement
    """
    from website.api import routes as routes_mod

    order: list[str] = []

    async def fake_require(*_a, **_kw):
        order.append("require")

    async def fake_summarize(*_a, **_kw):
        order.append("summarize")
        return {
            "title": "T",
            "summary": "S",
            "tags": [],
            "source_type": "web",
            "source_url": "https://example.com",
            "is_raw_fallback": False,
            "tokens_used": 10,
            "latency_ms": 20,
        }

    async def fake_persist(_result, *, user_sub):
        order.append("persist")
        return SimpleNamespace(
            result=_result,
            supabase_saved=False,
            file_saved=True,
        )

    async def fake_consume(*_a, **_kw):
        order.append("consume")

    monkeypatch.setattr(routes_mod, "require_entitlement", fake_require)
    monkeypatch.setattr(routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(routes_mod, "persist_summarized_result", fake_persist)
    monkeypatch.setattr(routes_mod, "consume_entitlement", fake_consume)
    # Rate limiter must allow the call.
    monkeypatch.setattr(routes_mod, "_check_rate_limit", lambda _ip: True)

    body = SimpleNamespace(url="https://example.com", client_action_id="a-1")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    await routes_mod.summarize(body=body, request=request, user=None)

    assert order == ["require", "summarize", "persist", "consume"], (
        f"route call order broken: {order}"
    )


@pytest.mark.asyncio
async def test_route_does_not_consume_when_persist_fails(monkeypatch):
    """Persist failure → consume_entitlement is NEVER called.

    This is the billing-correctness invariant: a user must not be debited
    a Zettel for a write that didn't land.
    """
    from website.api import routes as routes_mod

    consume_called = {"n": 0}

    async def fake_require(*_a, **_kw):
        return None

    async def fake_summarize(*_a, **_kw):
        return {
            "title": "T",
            "summary": "S",
            "tags": [],
            "source_type": "web",
            "source_url": "https://example.com",
            "is_raw_fallback": False,
            "tokens_used": 10,
            "latency_ms": 20,
        }

    async def fake_persist(*_a, **_kw):
        raise RuntimeError("supabase down")

    async def fake_consume(*_a, **_kw):
        consume_called["n"] += 1

    monkeypatch.setattr(routes_mod, "require_entitlement", fake_require)
    monkeypatch.setattr(routes_mod, "summarize_url", fake_summarize)
    monkeypatch.setattr(routes_mod, "persist_summarized_result", fake_persist)
    monkeypatch.setattr(routes_mod, "consume_entitlement", fake_consume)
    monkeypatch.setattr(routes_mod, "_check_rate_limit", lambda _ip: True)

    body = SimpleNamespace(url="https://example.com", client_action_id="a-1")
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with pytest.raises(Exception):
        await routes_mod.summarize(body=body, request=request, user=None)

    assert consume_called["n"] == 0, (
        "BILLING BUG: consume_entitlement called even though persist failed"
    )
