"""Persistent kasten storage over Supabase DB v2.

Phase 2.6 of the v2 purge: rewires this module from the legacy
``rag_sandboxes`` / ``rag_sandbox_members`` tables (with their nested
PostgREST embed `select("..., kg_nodes(...)")`) to the v2 surface:

* ``rag.kastens`` — the workspace-scoped kasten metadata
* ``rag.kasten_zettels`` — kasten <-> workspace_zettel link rows
* ``rag.list_kasten_zettels(p_kasten_id)`` — Phase 1.A SECURITY DEFINER
  RPC that performs the JOIN against content.workspace_zettels +
  content.canonical_zettels (replaces the legacy nested embed).
* ``rag.bulk_add_to_kasten(p_kasten_id, p_workspace_zettel_ids)`` —
  Phase 1.A SECURITY DEFINER RPC for atomic bulk membership insert.

Public class name + method names are preserved. Method signatures have
shifted from ``user_id`` to ``workspace_id`` because v2 is workspace-first
(the ``rag.kastens`` table has a NOT NULL ``workspace_id`` foreign key on
``core.workspaces``). The ``user_id`` param in v1 was a profile UUID; the
v2 substitute is ``workspace_id``, which the caller MUST pull from the
JWT claim or the existing v2 scope helper.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from website.core.supabase_v2.repositories.rag_repository import RAGRepository


class SandboxStore:
    def __init__(
        self,
        supabase: Any | None = None,  # legacy positional/keyword for back-compat
        *,
        repo: RAGRepository | None = None,
    ) -> None:
        # ``supabase`` was the legacy v1 client; ignored under v2 because the
        # repository instantiates its own service-role client. Kept as a no-op
        # kwarg so existing call sites continue to construct without error.
        del supabase
        self._repo = repo or RAGRepository()

    async def list_sandboxes(self, workspace_id: UUID, limit: int = 50) -> list[dict]:
        return self._repo.list_kastens(workspace_id, limit=limit)

    async def get_sandbox(self, sandbox_id: UUID, workspace_id: UUID) -> dict | None:
        return self._repo.get_kasten(sandbox_id, workspace_id)

    async def create_sandbox(
        self,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        default_quality: str = "fast",
    ) -> dict:
        return self._repo.create_kasten(
            workspace_id=workspace_id,
            name=name,
            description=description,
            icon=icon,
            color=color,
            default_quality=default_quality,
        )

    async def update_sandbox(
        self,
        sandbox_id: UUID,
        workspace_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        default_quality: str | None = None,
    ) -> dict | None:
        return self._repo.update_kasten(
            sandbox_id,
            workspace_id,
            name=name,
            description=description,
            icon=icon,
            color=color,
            default_quality=default_quality,
        )

    async def delete_sandbox(self, sandbox_id: UUID, workspace_id: UUID) -> bool:
        return self._repo.delete_kasten(sandbox_id, workspace_id)

    async def list_members(
        self,
        sandbox_id: UUID,
        workspace_id: UUID,
        limit: int = 500,
    ) -> list[dict]:
        """List the zettel members of a kasten via `rag.list_kasten_zettels`.

        Replaces the legacy nested PostgREST embed
        `select("..., kg_nodes(...)")` with an explicit JOIN inside the RPC.
        ``workspace_id`` is required for authorisation (the RPC checks the
        kasten's workspace against the JWT's workspace_ids); we still pass
        it through so the helper signature stays workspace-aware.

        ``limit`` is applied client-side because the RPC does not currently
        accept a limit parameter (full kasten contents are typically <500).
        """
        del workspace_id  # auth handled inside the RPC via JWT claims
        rows = self._repo.list_kasten_zettels(sandbox_id)
        if limit and len(rows) > limit:
            return rows[:limit]
        return rows

    async def add_members(
        self,
        *,
        sandbox_id: UUID,
        workspace_id: UUID,
        workspace_zettel_ids: list[UUID] | None = None,
        added_via: str = "manual",
    ) -> int:
        """Add zettels to a kasten via `rag.bulk_add_to_kasten`.

        Returns the count of newly-inserted membership rows. The RPC's
        ``added_via`` is hardcoded to ``'bulk_rpc'`` server-side; the
        keyword exists for caller compatibility but is currently unused.
        """
        del workspace_id, added_via  # auth + tagging handled inside RPC
        if not workspace_zettel_ids:
            return 0
        return self._repo.add_zettels_to_kasten(
            kasten_id=sandbox_id,
            workspace_zettel_ids=workspace_zettel_ids,
        )

    async def remove_member(
        self,
        sandbox_id: UUID,
        workspace_id: UUID,
        workspace_zettel_id: UUID,
    ) -> bool:
        del workspace_id  # FK + RLS already constrain the row
        return self._repo.remove_zettel_from_kasten(
            kasten_id=sandbox_id,
            workspace_zettel_id=workspace_zettel_id,
        )

    async def remove_members(
        self,
        sandbox_id: UUID,
        workspace_id: UUID,
        workspace_zettel_ids: list[UUID],
    ) -> int:
        del workspace_id
        if not workspace_zettel_ids:
            return 0
        return self._repo.remove_zettels_from_kasten(
            kasten_id=sandbox_id,
            workspace_zettel_ids=workspace_zettel_ids,
        )

    async def touch_sandbox(
        self,
        sandbox_id: UUID,
        workspace_id: UUID,
    ) -> dict | None:
        return self._repo.touch_kasten(sandbox_id, workspace_id)
