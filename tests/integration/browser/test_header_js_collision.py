"""HD-05 — JS-collision guard for shared header IDs.

Phase 0 flagged the duplicate-DOM-ID hazard: ``header.html`` declares
``#avatar-btn`` / ``#avatar-dropdown`` / ``#menu-signout``; on shells that
ALSO inject ``home/index.html``'s legacy inline copy of those IDs, the page
DOM ends up with duplicates and ``getElementById`` silently binds to the
first one. The user_home D-2 rename namespaces the home copies to
``#home-avatar-btn`` / ``#home-avatar-dropdown`` / ``#home-menu-signout``.

This test asserts the invariant holds on every shell'd route now AND will
fail loudly if a future regression reintroduces the duplicate IDs:

  (a) For each shell'd route, ``document.querySelectorAll("#avatar-btn")``
      returns exactly 1 element on routes that inject the shared header,
      and the matching element is the one owned by ``header.js`` (lives
      inside ``[data-zk-header]``).
  (b) Clicking that button toggles ``#avatar-dropdown`` open AND sets
      ``aria-expanded`` to "true" (cross-references D-4 surgical fix).
  (c) Clicking it again sets ``aria-expanded`` back to "false".

Marked ``@pytest.mark.e2e + @pytest.mark.live`` — needs a Chromium driver.
"""
from __future__ import annotations

import pytest


# Routes confirmed to inject <!--ZK_HEADER--> (grep 2026-05-12):
SHELL_INJECTED_ROUTES = [
    "/knowledge-graph",
    "/home/nexus",
    "/home/zettels",
    "/home/kastens",
    "/home/rag",
    "/pricing",
]


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.parametrize("path", SHELL_INJECTED_ROUTES)
def test_no_duplicate_avatar_btn_id(authed_browser, base_url, path):
    """Exactly one #avatar-btn per page — the one owned by header.js."""
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    count = page.evaluate(
        "document.querySelectorAll('#avatar-btn').length"
    )
    assert count == 1, (
        f"{path}: expected exactly 1 #avatar-btn, got {count} "
        f"(collision hazard — see HD-05 in docs/research/full_modular_test_plans/header.md)"
    )

    # Also assert the surviving instance is the one inside the shared
    # header (data-zk-header container). If the home copy somehow won
    # the ID, this would fail.
    owned_by_header = page.evaluate(
        "(() => {"
        "  const el = document.getElementById('avatar-btn');"
        "  return !!el && !!el.closest('[data-zk-header]');"
        "})()"
    )
    assert owned_by_header, (
        f"{path}: #avatar-btn is NOT inside [data-zk-header] — wrong element bound"
    )


@pytest.mark.e2e
@pytest.mark.live
def test_avatar_dropdown_toggles_aria_expanded(authed_browser, base_url):
    """D-4 cross-reference: aria-expanded mirrors the .open state.

    Uses /home/zettels (a representative shell'd inner page) — same dropdown
    behaviour as every other shell'd route.
    """
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.goto(f"{base_url}/home/zettels", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    btn = page.locator("#avatar-btn")
    drop = page.locator("#avatar-dropdown")

    # Initial state: collapsed.
    assert btn.get_attribute("aria-expanded") == "false"
    assert "open" not in (drop.get_attribute("class") or "")

    # Click → expanded.
    btn.click()
    page.wait_for_function(
        "document.getElementById('avatar-btn')"
        "  .getAttribute('aria-expanded') === 'true'"
    )
    assert "open" in (drop.get_attribute("class") or "")

    # Click again → collapsed.
    btn.click()
    page.wait_for_function(
        "document.getElementById('avatar-btn')"
        "  .getAttribute('aria-expanded') === 'false'"
    )
    assert "open" not in (drop.get_attribute("class") or "")


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.parametrize("legacy_id", ["avatar-btn", "avatar-dropdown", "menu-signout"])
def test_legacy_ids_not_duplicated_on_home(authed_browser, base_url, legacy_id):
    """/home shipped inline copies of these IDs historically (D-2 renamed them).

    After D-2, /home must NOT host the legacy ID anymore — it lives only on
    pages that inject the shared header, and even there it's exactly once.
    """
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    count = page.evaluate(
        f"document.querySelectorAll('#{legacy_id}').length"
    )
    # /home does not inject ZK_HEADER (inline header only), so count must
    # be 0 after the D-2 rename. If a future commit adds the placeholder
    # AND re-introduces the inline copy without re-namespacing, count
    # would go to 2 and this test would catch the regression.
    assert count == 0, (
        f"/home contains {count} elements with legacy id #{legacy_id} — "
        f"D-2 rename regressed"
    )
