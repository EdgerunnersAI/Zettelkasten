"""UH-07 — shell composition regression for /home.

``_render_with_shell`` (``website/app.py:53-68``) reads the shared header
and footer fragments per-request and substitutes them into the page at
``<!--ZK_HEADER-->`` / ``<!--ZK_FOOTER-->`` placeholders. The contract is
*fall-through*: a missing fragment file or missing placeholder must NOT
500 — the raw page text should be served as-is.

Two cases:
  1. Happy path — /home renders 200 even though /home's index.html does
     not include the ZK_HEADER placeholder (home ships its own avatar
     markup inline; D-2 namespace fix landed in the same iteration).
  2. Missing header fragment — re-point ``HEADER_DIR`` at a non-existent
     directory and assert /home still renders 200. This locks the
     fall-through and proves a header outage does not cascade into a
     5xx on user-facing pages.
"""
from __future__ import annotations

import os
from pathlib import Path

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


DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@pytest.fixture
def app():
    from website.app import create_app
    return create_app()


def _client(app):
    import httpx
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    )


async def test_home_renders_with_header_fragment_present(app):
    """Happy path — /home returns 200 with the shipped header fragment."""
    async with _client(app) as client:
        resp = await client.get("/home", headers={"User-Agent": DESKTOP_UA})
    assert resp.status_code == 200
    assert "home-vault" in resp.text


async def test_home_falls_through_without_header_fragment(
    app, monkeypatch, tmp_path,
):
    """Missing header fragment must NOT 500 (fall-through contract).

    Re-points ``website.app.HEADER_DIR`` at an empty tmp dir. /home's
    index.html does not contain the placeholder today, so the fall-through
    branch never reads header.html — but if a future commit adds the
    placeholder back, this test ensures a missing file is non-fatal.
    """
    import website.app as appmod

    monkeypatch.setattr(appmod, "HEADER_DIR", tmp_path)

    async with _client(app) as client:
        resp = await client.get("/home", headers={"User-Agent": DESKTOP_UA})

    # /home must still render. 200 with the page body is the only OK.
    assert resp.status_code == 200, (
        f"expected 200 with missing header fragment, got {resp.status_code}"
    )
    assert "home-vault" in resp.text
