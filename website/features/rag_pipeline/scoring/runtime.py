"""Worker-local scorer registry runtime.

Gunicorn runs this from ``post_fork`` so each worker owns its own asyncpg
LISTEN connection. Starting it in FastAPI lifespan would be too late for
``--preload`` safety and too easy to duplicate in test app factories.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from website.core.db_version import use_supabase_v2
from website.features.rag_pipeline.scoring.registry_adapter import RegistryAdapter

logger = logging.getLogger(__name__)

_adapter: RegistryAdapter | None = None
_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None


def get_registry_adapter() -> RegistryAdapter | None:
    return _adapter


def start_registry_adapter_post_fork() -> None:
    """Start the DB v2 registry listener once per worker when v2 is enabled."""
    global _adapter, _thread, _loop

    if not use_supabase_v2():
        return
    if _adapter is not None:
        return

    _adapter = RegistryAdapter()
    _loop = asyncio.new_event_loop()

    def _run() -> None:
        assert _loop is not None
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_adapter.start())
        _loop.run_forever()

    _thread = threading.Thread(target=_run, name="scorer-registry-listener", daemon=True)
    _thread.start()
    logger.info("Started DB v2 scorer registry adapter in worker")

