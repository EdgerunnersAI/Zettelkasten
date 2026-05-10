"""API routes for the web summarizer."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from website.api.auth import get_current_user, get_optional_user
from website.core.db_version import get_db_schema_version, use_supabase_v2
from website.core.pipeline import summarize_url
from website.features.summarization_engine.core.errors import ExtractionConfidenceError
from website.core.graph_store import _SOURCE_PREFIX, get_graph
from website.core.graph_models import KGGraph
from website.core.persist import (
    extract_summary_parts,
    get_supabase_v2_scope,
    get_supabase_v2_scope_for_read,
    persist_summarized_result,
)
from website.core.supabase_v2.repositories.kg_repository import KGRepository as V2KGRepository
from website.features.user_pricing.entitlements import consume_entitlement, require_entitlement
from website.features.user_pricing.models import Meter

logger = logging.getLogger("website.api")

router = APIRouter(prefix="/api")

# Simple in-memory rate limiter: {ip: [timestamps]}
_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 10  # requests per minute
_RATE_WINDOW = 60  # seconds

# In-memory graph cache (30-second TTL)
_graph_cache: dict | None = None
_graph_cache_ts: float = 0
_GRAPH_CACHE_TTL = 30  # seconds

def _enrich_graph_with_analytics(graph_dict: dict) -> dict:
    """Add PageRank, community, and centrality metrics to graph nodes.

    Also normalizes every node's ``summary`` into the canonical JSON envelope
    so the frontend never has to defend against mixed historical shapes.
    """
    from website.core.summary_normalizer import normalize_graph_nodes
    normalize_graph_nodes(graph_dict)
    try:
        from website.features.kg_features.analytics import compute_graph_metrics
        kg_graph = KGGraph(**graph_dict)
        metrics = compute_graph_metrics(kg_graph)

        for node in graph_dict.get("nodes", []):
            nid = node["id"]
            node["pagerank"] = metrics.pagerank.get(nid, 0)
            node["community"] = metrics.communities.get(nid, 0)
            node["betweenness"] = metrics.betweenness.get(nid, 0)
            node["closeness"] = metrics.closeness.get(nid, 0)

        graph_dict["meta"] = {
            "communities": metrics.num_communities,
            "components": metrics.num_components,
            "computed_at": metrics.computed_at,
        }
    except Exception as exc:
        logger.warning("Graph analytics enrichment failed: %s", exc)
    return graph_dict


class SummarizeRequest(BaseModel):
    url: str
    client_action_id: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL is required")
        if len(v) > 2048:
            raise ValueError("URL too long (max 2048 characters)")
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class AvatarUpdateRequest(BaseModel):
    avatar_id: int

    @field_validator("avatar_id")
    @classmethod
    def validate_avatar_id(cls, v: int) -> int:
        if not (0 <= v <= 59):
            raise ValueError("avatar_id must be between 0 and 59")
        return v


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed."""
    now = time.time()
    # Prune old timestamps
    _rate_store[ip] = [t for t in _rate_store[ip] if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_LIMIT:
        return False
    _rate_store[ip].append(now)
    return True


@router.get("/health")
async def health(request: Request):
    payload: dict = {"status": "ok"}
    monitor = getattr(request.app.state, "event_loop_monitor", None)
    if monitor is not None:
        payload["event_loop_lag"] = monitor.snapshot()

    # iter-12 T31 R4: bandit pathology metrics (5 ops-dashboard fields).
    # All collected from in-process telemetry; never expose model/score internals.
    bandit_state = getattr(request.app.state, "bandit_telemetry_snapshot", None)
    if bandit_state is not None:
        payload["bandit"] = {
            # Stuck-arm detection: argmax(α/(α+β)) switches over rolling 24h.
            # Alert if >3 after 50 pulls.
            "posterior_mode_flips_24h": bandit_state.get("posterior_mode_flips_24h"),
            # Near-uniform posterior = no learning. Alert if >1.3 after 200 pulls.
            "posterior_entropy_nats": bandit_state.get("posterior_entropy_nats"),
            # Starvation flag. Alert if <0.05 after 100 total pulls.
            "arm_pull_ratio_min_max": bandit_state.get("arm_pull_ratio_min_max"),
            # Sampling overhead. Alert if >5ms.
            "bandit_decision_latency_p99_ms": bandit_state.get("bandit_decision_latency_p99_ms"),
            # Concurrent-write health. Alert if >5%.
            "db_upsert_conflict_rate": bandit_state.get("db_upsert_conflict_rate"),
        }
    return payload


@router.get("/health/warm")
async def warm():
    """Pre-warm endpoint: triggers reranker first inference + tokenizer load.

    Called by ``ops/deploy/deploy.sh`` after the new color comes up so the
    first user request doesn't pay the BGE cold-start tax (~1-3s on a 1 vCPU
    droplet). Returns 200 with a small JSON payload regardless of whether the
    int8 model is present -- in the no-model case ``rerank_ms`` is 0 and the
    body still carries ``warmed=True`` so the deploy script's healthcheck
    succeeds.
    """
    import time as _time

    rerank_ms = 0.0
    detail = "ok"
    try:
        from website.features.rag_pipeline.rerank import cascade as cascade_mod
        from website.features.rag_pipeline.rerank.cascade import CascadeReranker

        if cascade_mod._STAGE2_SESSION is not None:
            cr = CascadeReranker()
            t0 = _time.perf_counter()
            cr.score_batch(
                "warmup query",
                [{"id": "w", "text": "warmup chunk"}],
                mode="fast",
            )
            rerank_ms = round((_time.perf_counter() - t0) * 1000, 1)
        else:
            detail = "int8_model_absent"
    except Exception as exc:  # pragma: no cover - logged for ops
        logger.warning("warm endpoint encountered %r", exc)
        detail = f"warmup_failed: {type(exc).__name__}"

    return {"warmed": True, "rerank_ms": rerank_ms, "detail": detail}


@router.get("/auth/config")
async def auth_config():
    """Return public Supabase config for client-side auth init."""
    if get_db_schema_version() == "v2":
        return {
            "supabase_url": os.environ.get("SUPABASE_V2_URL", ""),
            "supabase_anon_key": os.environ.get("SUPABASE_V2_ANON_KEY", ""),
        }
    return {
        "supabase_url": os.environ.get("SUPABASE_URL", ""),
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
    }


@router.get("/me")
async def me(user: Annotated[dict, Depends(get_current_user)]):
    """Return the authenticated user's profile.

    v2-only: when the JWT subject is a UUID with a valid v2 scope, read profile
    fields from ``core.profiles`` via :class:`CoreRepository`. On any miss
    (no v2 scope, lookup failure, v2 not configured) fall back to the JWT
    metadata claims so the wire shape ``{id, email, name, avatar_url}`` is
    stable. Phase 8.0.4: v1 ``kg_users`` fallback removed (table dropped in
    Phase 6).
    """
    metadata = user.get("user_metadata", {})
    avatar_url = metadata.get("avatar_url", "")

    # v2 path: read profile from core.profiles via CoreRepository.
    if use_supabase_v2():
        scope = get_supabase_v2_scope_for_read(user["sub"])
        if scope is not None:
            from uuid import UUID
            from website.core.supabase_v2.client import get_v2_client
            from website.core.supabase_v2.repositories.core_repository import CoreRepository

            _content_repo, profile_id, _workspace_ids = scope
            try:
                profile = CoreRepository(get_v2_client()).get_profile(profile_id)
            except Exception as exc:  # noqa: BLE001 — graceful fallback on v2 hiccup
                logger.warning("v2 /api/me profile lookup failed for %s: %s", profile_id, exc)
                profile = None

            if profile:
                return {
                    "id": user["sub"],
                    "email": profile.get("email") or user.get("email", "") or "",
                    "name": profile.get("display_name") or metadata.get("full_name", "") or "",
                    "avatar_url": profile.get("avatar_url") or avatar_url or "",
                }

    # Phase 8.0.3 B+: v1 ``kg_users``-backed avatar fallback removed —
    # ``public.kg_users`` was dropped in Phase 6, the get_supabase_scope
    # helper retired, and the live PUT /api/me/avatar handler writes to
    # ``core.profiles.avatar_url`` (covered by the v2 branch above).
    return {
        "id": user["sub"],
        "email": user.get("email", ""),
        "name": metadata.get("full_name", ""),
        "avatar_url": avatar_url,
    }


@router.put("/me/avatar")
async def update_avatar(
    body: AvatarUpdateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Update the authenticated user's avatar.

    Phase 8.5.R3 v2 port: writes to ``core.profiles.avatar_url`` via the
    authenticated profile id (resolved from JWT ``sub``). The product surface
    is a preset-picker (avatar_id ∈ [0, 59]) mapping to pre-built SVG assets
    under ``/artifacts/avatars/``. No file upload, no Pillow re-encode — the
    R-B research's full upload pipeline is overkill for this product shape.

    v1 fallback retired: pre-v2, this called ``KGRepository.update_user_avatar``
    against ``public.kg_users``. That table was dropped in Phase 6.
    """
    from uuid import UUID
    avatar_url = f"/artifacts/avatars/avatar_{body.avatar_id:02d}.svg"

    if not _is_supabase_uuid(user.get("sub")):
        raise HTTPException(status_code=400, detail="v2 avatar update requires UUID auth subject")

    scope = get_supabase_v2_scope(user["sub"])
    if scope is None:
        raise HTTPException(status_code=404, detail="No v2 profile scope")
    _content_repo, profile_id, _workspace_id = scope

    from website.core.supabase_v2.repositories.core_repository import CoreRepository
    core_repo = CoreRepository()
    updated = core_repo.update_avatar(profile_id, avatar_url)
    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found")

    return {"avatar_url": avatar_url}


# Separate caches for global vs per-user views
_graph_cache_global: dict | None = None
_graph_cache_global_ts: float = 0


def _v2_assemble_graph(
    *,
    user_sub: str,
    limit: int,
    offset: int,
) -> KGGraph | None:
    """Assemble a v2 :class:`KGGraph` for the user across their workspaces.

    Returns ``None`` when the user lacks a v2 scope (not configured, non-UUID
    sub, or no workspace memberships). Soft-deleted overlays are filtered by
    the repository. Edges are joined back through the workspace overlay rows
    so the resulting source/target IDs match the node IDs we emit.
    """
    scope = get_supabase_v2_scope_for_read(user_sub)
    if scope is None:
        return None
    content_repo, _profile_id, workspace_ids = scope
    kg_repo = V2KGRepository()

    nodes: list[dict] = []
    overlay_index: dict[int, dict] = {}  # placeholder; v2 KG tables key by bigint
    canonical_to_overlay: dict[str, str] = {}  # canonical_zettel_id -> frontend node id

    for ws_id in workspace_ids:
        rows = content_repo.list_workspace_zettels(ws_id, limit=limit, offset=offset)
        for row in rows:
            canonical = row.get("canonical") or {}
            canonical_id = str(canonical.get("id") or row.get("canonical_zettel_id") or "")
            if not canonical_id or canonical_id in canonical_to_overlay:
                continue
            source_type = str(canonical.get("source_type") or "web").lower()
            prefix = _SOURCE_PREFIX.get(source_type, "web")
            slug = re.sub(
                r"[^a-z0-9]+", "-", str(canonical.get("title") or "").lower()
            ).strip("-")[:24].rstrip("-") or "untitled"
            node_id = f"{prefix}-{slug}-{canonical_id[:8]}"
            canonical_to_overlay[canonical_id] = node_id

            brief, _detailed = extract_summary_parts(row.get("ai_summary"), None)
            pub_date = canonical.get("publication_date") or ""
            nodes.append(
                {
                    "id": node_id,
                    "name": str(canonical.get("title") or "Untitled"),
                    "group": source_type,
                    "summary": row.get("ai_summary") or "",
                    "tags": list(row.get("user_tags") or []),
                    "url": str(canonical.get("normalized_url") or ""),
                    "date": str(pub_date),
                    "node_date": str(pub_date),
                }
            )

    links: list[dict] = []
    for ws_id in workspace_ids:
        edge_rows = kg_repo.list_workspace_edges(ws_id)
        for edge in edge_rows:
            evidence = edge.get("evidence_canonical_zettel_id")
            # The v2 kg_edges table keys by bigint kg_nodes; without a mention
            # join we cannot resolve src/dst -> overlay node ids generally. We
            # only emit edges whose evidence canonical maps to a known overlay
            # node — sufficient for the dual-path 4.1 surface and forward-
            # compatible with richer mention joins in later phases.
            if not evidence:
                continue
            target_node = canonical_to_overlay.get(str(evidence))
            if not target_node:
                continue
            links.append(
                {
                    "source": target_node,
                    "target": target_node,
                    "relation": str(edge.get("relation_type") or "shared_tag"),
                    "weight": None,
                    "link_type": "tag",
                    "description": edge.get("shared_tag_label"),
                }
            )

    # Use Pydantic to enforce the shape; total_nodes mirrors v1 conventions.
    try:
        return KGGraph(nodes=nodes, links=links, total_nodes=len(nodes))
    except Exception as exc:
        logger.warning("v2 graph assembly produced invalid KGGraph: %s", exc)
        return KGGraph(nodes=[], links=[], total_nodes=0)


@router.get("/graph")
async def graph_data(
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
    view: str | None = None,
    limit: int = 5000,
    offset: int = 0,
):
    """Return the knowledge graph.

    - Default (no view param, or unauthenticated): global graph
    - ?view=my: authenticated user's personal graph
    - ?view=global: explicit global graph (all users combined)
    - ?limit=N&offset=M: pagination (default 5000 nodes, offset 0)
    """
    global _graph_cache, _graph_cache_ts, _graph_cache_global, _graph_cache_global_ts

    limit = max(1, min(limit, 10000))
    offset = max(0, offset)
    now = time.time()

    # v2 path: when DB v2 is live AND the caller is a UUID-subject user with
    # workspace memberships, assemble the graph from content/kg v2 tables.
    # On any miss (not configured, no scope, assembly failure) we fall through
    # to the file-store global graph (canonical public/anonymous surface).
    if use_supabase_v2() and user is not None:
        try:
            v2_graph = _v2_assemble_graph(
                user_sub=user["sub"], limit=limit, offset=offset,
            )
            if v2_graph is not None:
                return _enrich_graph_with_analytics(v2_graph.model_dump())
        except Exception as exc:
            logger.warning("v2 /api/graph assembly failed, serving file-store: %s", exc)

    # Phase 8.0.4: v1 ``KGRepository.get_graph`` fallback removed (Phase 6
    # dropped ``public.kg_nodes``). Anonymous and v2-miss callers both serve
    # the file-store graph — the canonical public/anonymous surface.
    # Only use cache for default pagination (first page, standard limit).
    use_cache = offset == 0 and limit >= 5000
    if use_cache and _graph_cache_global is not None and (now - _graph_cache_global_ts) < _GRAPH_CACHE_TTL:
        return _graph_cache_global

    result = _enrich_graph_with_analytics(get_graph())
    if use_cache:
        _graph_cache_global = result
        _graph_cache_global_ts = now
    return result


# Phase 8.5.R3 / Phase 8 Task 4d: /api/graph/rebuild-links — HARD DELETED.
# Admin endpoint with no external callers; production link maintenance is
# event-driven (Supabase triggers, pg_cron), not REST-triggered. FastAPI's
# default 404 handles unknown URLs. If ever needed again, ship as
# ops/scripts/rebuild_links.py — one-shot ops script, never an HTTP route.
# Industry pattern: Sitecore Content Hub graph-rebuild-tracking, Neo4j LLM
# Knowledge Graph Builder, Microsoft GraphRAG — all event-driven.


def _is_supabase_uuid(value: str | None) -> bool:
    """Return True when ``value`` parses as a canonical UUID.

    Used by the v2 dual-path branches to gate v2 routing on a UUID-shaped
    auth subject / path parameter. v2 IDs are UUIDs (workspace_zettel_id);
    v1 node_ids are slug-prefixed strings (``yt-...``, ``web-...``) and
    intentionally fail this check so they fall through to the v1 path.
    """
    if not value:
        return False
    try:
        from uuid import UUID

        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


@router.delete("/zettels/{node_id}")
async def delete_zettel(
    node_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a zettel from the authenticated user's graph.

    v2-only: requires DB v2 + UUID auth subject + UUID-shaped path parameter
    (treated as ``workspace_zettel_id``). Soft-delete flows via
    :class:`ContentRepository` so the reaper trigger handles canonical shred
    at last reference. Hard delete is intentionally NEVER performed in this
    handler (see audit fix A.3). Phase 8.0.4: v1 ``KGRepository.delete_node``
    AND the file-store fallback both removed — ``public.kg_nodes`` was
    dropped in Phase 6 and the file-store graph is the public/anonymous
    surface, not a user-owned write target. Non-UUID path params get 400.
    """
    global _graph_cache, _graph_cache_ts, _graph_cache_global, _graph_cache_global_ts
    from uuid import UUID

    if not (use_supabase_v2() and _is_supabase_uuid(user.get("sub")) and _is_supabase_uuid(node_id)):
        raise HTTPException(status_code=400, detail="Zettel delete requires v2 UUID path")

    scope = get_supabase_v2_scope(user["sub"])
    if scope is None:
        raise HTTPException(status_code=404, detail="No v2 workspace scope")
    content_repo, _profile_id, _workspace_id = scope

    try:
        # Phase 8.5.R3 SECURITY FIX: pass workspace_id so the repo's
        # compound-key match gates B-from-A cross-tenant deletion.
        ok = content_repo.soft_delete_workspace_zettel(
            UUID(node_id), workspace_id=_workspace_id,
        )
    except Exception as exc:
        logger.warning("v2 soft-delete failed for %s: %s", node_id, exc)
        ok = False
    if not ok:
        raise HTTPException(status_code=404, detail="Zettel not found")

    _graph_cache = None
    _graph_cache_ts = 0
    _graph_cache_global = None
    _graph_cache_global_ts = 0
    return {"status": "ok", "workspace_zettel_id": node_id}


class ZettelUpdateRequest(BaseModel):
    """User-editable fields on a workspace overlay (v2 only).

    ``user_tags``, ``user_note``, and ``pinned`` are user-owned. ``ai_summary``
    is engine-owned; if a client sends ``ai_summary`` (legacy frontend), the
    text is rerouted to ``user_note`` so it lands in a user-editable surface
    instead of clobbering the AI-generated summary.
    """

    user_tags: list[str] | None = None
    user_note: str | None = None
    pinned: bool | None = None
    ai_summary: str | None = None  # rerouted to user_note in handler


@router.patch("/zettels/{node_id}")
async def update_zettel(
    node_id: str,
    body: ZettelUpdateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Update user-editable fields on a workspace zettel overlay (v2 path).

    Phase 4.3 dual-path: requires DB v2 + UUID auth subject + UUID path param.
    The v1 path has no PATCH endpoint — for non-v2 callers this returns 404.
    ``ai_summary`` in the payload is intentionally redirected into
    ``user_note`` (engine-owned vs user-owned separation).
    """
    global _graph_cache, _graph_cache_ts, _graph_cache_global, _graph_cache_global_ts
    from uuid import UUID

    if not (
        use_supabase_v2()
        and _is_supabase_uuid(user.get("sub"))
        and _is_supabase_uuid(node_id)
    ):
        raise HTTPException(status_code=404, detail="Zettel update requires v2 path")

    scope = get_supabase_v2_scope(user["sub"])
    if scope is None:
        raise HTTPException(status_code=404, detail="No v2 workspace scope")
    content_repo, _profile_id, _workspace_id = scope

    # ai_summary -> user_note redirect (engine-owned vs user-owned).
    user_note = body.user_note
    if body.ai_summary is not None and user_note is None:
        user_note = body.ai_summary

    try:
        # Phase 8.5.R3 SECURITY FIX: workspace_id gates compound-key match so
        # B's PATCH against A's zettel by id no longer succeeds.
        ok = content_repo.update_workspace_zettel(
            UUID(node_id),
            workspace_id=_workspace_id,
            user_tags=body.user_tags,
            user_note=user_note,
            pinned=body.pinned,
        )
    except Exception as exc:
        logger.warning("v2 update_workspace_zettel failed for %s: %s", node_id, exc)
        raise HTTPException(status_code=500, detail="Update failed") from exc

    if not ok:
        raise HTTPException(status_code=404, detail="Zettel not found")

    _graph_cache = None
    _graph_cache_ts = 0
    _graph_cache_global = None
    _graph_cache_global_ts = 0
    return {"status": "ok", "workspace_zettel_id": node_id}


class GraphQueryRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question is required")
        if len(v) > 500:
            raise ValueError("Question too long (max 500 characters)")
        return v


class GraphSearchRequest(BaseModel):
    query: str
    seed_node_id: str | None = None
    limit: int = 20

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query is required")
        return v


# Rate limit bucket for NL queries (separate from summarize)
_query_rate_store: dict[str, list[float]] = defaultdict(list)
_QUERY_RATE_LIMIT = 5  # per minute


def _check_query_rate_limit(ip: str) -> bool:
    now = time.time()
    _query_rate_store[ip] = [t for t in _query_rate_store[ip] if now - t < _RATE_WINDOW]
    if len(_query_rate_store[ip]) >= _QUERY_RATE_LIMIT:
        return False
    _query_rate_store[ip].append(now)
    return True


@router.post("/graph/query")
async def graph_query(
    body: GraphQueryRequest,
    request: Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    """RETIRED: NL→SQL surface. 410 Gone per Phase 8.5.C-defer.

    The NL→SQL prompt vocabulary in `website.features.kg_features.nl_query`
    references the v1 schema (`public.kg_users / kg_nodes / kg_links`) — every
    table dropped in Phase 6 commit e168b38. Any successful prompt completion
    would fail at psql execution against missing tables.

    Re-enable when the prompt is ported to the v2 schema (content.canonical_*,
    kg.kg_* with proper RLS guardrails). Tracked in:
      * docs/superpowers/plans/2026-05-10-phase-8.5-hardening-additions.md (8.5.C-defer)
      * memory/project_kg_intelligence_remaining.md

    Returns 410 with RFC 8594 Sunset header + IETF Deprecation draft-09 header
    so clients can distinguish "intentionally retired" from "404 not found".
    """
    return JSONResponse(
        status_code=410,
        content={
            "error": "gone",
            "message": (
                "/api/graph/query NL→SQL surface is retired pending v2 schema "
                "port. Use /api/graph for the structured KG, or /api/rag/adhoc "
                "for free-form questions over your Kasten content."
            ),
            "v2_endpoint": None,
            "docs": "docs/db-v2/cutover-runbook.md",
        },
        headers={
            "Sunset": "Sat, 10 May 2026 00:00:00 GMT",
            "Deprecation": "@1715299200",
        },
    )


@router.post("/graph/search")
async def graph_search(
    body: GraphSearchRequest,
    request: Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    """RETIRED: 410 Gone per Phase 8.5.R3 / Phase 8 Task 4c.

    Graph search is either a frontend filter over the already-loaded /api/graph
    payload (Obsidian/Roam/Logseq pattern) or subsumed by RAG retrieval
    (/api/rag/adhoc — Tana/Mem.ai/Microsoft GraphRAG pattern). No v2 successor
    today; if a real product surface ever needs it, ship as a scope filter on
    the existing RAG endpoint, not by un-deprecating this v1 route.

    Industry pattern (2026): Notion/Zalando/Sentry deprecation conventions —
    410 with RFC 8594 Sunset + IETF Deprecation header so clients can
    distinguish intentional retirement from 404 not-found.
    """
    return JSONResponse(
        status_code=410,
        content={
            "error": "gone",
            "message": (
                "/api/graph/search is retired. Use /api/rag/adhoc for query-"
                "driven retrieval over your Kasten content, or filter the "
                "/api/graph payload client-side."
            ),
            "v2_endpoint": None,
            "docs": "docs/db-v2/cutover-runbook.md",
        },
        headers={
            "Sunset": "Sat, 10 May 2026 00:00:00 GMT",
            "Deprecation": "@1715299200",
        },
    )


@router.post("/summarize")
async def summarize(body: SummarizeRequest, request: Request, user: Annotated[dict | None, Depends(get_optional_user)] = None):
    global _graph_cache_global, _graph_cache_global_ts

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait a minute before trying again.",
        )

    logger.info("Summarize request from %s: %s", ip, body.url)

    try:
        action_id = body.client_action_id or body.url
        await require_entitlement(Meter.ZETTEL, user, action_id=action_id)
        result = await summarize_url(body.url)
        persistence = await persist_summarized_result(
            result,
            user_sub=user["sub"] if user else None,
        )
        await consume_entitlement(Meter.ZETTEL, user, action_id=action_id)
        if persistence.supabase_saved:
            _graph_cache_global = None
            _graph_cache_global_ts = 0
        return persistence.result
    except HTTPException:
        raise
    except ExtractionConfidenceError as exc:
        logger.warning("Extraction too thin for %s: %s", body.url, exc)
        is_youtube = (exc.source_type or "").lower() == "youtube"
        if is_youtube:
            message = (
                "YouTube transcript unavailable for this video. All extraction "
                "tiers (transcript API, Piped, Invidious, Gemini audio, oEmbed, "
                "metadata-only) failed — usually due to datacenter IP "
                "restrictions on the host, a private or age-restricted video, "
                "or YouTube blocking the regional fetcher. Try a different "
                "URL, or paste the transcript content directly."
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "message": message,
                    "tier_results": exc.tier_results,
                    "url": exc.url or body.url,
                },
            )
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not extract enough content from this URL to produce "
                "a reliable summary. This often happens with YouTube videos "
                "when transcript access is restricted. Please try a different URL."
            ),
        )
    except Exception as exc:
        logger.error("Summarization failed for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process URL: {exc}",
        )
