"""Soft RSS-guard middleware. iter-03 mem-bounded §2.9.

Reads /proc/self/status before dispatching every request. When VmRSS exceeds
``RAG_MEMORY_GUARD_THRESHOLD_PERCENT`` of the cgroup memory limit, returns
503 with Retry-After=5 instead of letting the kernel cgroup-OOM the worker
mid-request (which would surface as a 502 from Caddy).

Path exemptions: /api/health, /api/admin/*, /favicon.*.
These probes/ops paths must always work, even under pressure.

Set RAG_MEMORY_GUARD_THRESHOLD_PERCENT=0 to disable entirely (tests/dev).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("website.api._memory_guard")

_CGROUP_V2_MEM_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEM_MAX = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_STATUS = Path("/proc/self/status")

_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/admin/",
    "/favicon.ico",
    "/favicon.svg",
)

_DEFAULT_THRESHOLD_PERCENT = 90


def _detect_mem_max() -> int:
    """Return the cgroup memory limit in bytes, falling back to MemTotal."""
    for p in (_CGROUP_V2_MEM_MAX, _CGROUP_V1_MEM_MAX):
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except (OSError, FileNotFoundError):
            continue
        if raw == "max":
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    try:
        for line in _PROC_MEMINFO.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, FileNotFoundError):
        pass
    return 0


def _read_vm_rss_bytes() -> int:
    try:
        for line in _PROC_STATUS.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, FileNotFoundError):
        pass
    return 0


def _threshold_percent() -> int:
    raw = os.environ.get("RAG_MEMORY_GUARD_THRESHOLD_PERCENT")
    if raw is None:
        return _DEFAULT_THRESHOLD_PERCENT
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_THRESHOLD_PERCENT


def install(app: FastAPI) -> None:
    """Register the middleware on ``app``."""

    @app.middleware("http")
    async def _memory_guard_middleware(request: Request, call_next):
        threshold = _threshold_percent()
        if threshold <= 0:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)
        mem_max = _detect_mem_max()
        if mem_max <= 0:
            # Cannot determine bound — guard self-disables to avoid blocking prod.
            return await call_next(request)
        rss = _read_vm_rss_bytes()
        if rss <= 0:
            return await call_next(request)
        if rss * 100 >= mem_max * threshold:
            logger.warning(
                "memory pressure shedding: rss=%d mem_max=%d threshold_pct=%d path=%s",
                rss, mem_max, threshold, path,
            )
            return JSONResponse(
                {"error": "server_under_memory_pressure", "retry_after_seconds": 5},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        return await call_next(request)
