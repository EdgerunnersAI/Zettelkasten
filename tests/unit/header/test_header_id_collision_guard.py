"""HD-05 (static counterpart) — regression guard for the D-2 namespace fix.

The browser-driven HD-05 lives in ``tests/integration/browser/`` and runs
only with ``--live``. This static counterpart parses the rendered HTML for
every shell'd route via ASGI transport and asserts the shared-header IDs
(``avatar-btn`` / ``avatar-dropdown`` / ``menu-signout`` /
``avatar-wrap`` / ``avatar-img`` / ``avatar-fallback``) appear at most
once per page, AND that the home-only ``home-*`` IDs do NOT leak into
any non-``/home`` route — catching duplicate-ID regressions and reverse
namespace leakage without a browser.
"""
from __future__ import annotations

import os
import re

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

# All 9 desktop routes — collision must be zero on every page regardless of
# whether the route injects the shared header or ships an inline copy.
ALL_ROUTES = [
    "/", "/knowledge-graph", "/home", "/home/nexus",
    "/home/zettels", "/home/kastens", "/home/rag", "/about", "/pricing",
]

# IDs declared in the shared header fragment. Any page that injects the
# fragment AND re-declares one of these inline would create a duplicate.
SHARED_HEADER_IDS = [
    "avatar-btn",
    "avatar-dropdown",
    "menu-signout",
    "avatar-wrap",
    "avatar-img",
    "avatar-fallback",
]

# Home-only IDs introduced by the D-2 namespace fix. These must NEVER appear
# on any route other than /home — a regression here would mean the home
# template (or one of its IDs) leaked into the shared header fragment.
HOME_ONLY_IDS = [
    "home-avatar-btn",
    "home-avatar-dropdown",
    "home-menu-signout",
    "home-avatar-wrap",
    "home-avatar-img",
    "home-avatar-fallback",
]
NON_HOME_ROUTES = [r for r in ALL_ROUTES if r != "/home"]


@pytest.fixture(scope="module")
def app():
    from website.app import create_app
    return create_app()


def _client(app):
    import httpx
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    )


@pytest.mark.parametrize("path", ALL_ROUTES)
@pytest.mark.parametrize("element_id", SHARED_HEADER_IDS)
async def test_shared_id_appears_at_most_once(app, path, element_id):
    """Every rendered page must contain ``id="<element_id>"`` at most once."""
    async with _client(app) as client:
        resp = await client.get(path, headers={"User-Agent": DESKTOP_UA})
    assert resp.status_code == 200, f"{path}: non-200 {resp.status_code}"

    # Count exact-match id=" + element_id + " occurrences (anchored on the
    # closing quote so a longer id like 'avatar-btn-2' wouldn't double-count).
    pattern = re.compile(rf'\bid="{re.escape(element_id)}"')
    hits = pattern.findall(resp.text)
    assert len(hits) <= 1, (
        f"{path}: id=\"{element_id}\" appears {len(hits)} times — "
        f"duplicate-ID collision (HD-05 regression)"
    )


@pytest.mark.parametrize("path", NON_HOME_ROUTES)
@pytest.mark.parametrize("element_id", HOME_ONLY_IDS)
async def test_home_only_ids_absent_from_other_routes(app, path, element_id):
    """The home-* namespace IDs must NOT appear on any non-/home route."""
    async with _client(app) as client:
        resp = await client.get(path, headers={"User-Agent": DESKTOP_UA})
    assert resp.status_code == 200, f"{path}: non-200 {resp.status_code}"

    pattern = re.compile(rf'\bid="{re.escape(element_id)}"')
    hits = pattern.findall(resp.text)
    assert len(hits) == 0, (
        f"{path}: id=\"{element_id}\" appeared {len(hits)} times — "
        f"home-only ID leaked into a non-/home route (D-2 regression)"
    )
