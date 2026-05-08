"""Production entrypoint.

In production, dispatches to ``gunicorn`` with two ``UvicornWorker``s and the
``--preload`` flag so the heavy BGE int8 ONNX session in
``website.features.rag_pipeline.rerank.cascade`` is loaded once in the master
process and inherited by workers via copy-on-write -- a ~110 MB RAM saving on
a 2 GB droplet.

Set ``ENV=dev`` to fall back to bare uvicorn for local debugging.

Usage: ``python run.py``
"""

from __future__ import annotations

import os
import subprocess
import sys


def _is_dev() -> bool:
    return os.environ.get("ENV", "").strip().lower() == "dev"


def main() -> int:
    if _is_dev():
        from website.main import main as uvicorn_main

        uvicorn_main()
        return 0

    cmd = [
        "gunicorn",
        "-k", "uvicorn.workers.UvicornWorker",
        "-w", os.environ.get("GUNICORN_WORKERS", "2"),
        "--preload",
        "--bind", f"0.0.0.0:{os.environ.get('PORT', '10000')}",
        # iter-10 doc reconciliation: production droplet sets GUNICORN_TIMEOUT=240
        # in /opt/zettelkasten/compose/.env (>=180s per CLAUDE.md guardrail). The
        # "90" default below is for un-configured dev; prod always overrides.
        "--timeout", os.environ.get("GUNICORN_TIMEOUT", "90"),
        "--graceful-timeout", os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "60"),
        "--keep-alive", os.environ.get("GUNICORN_KEEPALIVE", "5"),
        # iter-05: recycle every ~100±25 requests. iter-03 §2.7 set 100/20;
        # workflow override briefly hard-pinned 5/2 (debug); iter-05 mem-fixes
        # (clear_frames + aggressive_release) attack drift at source so the
        # 5-request belt-and-braces is no longer needed. 25% jitter de-correlates
        # the two workers' recycle clocks.
        "--max-requests", os.environ.get("GUNICORN_MAX_REQUESTS", "100"),
        "--max-requests-jitter", os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "25"),
        # iter-04: cap OS accept-queue. Default gunicorn backlog is 2048
        # which lets the kernel accept 2048 SYNs into a 240 s death-trail
        # under burst load. With 2 workers x (2 sem + 8 queue) = 20 in-
        # flight + 4x headroom = 64. Beyond that we'd rather fail-fast at
        # the listen() boundary so Caddy can hand back 503.
        "--backlog", os.environ.get("GUNICORN_BACKLOG", "64"),
        "website.main:app",
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
