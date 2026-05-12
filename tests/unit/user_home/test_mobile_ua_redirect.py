"""UH-04 — mobile UA → /m/ redirect.

The desktop ``/home`` route at ``website/app.py:361-364`` checks
``_is_mobile(request)`` (regex ``_MOBILE_RE`` at app.py:75-78) and serves a
302 RedirectResponse to ``/m/`` for any UA the regex matches.

These tests parameterize on representative Pixel + iPhone UAs to lock the
contract against regex regressions. We hit the route in-process via
httpx ASGITransport so no live server is required.
"""
from __future__ import annotations

import os
import sys

import pytest

# Stub env BEFORE importing the FastAPI app — settings validation would
# otherwise SystemExit on missing keys.
os.environ.setdefault("GEMINI_API_KEY", "ci-stub")
os.environ.setdefault("SUPABASE_V2_URL", "https://ci-stub.supabase.co")
os.environ.setdefault("SUPABASE_V2_ANON_KEY", "ci-stub-anon")
os.environ.setdefault("SUPABASE_V2_SERVICE_ROLE_KEY", "ci-stub-service")
os.environ.setdefault(
    "NEXUS_TOKEN_ENCRYPTION_KEY",
    "7TgtMgeR5dMTnXxW6ULICwhf66A1VpzwuNFuIBqmoe4=",
)


# Sample UAs sourced from the canonical strings the _MOBILE_RE regex
# targets. Desktop UA paired as a negative control.
PIXEL_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module.

    We avoid the live brotli middleware path by letting it auto-fall-back
    if absent. Settings validation has been satisfied by the env stubs at
    module import time above.
    """
    from website.app import create_app

    return create_app()


def _async_client(app):
    # Lazy import so collection-time failures don't mask the more
    # informative settings-validation error at app build.
    import httpx

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.parametrize("ua", [PIXEL_UA, IPHONE_UA], ids=["pixel", "iphone"])
async def test_mobile_ua_redirects_home_to_m(app, ua):
    """Mobile UA on /home returns 302 → /m/ (UH-04)."""
    async with _async_client(app) as client:
        resp = await client.get("/home", headers={"User-Agent": ua}, follow_redirects=False)
    assert resp.status_code == 302, (
        f"expected 302 for mobile UA, got {resp.status_code}"
    )
    assert resp.headers["location"] == "/m/", (
        f"mobile redirect target drifted: {resp.headers.get('location')!r}"
    )


async def test_desktop_ua_renders_home_inline(app):
    """Desktop UA on /home returns 200 HTML (negative control)."""
    async with _async_client(app) as client:
        resp = await client.get(
            "/home", headers={"User-Agent": DESKTOP_UA}, follow_redirects=False,
        )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    # Sanity: confirm we are looking at the home page, not landing.
    assert "home-vault" in resp.text or "Welcome back" in resp.text
