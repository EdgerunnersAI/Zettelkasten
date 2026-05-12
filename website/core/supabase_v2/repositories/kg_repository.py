"""Repository for DB v2 knowledge-graph tables."""

from __future__ import annotations

from uuid import UUID

from supabase import Client

from website.core.supabase_v2.client import get_v2_client


class KGRepository:
    def __init__(self, client: Client | None = None) -> None:
        self._client = client or get_v2_client()

    def upsert_node(
        self,
        *,
        workspace_id: UUID | None,
        node_type: str,
        canonical_name: str,
        slug: str,
        metadata: dict | None = None,
    ) -> int:
        payload = {
            "workspace_id": str(workspace_id) if workspace_id else None,
            "type": node_type,
            "canonical_name": canonical_name,
            "slug": slug,
            "metadata": metadata or {},
        }
        response = (
            self._client.schema("kg")
            .table("kg_nodes")
            .upsert(payload, on_conflict="workspace_key,slug")
            .execute()
        )
        row = _first(response.data)
        return int(row["id"])

    def add_edge(
        self,
        *,
        workspace_id: UUID | None,
        src_node_id: int,
        dst_node_id: int,
        relation_type: str,
        shared_tag_label: str | None = None,
        weight: float | None = None,
        metadata: dict | None = None,
    ) -> int:
        payload = {
            "workspace_id": str(workspace_id) if workspace_id else None,
            "src_node_id": src_node_id,
            "dst_node_id": dst_node_id,
            "relation_type": relation_type,
            "shared_tag_label": shared_tag_label,
            "weight": weight,
            "metadata": metadata or {},
        }
        response = self._client.schema("kg").table("kg_edges").insert(payload).execute()
        return int(_first(response.data)["id"])

    def list_workspace_edges(
        self,
        workspace_id: UUID,
        *,
        limit: int = 10000,
    ) -> list[dict]:
        """Return raw kg_edges rows for a workspace.

        Shape mirrors columns the v2 ``/api/graph`` path needs to render
        ``KGGraphLink`` rows: src_node_id, dst_node_id, relation_type,
        shared_tag_label, weight, evidence_canonical_zettel_id. Caller is
        responsible for joining src/dst back to the workspace zettels.
        """
        response = (
            self._client.schema("kg")
            .table("kg_edges")
            .select(
                "id,src_node_id,dst_node_id,relation_type,"
                "shared_tag_label,weight,evidence_canonical_zettel_id"
            )
            .eq("workspace_id", str(workspace_id))
            .limit(max(1, limit))
            .execute()
        )
        return list(response.data or [])

    def list_node_zettel_mapping(
        self,
        workspace_id: UUID,
        kg_node_ids: list[int],
        *,
        limit: int = 50000,
    ) -> dict[int, list[str]]:
        """Resolve kg_nodes.id -> set of canonical_zettel_id strings.

        Joins kg.chunk_node_mentions -> content.canonical_chunks to surface
        every canonical_zettel that mentions a given kg_node, scoped to the
        workspace via the kg_node parent. The /api/graph assembler needs this
        to translate edge endpoints (bigint kg_node ids) into overlay node
        ids (which key off canonical_zettel_id).

        Returns {} when ``kg_node_ids`` is empty. The PostgREST embed pulls
        the chunk row in the same round-trip; we deduplicate canonical zettel
        ids per node on the Python side.
        """
        if not kg_node_ids:
            return {}
        # Filter mentions to the requested node ids; embed canonical_chunks
        # so we can read canonical_zettel_id without a second round-trip.
        response = (
            self._client.schema("kg")
            .table("chunk_node_mentions")
            .select(
                "kg_node_id,canonical_chunk_id,"
                "canonical_chunks:canonical_chunk_id(canonical_zettel_id)"
            )
            .in_("kg_node_id", list(kg_node_ids))
            .limit(max(1, limit))
            .execute()
        )
        out: dict[int, list[str]] = {}
        seen: dict[int, set[str]] = {}
        for row in response.data or []:
            try:
                node_id = int(row.get("kg_node_id"))
            except (TypeError, ValueError):
                continue
            chunk = row.get("canonical_chunks") or {}
            zettel_id = chunk.get("canonical_zettel_id") if isinstance(chunk, dict) else None
            if not zettel_id:
                continue
            zettel_str = str(zettel_id)
            bucket = seen.setdefault(node_id, set())
            if zettel_str in bucket:
                continue
            bucket.add(zettel_str)
            out.setdefault(node_id, []).append(zettel_str)
        return out

    def expand_subgraph(self, *, workspace_id: UUID, node_ids: list[int], depth: int = 1) -> list[int]:
        response = self._client.schema("kg").rpc(
            "expand_subgraph",
            {
                "p_workspace_id": str(workspace_id),
                "p_node_ids": node_ids,
                "p_depth": depth,
            },
        ).execute()
        return [int(row["id"]) for row in response.data or []]


def _first(data):
    if not data:
        raise RuntimeError("Supabase returned no rows")
    return data[0] if isinstance(data, list) else data

