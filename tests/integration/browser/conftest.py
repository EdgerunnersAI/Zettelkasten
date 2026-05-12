"""Browser-test fixtures for WAVE-D Phase 1.

Bridges the Supabase test-user mint pattern (``tests/v2/fixtures/users``) to
the Playwright ``browser_context`` lifecycle. The auth fixture pattern is
adapted from the D-5 research recommendation
(``docs/research/2026-05-12-playwright-ci.md``, §3): pre-seed the Supabase
session into localStorage via ``context.add_init_script`` BEFORE any page
script runs, so the Supabase JS client picks up the session on first
construction.

Three viewports (mobile=375, tablet=768, desktop=1280) cover the responsive
matrix. Workers stay at 1 for visual tests — the Chrome-for-Testing memory
regression in Playwright 1.57+ is single-instance-safe but multiplies under
parallel workers (microsoft/playwright#38489).
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Iterator

import pytest


# Three responsive widths the WAVE-D visual matrix must cover. Heights are
# common pairs (no need to vary heights — viewport width is the regression
# axis). device_scale_factor=1 prevents retina drift on the Linux runner.
PLAYWRIGHT_VIEWPORTS = {
    "mobile":  {"width":  375, "height":  812},
    "tablet":  {"width":  768, "height": 1024},
    "desktop": {"width": 1280, "height":  900},
}


@pytest.fixture(scope="session")
def playwright_devices() -> dict[str, dict[str, int]]:
    """Expose the 3-width viewport map to tests.

    Yields a dict ``{"mobile": {...}, "tablet": {...}, "desktop": {...}}``
    so test bodies can iterate the matrix without hard-coding numbers.
    """
    return dict(PLAYWRIGHT_VIEWPORTS)


@pytest.fixture
def browser_context_args(browser_context_args):
    """Override pytest-playwright's default context args.

    Applies the visual-regression flake-mitigation defaults from the D-5
    research doc: 1280×900 viewport baseline, device_scale_factor=1,
    Accept-Language pin for deterministic locale-driven content.
    """
    return {
        **browser_context_args,
        "viewport": PLAYWRIGHT_VIEWPORTS["desktop"],
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone_id": "UTC",
    }


@pytest.fixture
def authed_browser(browser, request):
    """Playwright BrowserContext pre-seeded with a fresh Supabase session.

    Mints a transient Supabase user via ``mint_test_user_with_workspaces``,
    extracts the access_token + refresh_token, and injects them into
    localStorage under the canonical ``sb-<project-ref>-auth-token`` key
    BEFORE any page script runs (via ``context.add_init_script``).

    Teardown deletes the auth user. If the test is marked ``@pytest.mark.live``
    AND ``--live`` was not passed, the fixture is never invoked (the
    autouse ``skip_live`` fixture at root short-circuits first).

    Returns a tuple ``(context, user)`` so tests can access both the
    Playwright API and the underlying user metadata::

        @pytest.mark.e2e
        def test_home(authed_browser):
            ctx, user = authed_browser
            page = ctx.new_page()
            page.goto(f"{base_url}/home")
            assert page.locator("header").is_visible()
    """
    from tests.v2.fixtures.users import (
        delete_test_user,
        mint_test_user_with_workspaces,
    )

    user = mint_test_user_with_workspaces(workspace_count=1)

    supabase_url = os.environ.get("SUPABASE_V2_URL") or os.environ.get(
        "TEST_SUPABASE_URL", ""
    )
    # Project ref is the subdomain of the Supabase URL —
    # https://<project-ref>.supabase.co. localStorage key shape is fixed by
    # the supabase-js client and must match exactly for session pickup.
    project_ref = (
        supabase_url.split("//", 1)[1].split(".", 1)[0] if supabase_url else "stub"
    )
    storage_key = f"sb-{project_ref}-auth-token"
    storage_value = json.dumps(
        {
            "access_token": user.jwt,
            "refresh_token": "",  # signup mint doesn't expose refresh; tests
            # exercising token refresh should mint via the live path explicitly
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {
                "id": str(user.auth_user_id),
                "email": user.email,
            },
        }
    )

    ctx = browser.new_context(
        viewport=PLAYWRIGHT_VIEWPORTS["desktop"],
        device_scale_factor=1,
        locale="en-US",
        timezone_id="UTC",
    )
    # add_init_script runs in every frame BEFORE any page script — required so
    # the Supabase client picks up the session on first construction.
    ctx.add_init_script(
        f"window.localStorage.setItem({storage_key!r}, {storage_value!r});"
    )

    try:
        yield ctx, user
    finally:
        try:
            ctx.close()
        finally:
            try:
                delete_test_user(user.auth_user_id)
            except Exception:  # noqa: BLE001 — best-effort, session-finish
                # backstop in tests/integration/v2/conftest.py sweeps any
                # ``e2e-*@test.com`` users this misses.
                pass


@pytest.fixture(scope="session")
def base_url() -> str:
    """URL of the app under test.

    Defaults to ``http://127.0.0.1:8000`` (the dev-mode default in
    ``run.py``). CI can override with ``E2E_BASE_URL`` to point at a
    transient ``docker compose`` instance or a staging droplet.
    """
    return os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
