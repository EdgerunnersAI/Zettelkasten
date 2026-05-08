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
from website.features.kg_features import retrieval as kg_retrieval
from website.core.persist import get_supabase_scope

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_QUERIES = (
    _PROJECT_ROOT
    / "website"
    / "features"
    / "user_rag"
    / "content"
    / "example_queries.json"
)


class _KGModuleAdapter:
    """Bind a Supabase client to ``kg_features.retrieval`` callables.

    The :class:`RetrievalPlanner` invokes ``hybrid_search`` and
    ``expand_subgraph`` with a positional client argument and reads the
    client off the kg_module via ``_supabase``. Module-level functions in
    ``kg_features.retrieval`` don't carry state, so this adapter holds the
    client and exposes the two callables plus the expected ``_supabase``
    attribute. Keeping the shim local to ``service.py`` avoids leaking
    runtime wiring into the kg_features feature surface.
    """

    def __init__(self, *, client) -> None:
        self._supabase = client
        self.hybrid_search = kg_retrieval.hybrid_search
        self.expand_subgraph = kg_retrieval.expand_subgraph


@dataclass(slots=True)
class RAGRuntime:
    repo: object
    kg_user_id: UUID
    sessions: ChatSessionStore
    sandboxes: SandboxStore
    orchestrator: RAGOrchestrator


@lru_cache(maxsize=16)
def _build_runtime(user_sub: str | None) -> RAGRuntime:
    scope = get_supabase_scope(user_sub)
    if scope is None:
        raise RuntimeError("Supabase-backed RAG is not configured")

    repo, kg_user_id = scope
    client = repo._client
    sessions = ChatSessionStore(supabase=client)
    sandboxes = SandboxStore(supabase=client)
    embedder = ChunkEmbedder(pool=get_embedding_pool())
    # T20: bind the Supabase client to the kg_features module functions so the
    # RetrievalPlanner can call hybrid_search/expand_subgraph without each
    # caller threading the client. The adapter is a tiny shim that exposes
    # the same callables plus a ``_supabase`` attribute the planner reads.
    kg_module_adapter = _KGModuleAdapter(client=client)
    planner = RetrievalPlanner(kg_module=kg_module_adapter)
    orchestrator = RAGOrchestrator(
        rewriter=QueryRewriter(),
        router=QueryRouter(),
        transformer=QueryTransformer(),
        retriever=HybridRetriever(
            embedder=embedder,
            supabase=client,
            registry_adapter=get_registry_adapter(),
        ),
        graph_scorer=LocalizedPageRankScorer(supabase=client),
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
    return RAGRuntime(
        repo=repo,
        kg_user_id=UUID(str(kg_user_id)),
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

