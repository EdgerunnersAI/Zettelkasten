"""M2 — Semantic Embeddings via Gemini embedding model.

Generates vector embeddings for KG node content and provides
similarity-based linking and search helpers using cosine distance.

Key rotation is handled by the centralized GeminiKeyPool.
"""

from __future__ import annotations

import logging

import numpy as np

from website.features.api_key_switching import get_key_pool

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

_EMBEDDING_DIMS = 768

# Pinned to the schema-default `embedding_model_version` on
# `content.canonical_chunks` (see `supabase/website/_v2/02_content_schema.sql`).
# Any change here MUST land alongside a new row in
# `content.embedding_model_versions` and a backfill of existing chunks; the
# `kg.match_kg_nodes` RPC filters on this value to prevent cross-version
# cosine collisions.
_CURRENT_EMBEDDING_MODEL_VERSION = "gemini-001-mrl-768"


def _normalize_embedding(raw: list[float]) -> list[float]:
    """Return an L2-normalized embedding vector as a plain Python list."""
    if len(raw) != _EMBEDDING_DIMS:
        logger.warning("Embedding returned %d dims, expected %d", len(raw), _EMBEDDING_DIMS)
    vec = np.array(raw, dtype=np.float64)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


# ── Single embedding ────────────────────────────────────────────────────────

def generate_embedding(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[float]:
    """Generate an L2-normalised embedding vector for *text*.

    Returns an empty list on any failure (rate-limit, network, etc.).
    The key pool handles key rotation on 429 errors automatically.
    """
    if not text or not text.strip():
        return []

    try:
        pool = get_key_pool()
        response = pool.embed_content_safe(
            text,
            config={"task_type": task_type, "output_dimensionality": _EMBEDDING_DIMS},
        )

        if response is None:
            return []

        return _normalize_embedding(response.embeddings[0].values)

    except Exception as exc:
        logger.error("Embedding generation failed: %s", exc)
        return []


# ── Batch embeddings ────────────────────────────────────────────────────────

def generate_embeddings_batch(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Generate L2-normalised embeddings for a list of texts.

    Returns a list the same length as *texts*; failed items are [].
    """
    if not texts:
        return []

    try:
        pool = get_key_pool()
        response = pool.embed_content_safe(
            texts,
            config={"task_type": task_type, "output_dimensionality": _EMBEDDING_DIMS},
        )

        if response is None:
            return [[] for _ in texts]

        results: list[list[float]] = []
        for emb in response.embeddings:
            results.append(_normalize_embedding(emb.values))
        return results

    except Exception as exc:
        logger.error("Batch embedding failed: %s", exc)
        return [[] for _ in texts]


# ── Similarity helpers ──────────────────────────────────────────────────────

def should_create_semantic_link(similarity: float, threshold: float = 0.75) -> bool:
    """Return True if *similarity* is strictly above *threshold*."""
    return similarity > threshold


def find_similar_nodes(
    supabase_client,
    user_id: str,
    embedding: list[float],
    threshold: float = 0.75,
    limit: int = 10,
    model_version: str = _CURRENT_EMBEDDING_MODEL_VERSION,
) -> list[dict]:
    """Find nodes similar to *embedding* via the ``kg.match_kg_nodes`` RPC.

    Calls the v2 ``kg.match_kg_nodes`` Postgres function which performs a
    halfvec(768) cosine-distance search joined to ``kg.kg_nodes`` via
    ``kg.chunk_node_mentions``. The RPC filters by ``embedding_model_version``
    so cross-version cosine collisions are dropped at the storage layer.

    Returns a list of dicts each with ``node_id`` (bigint) and ``score``
    (float in [0, 1]). Returns an empty list on any failure (schema drift,
    network, RPC error) — the caller is the KG-link recommender, which must
    degrade gracefully rather than fail the ingest pipeline.
    """
    if not embedding:
        return []

    try:
        response = supabase_client.schema("kg").rpc(
            "match_kg_nodes",
            {
                "p_user_id": user_id,
                "p_query_embedding": embedding,
                "p_model_version": model_version,
                "p_match_threshold": threshold,
                "p_match_count": limit,
            },
        ).execute()
        return response.data or []
    except Exception as exc:
        logger.warning("find_similar_nodes RPC failed: %s", exc)
        return []
