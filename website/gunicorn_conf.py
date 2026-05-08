"""Gunicorn hooks for production workers."""

from __future__ import annotations


def post_fork(server, worker) -> None:  # pragma: no cover - exercised by gunicorn
    from website.features.rag_pipeline.scoring.runtime import start_registry_adapter_post_fork

    start_registry_adapter_post_fork()

