"""Runtime factory for the user-level RAG product surfaces."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from website.features.rag_pipeline.adapters.pool_factory import get_embedding_pool
from website.features.rag_pipeline.context.assembler import ContextAssembler
from website.features.rag_pipeline.critic.answer_critic import AnswerCritic
from website.features.rag_pipeline.generation.claude_backend import ClaudeBackend
from website.features.rag_pipeline.generation.gemini_backend import GeminiBackend
from website.features.rag_pipeline.generation.llm_router import LLMRouter
from website.features.rag_pipeline.ingest.embedder import ChunkEmbedder
from website.features.rag_pipeline.memory import ChatSessionStore, SandboxStore
from website.features.rag_pipeline.orchestrator import RAGOrchestrator
from website.features.api_key_switching import get_key_pool
from website.features.rag_pipeline.query.metadata import QueryMetadataExtractor
from website.features.rag_pipeline.query.rewriter import QueryRewriter
from website.features.rag_pipeline.query.router import QueryRouter
from website.features.rag_pipeline.query.transformer import QueryTransformer
from website.features.rag_pipeline.rerank.cascade import CascadeReranker
from website.features.rag_pipeline.retrieval.graph_score import LocalizedPageRankScorer
from website.features.rag_pipeline.retrieval.hybrid import HybridRetriever
from website.features.rag_pipeline.retrieval.planner import RetrievalPlanner
from website.features.rag_pipeline.scoring.runtime import get_registry_adapter
from website.core.supabase_v2.client import get_v2_client

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_QUERIES = (
    _PROJECT_ROOT
    / "website"
    / "features"
    / "user_rag"
    / "content"
    / "example_queries.json"
)


# 8.0-H7: ``_KGModuleAdapter`` retired alongside ``website.features.kg_features.retrieval``.
# The v1 ``hybrid_search`` / ``expand_subgraph`` calls hit ``hybrid_kg_search`` and
# ``kg_expand_subgraph`` RPCs against the dropped v1 ``public.kg_nodes`` / ``kg_links``
# tables (Phase 6 commit e168b38) and silently returned [] in prod. v2 entity-anchor
# expansion now happens directly inside ``HybridRetriever`` via ``entity_anchor.py``
# (`kg.expand_subgraph(p_workspace_id, p_node_ids bigint[], p_depth int)`), so the
# ``RetrievalPlanner`` no longer needs a kg_module shim — see planner.py header.


@dataclass(slots=True)
class RAGRuntime:
    repo: object
    kg_user_id: UUID
    sessions: ChatSessionStore
    sandboxes: SandboxStore
    orchestrator: RAGOrchestrator


@lru_cache(maxsize=16)
def _build_runtime(user_sub: str | None) -> RAGRuntime:
    # Phase 8.0.3 B+ atomic swap: v1 get_supabase_scope retired. Every
    # consumer below accepts ``supabase=None`` and lazily defaults to
    # ``get_v2_client()`` internally. We hard-fail on a non-UUID auth
    # subject because v2 is profile-UUID-keyed end to end.
    if not user_sub:
        raise RuntimeError("Supabase-backed RAG requires an authenticated user_sub")
    try:
        kg_user_id = UUID(str(user_sub))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Supabase-backed RAG requires a UUID auth subject; got {user_sub!r}"
        ) from exc

    client = get_v2_client()
    sessions = ChatSessionStore(supabase=None)
    sandboxes = SandboxStore(supabase=None)
    embedder = ChunkEmbedder(pool=get_embedding_pool())
    # 8.0-H7: planner no longer needs a kg_module — v2 entity-anchor expansion
    # is performed by ``HybridRetriever`` via ``entity_anchor.py``. The planner
    # is a pass-through preserved for orchestrator wiring symmetry.
    planner = RetrievalPlanner(kg_module=None)
    orchestrator = RAGOrchestrator(
        rewriter=QueryRewriter(),
        router=QueryRouter(),
        transformer=QueryTransformer(),
        retriever=HybridRetriever(
            embedder=embedder,
            supabase=None,
            registry_adapter=get_registry_adapter(),
        ),
        graph_scorer=LocalizedPageRankScorer(supabase=None),
        reranker=CascadeReranker(
            model_dir=os.environ.get("RAG_MODEL_DIR", "/app/models"),
            stage1_k=int(os.environ.get("RAG_CASCADE_STAGE1_K", "10")),
        ),
        assembler=ContextAssembler(),
        llm=LLMRouter(gemini=GeminiBackend(), claude=ClaudeBackend()),
        critic=AnswerCritic(),
        sessions=sessions,
        metadata_extractor=QueryMetadataExtractor(key_pool=get_key_pool()),
        planner=planner,
    )
    # ``repo`` is preserved on the runtime for back-compat with callers that
    # treat it as an opaque handle; the v2 client is the operational object.
    return RAGRuntime(
        repo=client,
        kg_user_id=kg_user_id,
        sessions=sessions,
        sandboxes=sandboxes,
        orchestrator=orchestrator,
    )


def get_rag_runtime(user_sub: str | None) -> RAGRuntime:
    return _build_runtime(user_sub)


@lru_cache(maxsize=1)
def load_example_queries() -> list[str]:
    if not _EXAMPLE_QUERIES.exists():
        return []
    try:
        payload = json.loads(_EXAMPLE_QUERIES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item.strip() for item in payload if isinstance(item, str) and item.strip()]

