"""iter-12 Class P (PATH_F): async wrapper for sync Supabase RPC calls.

Every supabase.rpc(...).execute() is a blocking HTTP call that stalls the
asyncio event loop for 150-400 ms. Under burst-12 the default executor
(5 threads on 1-vCPU) saturates, starving the SSE heartbeat and triggering
Caddy dial_timeout → Cloudflare 502. This module offloads every RPC to
asyncio.to_thread and caps concurrent in-flight calls via a global semaphore.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

# Global semaphore: caps concurrent in-flight RPCs across ALL callers in
# this worker process. Sized to match RAG_EXECUTOR_MAX_WORKERS so we never
# queue more work than the executor can run. Never raise above 8 — httpx
# pool ceiling and RAM constraints documented in iter-12 RESEARCH.md.
_GLOBAL_RPC_SEM_SIZE = int(os.environ.get("RAG_RPC_GLOBAL_SEMAPHORE", "8"))
_RPC_SEM = asyncio.Semaphore(_GLOBAL_RPC_SEM_SIZE)


async def rpc_call(rpc_obj: Any, *, request_sem: asyncio.Semaphore | None = None) -> Any:
    """Await a sync supabase RPC object in a thread pool executor.

    Args:
        rpc_obj: The object returned by ``supabase.rpc(...)``. Its ``.execute()``
            method is called inside asyncio.to_thread.
        request_sem: Optional per-request semaphore for caller-side fan-out
            bounding (e.g. entity_anchor per-entity gather). Acquired BEFORE
            the global semaphore (outer) so a coroutine never holds a scarce global slot while blocked on its own per-request gate.

    Returns:
        The response object from ``.execute()`` (same as the sync call).

    Raises:
        Any exception raised by ``.execute()`` is propagated to the caller.
    """
    # iter-12 Class P: request_sem outer / _RPC_SEM inner avoids priority inversion (a coroutine holding a scarce global slot while waiting for its own per-request gate).
    if request_sem is not None:
        async with request_sem:
            async with _RPC_SEM:
                return await asyncio.to_thread(rpc_obj.execute)
    async with _RPC_SEM:
        return await asyncio.to_thread(rpc_obj.execute)
