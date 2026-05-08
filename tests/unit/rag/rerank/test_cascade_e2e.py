"""Lightweight wiring tests for cascade reranker integration."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from website.features.rag_pipeline.rerank.cascade import CascadeReranker
from website.features.rag_pipeline import service


def test_cascade_reranker_importable_from_package() -> None:
    from website.features.rag_pipeline.rerank import CascadeReranker as Imported

    assert Imported is CascadeReranker


def test_service_runtime_builds_cascade_reranker(monkeypatch) -> None:
    class FakeCascade:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    repo = SimpleNamespace(_client=object())
    user_id = uuid4()

    service._build_runtime.cache_clear()
    monkeypatch.setenv("RAG_MODEL_DIR", "/tmp/models")
    monkeypatch.setenv("RAG_CASCADE_STAGE1_K", "9")
    monkeypatch.setattr(service, "get_supabase_scope", lambda user_sub: (repo, user_id))
    monkeypatch.setattr(service, "ChatSessionStore", lambda supabase: SimpleNamespace(supabase=supabase))
    monkeypatch.setattr(service, "SandboxStore", lambda supabase: SimpleNamespace(supabase=supabase))
    monkeypatch.setattr(service, "get_embedding_pool", lambda: "pool")
    monkeypatch.setattr(service, "ChunkEmbedder", lambda pool: SimpleNamespace(pool=pool))
    monkeypatch.setattr(service, "QueryRewriter", lambda: "rewriter")
    monkeypatch.setattr(service, "QueryRouter", lambda: "router")
    monkeypatch.setattr(service, "QueryTransformer", lambda: "transformer")
    monkeypatch.setattr(service, "get_key_pool", lambda: "key-pool")
    monkeypatch.setattr(service, "HybridRetriever", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(service, "LocalizedPageRankScorer", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(service, "CascadeReranker", FakeCascade)
    monkeypatch.setattr(service, "ContextAssembler", lambda: "assembler")
    monkeypatch.setattr(service, "GeminiBackend", lambda: "gemini")
    monkeypatch.setattr(service, "ClaudeBackend", lambda: "claude")
    monkeypatch.setattr(service, "LLMRouter", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(service, "AnswerCritic", lambda: "critic")
    monkeypatch.setattr(service, "RAGOrchestrator", FakeOrchestrator)

    runtime = service._build_runtime("user-sub")

    reranker = runtime.orchestrator.kwargs["reranker"]
    assert isinstance(reranker, FakeCascade)
    assert reranker.kwargs == {"model_dir": "/tmp/models", "stage1_k": 9}
    assert runtime.kg_user_id == user_id
