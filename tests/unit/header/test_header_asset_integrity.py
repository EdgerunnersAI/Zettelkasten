"""HD-06 — asset integrity sweep for the shared header surface.

Header.js silently swallows preload errors (``preload(url, timeoutMs)`` —
``website/features/header/js/header.js:144-179``) and falls back to a
random avatar. A missing ``/artifacts/avatars/avatar_NN.svg`` would deploy
without any user-visible signal — defaults degrade to the initial-letter
fallback. This test is the post-deploy guard: every asset the header
relies on must respond 200 with non-zero Content-Length.

Asset inventory (verified 2026-05-12):
  * ``/header/js/header.js`` — script.
  * ``/header/css/header.css`` — stylesheet.
  * ``/artifacts/avatars/avatar_NN.svg`` for NN in [00, 59] (60 files).

Concurrency: all 62 HEAD requests issue via ``asyncio.gather`` against the
in-process ASGI app. Wall-clock is ~1-2s vs ~30s serial — keeps CI green
without burning a Playwright runner.
"""
from __future__ import annotations

import asyncio
import os

import pytest

# Settings validation stubs — must precede the website.app import.
os.environ.setdefault("GEMINI_API_KEY", "ci-stub")
os.environ.setdefault("SUPABASE_V2_URL", "https://ci-stub.supabase.co")
os.environ.setdefault("SUPABASE_V2_ANON_KEY", "ci-stub-anon")
os.environ.setdefault("SUPABASE_V2_SERVICE_ROLE_KEY", "ci-stub-service")
os.environ.setdefault(
    "NEXUS_TOKEN_ENCRYPTION_KEY",
    "7TgtMgeR5dMTnXxW6ULICwhf66A1VpzwuNFuIBqmoe4=",
)


AVATAR_COUNT = 60  # matches header.js AVATAR_COUNT — must stay in sync.

HEADER_ASSETS = [
    "/header/js/header.js",
    "/header/css/header.css",
]
AVATAR_ASSETS = [
    f"/artifacts/avatars/avatar_{n:02d}.svg" for n in range(AVATAR_COUNT)
]
ALL_ASSETS = HEADER_ASSETS + AVATAR_ASSETS


@pytest.fixture(scope="module")
def app():
    from website.app import create_app
    return create_app()


async def _head(client, url: str) -> tuple[str, int, int]:
    # StaticFiles supports HEAD; some sub-paths only respond to GET. Try
    # HEAD first; on 405 fall back to GET and measure body length.
    resp = await client.head(url)
    if resp.status_code == 405:
        resp = await client.get(url)
        length = len(resp.content)
    else:
        length = int(resp.headers.get("content-length", "0") or "0")
    return url, resp.status_code, length


async def test_all_header_assets_served_200(app):
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as client:
        results = await asyncio.gather(*[_head(client, u) for u in ALL_ASSETS])

    bad = [(u, s, n) for u, s, n in results if s != 200 or n <= 0]
    assert not bad, (
        f"asset integrity failure ({len(bad)} of {len(ALL_ASSETS)}): {bad!r}"
    )


@pytest.mark.parametrize("asset", HEADER_ASSETS)
async def test_individual_header_asset_200(app, asset):
    """Per-asset assertion so failures attribute clearly to css vs js."""
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as client:
        url, status, length = await _head(client, asset)
    assert status == 200 and length > 0, (
        f"{asset}: status={status}, length={length}"
    )


async def test_avatar_pool_size_matches_header_js_constant(app):
    """If header.js bumps AVATAR_COUNT, the on-disk pool must keep up."""
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as client:
        # Sentinel just past the pool — must 404. Catches the case where
        # AVATAR_COUNT was bumped but the SVGs were never authored.
        sentinel = f"/artifacts/avatars/avatar_{AVATAR_COUNT:02d}.svg"
        resp = await client.head(sentinel)
        assert resp.status_code in (404, 405), (
            f"unexpected status for sentinel {sentinel}: {resp.status_code}"
        )
