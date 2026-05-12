"""UH-01 — signed-in smoke + anonymous redirect for /home.

Two browser tests:

  * ``test_anonymous_redirects_to_landing``: opens /home with NO Supabase
    session in localStorage. ``home.js:108`` reads the session via
    ``_supabaseClient.auth.getSession()``; when no token is present it sets
    ``window.location.href = '/'``. The server-side route at
    ``website/app.py:361-364`` does NOT redirect anonymous users — it
    always renders the page; the redirect is client-side, which is why
    this test must drive a real browser, not a plain HTTP client.

  * ``test_signed_in_loads_home_clean``: opens /home with a minted Supabase
    session pre-seeded into localStorage via the ``authed_browser``
    fixture; asserts the home-vault landmark renders.

Both gated by ``@pytest.mark.live`` — they need a running app and a
reachable Supabase project. CI workflow ``e2e.yml`` runs ``-m "not live"``
by default; pass ``--live`` locally or against staging to execute.
"""
from __future__ import annotations

import pytest


@pytest.mark.e2e
@pytest.mark.live
def test_anonymous_redirects_to_landing(browser, base_url):
    """Anonymous /home visit → client-side redirect to / (UH-01)."""
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        device_scale_factor=1,
        locale="en-US",
        timezone_id="UTC",
    )
    try:
        page = ctx.new_page()
        page.goto(f"{base_url}/home", wait_until="domcontentloaded")
        # home.js sets sessionStorage 'zk-home-redirect' just before
        # window.location.href = '/'. Wait for navigation to settle.
        page.wait_for_url(f"{base_url}/", timeout=10_000)
        assert page.url.rstrip("/") == base_url.rstrip("/"), (
            f"expected redirect to landing '/', got {page.url!r}"
        )
    finally:
        ctx.close()


@pytest.mark.e2e
@pytest.mark.live
def test_signed_in_loads_home_clean(authed_browser, base_url):
    """Signed-in /home renders the vault panel and no console errors (UH-01)."""
    ctx, _user = authed_browser
    page = ctx.new_page()

    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text)
        if msg.type == "error"
        else None,
    )

    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    # Structural anchor for the authed view.
    vault = page.locator("#home-vault")
    assert vault.count() >= 1, "expected #home-vault on /home for signed-in user"

    # D-2 namespace fix: the avatar IDs MUST be home-namespaced. If a future
    # commit re-introduces the raw IDs the duplicate-DOM bug returns.
    assert page.locator("#home-avatar-btn").count() == 1
    assert page.locator("#home-avatar-dropdown").count() == 1
    assert page.locator("#home-menu-signout").count() == 1
    # And the un-namespaced IDs must NOT appear on /home (they live in
    # header.html which /home does not inject).
    assert page.locator("#avatar-btn").count() == 0
    assert page.locator("#avatar-dropdown").count() == 0
    assert page.locator("#menu-signout").count() == 0

    assert not console_errors, (
        f"console.error chatter on signed-in /home: {console_errors!r}"
    )
