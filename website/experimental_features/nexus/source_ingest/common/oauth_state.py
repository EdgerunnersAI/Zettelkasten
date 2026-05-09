"""Ephemeral OAuth state for the Nexus connect flow.

Phase 3.5 of the v2 purge: rebased off the legacy
``public.nexus_oauth_states`` table onto an in-memory store with TTL.
Rationale:

* OAuth state lives ~10 minutes between ``/connect`` and ``/callback``.
  A short-lived row in the database is overkill for this use, and
  ties the v2 cutover to a legacy public-schema table that the v2
  schema deliberately doesn't model.
* All four nexus surfaces are accessed by the same web instance that
  issues the state (Caddy → uvicorn → app), so an in-memory store is
  topology-correct: blue/green cutovers happen at the load-balancer
  level and a re-issue ("retry connect") is the natural recovery path
  if a state was issued by a stopped color.
* Module-level dict is bounded by `_MAX_STATES` to cap memory under
  pathological enumerate-the-state attacks. Eviction is lazy +
  oldest-first.

Public symbol signatures preserved byte-for-byte:
``issue_oauth_state(...) -> tuple[str, OAuthStateRecord]`` and
``consume_oauth_state(provider, state_token) -> OAuthStateRecord``.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from website.experimental_features.nexus.source_ingest.common.models import (
    NexusProvider,
    OAuthStateRecord,
)

_STATE_TTL = timedelta(minutes=10)
_MAX_STATES = 5000

_STATES: dict[tuple[str, str], OAuthStateRecord] = {}
_STATES_LOCK = threading.Lock()


def issue_oauth_state(
    *,
    provider: NexusProvider,
    auth_user_sub: str,
    redirect_path: str | None = "/home/nexus",
    code_verifier: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, OAuthStateRecord]:
    state_token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + _STATE_TTL
    record = OAuthStateRecord(
        id=uuid4(),
        provider=provider,
        auth_user_sub=auth_user_sub,
        redirect_path=redirect_path,
        code_verifier=code_verifier,
        metadata=metadata or {},
        expires_at=expires_at,
        consumed_at=None,
        created_at=_utcnow(),
    )
    key = (provider.value, _digest(state_token))
    with _STATES_LOCK:
        _maybe_evict_locked()
        _STATES[key] = record
    return state_token, record


def consume_oauth_state(provider: NexusProvider, state_token: str) -> OAuthStateRecord:
    """Atomically consume the state. Re-use, expiry, and unknown tokens
    raise ValueError exactly as the legacy SQL path did."""
    key = (provider.value, _digest(state_token))
    now = _utcnow()
    with _STATES_LOCK:
        record = _STATES.get(key)
        if record is None:
            raise ValueError("Invalid OAuth state")
        if record.consumed_at is not None:
            raise ValueError("OAuth state has already been used")
        if record.expires_at <= now:
            # Drop expired record so memory doesn't keep growing.
            _STATES.pop(key, None)
            raise ValueError("OAuth state has expired")
        consumed = record.model_copy(update={"consumed_at": now})
        _STATES[key] = consumed
    return consumed


def _maybe_evict_locked() -> None:
    """Caller MUST hold _STATES_LOCK. Drops the oldest record if at cap."""
    if len(_STATES) < _MAX_STATES:
        return
    # Oldest by expires_at (created_at would also work; same age relation).
    oldest_key = min(_STATES, key=lambda k: _STATES[k].expires_at)
    _STATES.pop(oldest_key, None)


def _digest(state_token: str) -> str:
    return hashlib.sha256(state_token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reset_state_for_tests() -> None:
    """Test hook — clears the in-memory store between tests."""
    with _STATES_LOCK:
        _STATES.clear()
