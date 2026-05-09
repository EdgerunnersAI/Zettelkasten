"""Persistent chat session storage over Supabase DB v2.

Phase 2.7 of the v2 purge: rewires this module from the legacy
``chat_sessions`` / ``chat_messages`` tables to ``rag.chat_sessions`` /
``rag.chat_messages``.

CRITICAL: ``workspace_id`` is NOT NULL on both v2 tables. Every insert
MUST pass a workspace_id or PostgREST will raise
``null value in column "workspace_id" violates not-null constraint``.
This module derives workspace_id by treating the v1 ``user_id`` argument
(an auth/profile UUID) as a profile_id and looking up the profile's
default workspace via ``core.workspace_members``. The resolved value
is cached per (profile_id) for the store's lifetime.

Public class name + method signatures are preserved byte-for-byte:
``user_id`` is the v1 profile UUID, and ``sandbox_id`` is the v1 alias
for ``kasten_id``. Internally the store maps these onto the v2 schema.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from website.core.supabase_v2.repositories.core_repository import CoreRepository
from website.core.supabase_v2.repositories.rag_repository import RAGRepository
from website.features.rag_pipeline.types import AnswerTurn, ChatTurn

_REWRITER_WINDOW = 5


class _UnknownWorkspaceError(RuntimeError):
    """Raised when a profile has no default workspace — shouldn't happen
    for a properly-provisioned auth user, but fail loud rather than swallow."""


class ChatSessionStore:
    def __init__(
        self,
        supabase: Any | None = None,  # legacy positional/keyword for back-compat
        *,
        repo: RAGRepository | None = None,
        core_repo: CoreRepository | None = None,
    ) -> None:
        # ``supabase`` was the legacy v1 client; ignored under v2 because the
        # repositories instantiate their own clients. Kept as a no-op kwarg so
        # existing call sites continue to construct without error.
        del supabase
        self._repo = repo or RAGRepository()
        self._core = core_repo or CoreRepository()
        self._workspace_cache: dict[UUID, UUID] = {}

    def _resolve_workspace_id(self, user_id: Any) -> UUID:
        """Map a profile UUID -> workspace UUID (cached, NOT NULL guarantee).

        ``user_id`` is the v1 profile_id (auth.users.id). For v2 inserts we
        need the workspace_id, derived from ``core.workspace_members``.
        Raises ``_UnknownWorkspaceError`` if the profile has no default
        workspace — this is a misconfiguration, not a recoverable runtime
        condition, and we propagate rather than swallow.
        """
        profile_id = UUID(str(user_id))
        cached = self._workspace_cache.get(profile_id)
        if cached is not None:
            return cached
        workspace_id = self._core.get_default_workspace_id(profile_id)
        if workspace_id is None:
            raise _UnknownWorkspaceError(
                f"profile {profile_id} has no default workspace; cannot insert v2 chat row"
            )
        self._workspace_cache[profile_id] = workspace_id
        return workspace_id

    async def create_session(
        self,
        *,
        user_id,
        sandbox_id,
        title="New conversation",
        initial_scope_filter=None,  # accepted for back-compat; v2 has no scope_filter column
        quality_mode="fast",  # accepted for back-compat; v2 has no quality_mode column
    ):
        del initial_scope_filter, quality_mode
        workspace_id = self._resolve_workspace_id(user_id)
        kasten_id = UUID(str(sandbox_id)) if sandbox_id else None
        session_id = self._repo.create_chat_session(
            workspace_id=workspace_id,
            profile_id=UUID(str(user_id)),
            kasten_id=kasten_id,
            title=title,
        )
        return session_id

    async def get_session(self, session_id, user_id):
        workspace_id = self._resolve_workspace_id(user_id)
        return self._repo.get_chat_session(UUID(str(session_id)), workspace_id)

    async def list_sessions(self, user_id, sandbox_id=None, limit=50):
        workspace_id = self._resolve_workspace_id(user_id)
        kasten_id = UUID(str(sandbox_id)) if sandbox_id else None
        return self._repo.list_chat_sessions(
            workspace_id=workspace_id, kasten_id=kasten_id, limit=limit,
        )

    async def list_messages(self, session_id, user_id, limit=100):
        workspace_id = self._resolve_workspace_id(user_id)
        return self._repo.list_chat_messages(
            session_id=UUID(str(session_id)),
            workspace_id=workspace_id,
            limit=limit,
        )

    async def delete_session(self, session_id, user_id):
        workspace_id = self._resolve_workspace_id(user_id)
        return self._repo.delete_chat_session(UUID(str(session_id)), workspace_id)

    async def update_session(
        self,
        session_id,
        user_id,
        *,
        sandbox_id=None,
        title=None,
        last_scope_filter=None,  # back-compat; no v2 column
        quality_mode=None,  # back-compat; no v2 column
    ):
        del last_scope_filter, quality_mode
        workspace_id = self._resolve_workspace_id(user_id)
        kasten_id = UUID(str(sandbox_id)) if sandbox_id else None
        if kasten_id is None and title is None:
            return None
        return self._repo.update_chat_session(
            UUID(str(session_id)),
            workspace_id,
            kasten_id=kasten_id,
            title=title,
        )

    async def load_recent_turns(self, session_id, user_id, limit=_REWRITER_WINDOW):
        workspace_id = self._resolve_workspace_id(user_id)
        rows = self._repo.list_chat_messages(
            session_id=UUID(str(session_id)),
            workspace_id=workspace_id,
            limit=limit * 4,  # over-fetch to allow tail-window slicing
        )
        # Take the most-recent ``limit`` rows in chronological order.
        tail = rows[-limit:] if len(rows) > limit else rows
        return [
            ChatTurn(role=r["role"], content=r["content"], created_at=r["created_at"])
            for r in tail
        ]

    async def append_user_message(self, *, session_id, user_id, content):
        workspace_id = self._resolve_workspace_id(user_id)
        return self._repo.append_chat_message(
            session_id=UUID(str(session_id)),
            workspace_id=workspace_id,
            role="user",
            content=content,
        )

    async def append_assistant_message(self, *, session_id, user_id, turn: AnswerTurn):
        workspace_id = self._resolve_workspace_id(user_id)
        citations = [c.model_dump(mode="json") for c in turn.citations]
        token_counts = turn.token_counts if isinstance(turn.token_counts, dict) else None
        return self._repo.append_chat_message(
            session_id=UUID(str(session_id)),
            workspace_id=workspace_id,
            role="assistant",
            content=turn.content,
            citations=citations,
            verdict=turn.critic_verdict if turn.critic_verdict in {
                "supported", "unsupported", "retried_supported", "partial",
            } else None,
            token_counts=token_counts,
            latency_ms=turn.latency_ms,
        )

    async def auto_title_session(self, session_id, user_id, first_query: str):
        title = first_query.strip().split("\n")[0][:60]
        if len(title) == 60:
            title = title.rstrip() + "..."
        workspace_id = self._resolve_workspace_id(user_id)
        self._repo.update_chat_session(
            UUID(str(session_id)),
            workspace_id,
            title=title,
        )
        return title
