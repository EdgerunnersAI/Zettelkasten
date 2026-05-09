"""API routes for the web summarizer."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from website.api.auth import get_current_user, get_optional_user
from website.core.db_version import get_db_schema_version
from website.core.pipeline import summarize_url
from website.features.summarization_engine.core.errors import ExtractionConfidenceError
from website.core.graph_store import get_graph, delete_node as delete_graph_node
from website.core.graph_models import KGGraph
from website.core.persist import (
    get_supabase_scope as _get_supabase,
    persist_summarized_result,
)
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
    """Return the authenticated user's profile."""
    metadata = user.get("user_metadata", {})
    avatar_url = metadata.get("avatar_url", "")

    # Prefer avatar from kg_users table (set via PUT /api/me/avatar)
    sb = _get_supabase(user_id_override=user["sub"])
    if sb:
        repo, _ = sb
        kg_user = repo.get_user_by_render_id(user["sub"])
        if kg_user and kg_user.avatar_url:
            avatar_url = kg_user.avatar_url

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
    """Update the authenticated user's avatar."""
    avatar_url = f"/artifacts/avatars/avatar_{body.avatar_id:02d}.svg"

    sb = _get_supabase(user_id_override=user["sub"])
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    repo, _ = sb
    # Ensure user exists in kg_users before updating avatar
    metadata = user.get("user_metadata", {})
    repo.get_or_create_user(
        render_user_id=user["sub"],
        display_name=metadata.get("full_name"),
        email=user.get("email"),
    )
    updated = repo.update_user_avatar(user["sub"], avatar_url)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return {"avatar_url": avatar_url}


# Separate caches for global vs per-user views
_graph_cache_global: dict | None = None
_graph_cache_global_ts: float = 0


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
    is_personal = view == "my" and user is not None

    if is_personal:
        # Per-user graph — always fresh, no global cache
        sb = _get_supabase(user_id_override=user["sub"])
        if sb:
            repo, user_id = sb
            try:
                from uuid import UUID
                graph = repo.get_graph(UUID(user_id), limit=limit, offset=offset)
                return _enrich_graph_with_analytics(graph.model_dump())
            except Exception as exc:
                logger.warning("Supabase user graph fetch failed, falling back: %s", exc)
        return _enrich_graph_with_analytics(get_graph())

    # Global graph (default for all users including anonymous)
    # Only use cache for default pagination (first page, standard limit)
    use_cache = offset == 0 and limit >= 5000
    if use_cache and _graph_cache_global is not None and (now - _graph_cache_global_ts) < _GRAPH_CACHE_TTL:
        return _graph_cache_global

    sb = _get_supabase()
    if sb:
        repo, _ = sb
        try:
            graph = repo.get_graph(user_id=None, limit=limit, offset=offset)
            result = _enrich_graph_with_analytics(graph.model_dump())
            if use_cache:
                _graph_cache_global = result
                _graph_cache_global_ts = now
            return result
        except Exception as exc:
            logger.warning("Supabase global graph fetch failed, falling back: %s", exc)

    result = _enrich_graph_with_analytics(get_graph())
    if use_cache:
        _graph_cache_global = result
        _graph_cache_global_ts = now
    return result


@router.post("/graph/rebuild-links")
async def rebuild_links(user: Annotated[dict | None, Depends(get_optional_user)] = None):
    """Rebuild all tag-based links for a user (or default user).

    Deletes existing links and re-creates them from shared tags.
    """
    global _graph_cache_global, _graph_cache_global_ts

    sb = _get_supabase(user_id_override=user["sub"] if user else None)
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    repo, user_id = sb
    try:
        from uuid import UUID
        count = repo.rebuild_links(UUID(user_id))
        _graph_cache_global = None
        _graph_cache_global_ts = 0
        return {"status": "ok", "links_created": count, "user_id": user_id}
    except Exception as exc:
        logger.error("Rebuild links failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to rebuild links: {exc}")


@router.delete("/zettels/{node_id}")
async def delete_zettel(
    node_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a zettel from the authenticated user's graph."""
    global _graph_cache, _graph_cache_ts, _graph_cache_global, _graph_cache_global_ts

    deleted = False
    sb = _get_supabase(user_id_override=user["sub"])
    if sb:
        repo, user_id = sb
        try:
            from uuid import UUID

            deleted = repo.delete_node(UUID(user_id), node_id)
        except Exception as exc:
            logger.warning("Supabase node delete failed, falling back to file store: %s", exc)

    # Fallback for non-supabase mode
    if not deleted:
        deleted = delete_graph_node(node_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Zettel not found")

    _graph_cache = None
    _graph_cache_ts = 0
    _graph_cache_global = None
    _graph_cache_global_ts = 0

    return {"status": "ok", "node_id": node_id}


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
    """Natural-language query against the knowledge graph (M4)."""
    ip = request.client.host if request.client else "unknown"
    if not _check_query_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many queries. Wait a minute.")

    sb = _get_supabase(user_id_override=user["sub"] if user else None)
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    repo, user_id = sb
    try:
        from uuid import UUID
        from website.features.kg_features.nl_query import NLGraphQuery, NLQueryError

        query_engine = NLGraphQuery(repo._client, user_id=user_id)
        result = await query_engine.ask(body.question, UUID(user_id))
        return result.model_dump()
    except Exception as exc:
        if hasattr(exc, "status_code"):
            raise HTTPException(status_code=exc.status_code, detail=exc.user_message)
        logger.error("Graph query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Query failed. Try rephrasing.")


@router.post("/graph/search")
async def graph_search(
    body: GraphSearchRequest,
    request: Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    """Hybrid search across the knowledge graph (M6)."""
    ip = request.client.host if request.client else "unknown"
    if not _check_query_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many queries. Wait a minute.")

    sb = _get_supabase(user_id_override=user["sub"] if user else None)
    if not sb:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    repo, user_id = sb
    try:
        from website.features.kg_features.retrieval import hybrid_search

        results = hybrid_search(
            supabase_client=repo._client,
            user_id=user_id,
            query=body.query,
            seed_node_id=body.seed_node_id,
            limit=body.limit,
        )
        return {"results": [r.model_dump() for r in results]}
    except Exception as exc:
        logger.error("Graph search failed: %s", exc)
        raise HTTPException(status_code=500, detail="Search failed.")


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
