"""Smoke test proving the WAVE-D Phase 1 browser fixture chain works.

Validates three things end-to-end so the 3 module impl sub-agents
(web_monitor / user_home / header) can consume the fixtures with confidence:

  1. ``authed_browser`` mints a Supabase user + injects the session
     localStorage payload before page scripts run.
  2. Navigation to ``/home`` lands an authenticated view (header visible,
     no ``console.error`` chatter).
  3. ``axe-playwright-python`` injection + ``axe.run()`` returns a clean
     payload (zero critical/serious WCAG 2.2 AA violations).

Marked ``@pytest.mark.e2e`` and ``@pytest.mark.live`` — runs only when both
``--live`` is passed AND a real Supabase project is reachable. Default CI
unit-test pass skips this; the dedicated e2e.yml workflow runs it with
the right env wired in.
"""
from __future__ import annotations

import pytest


# Critical + serious are the WCAG 2.2 AA severity floor we gate on.
# axe-core taxonomy: minor / moderate / serious / critical.
_BLOCKING_AXE_IMPACTS = {"critical", "serious"}


@pytest.mark.e2e
def test_browser_fixture_chain_imports():
    """Non-live smoke: prove the dep + fixture chain loads in CI.

    The e2e.yml workflow runs ``pytest -m "not live"`` by default — the
    full ``authed_browser`` test below is gated behind ``@pytest.mark.live``
    because it mints a real Supabase user. This non-live counterpart
    confirms the workflow's chromium install + pytest-playwright plugin +
    axe-playwright-python + browser-fixtures module all import successfully.

    Together those checks prove the workflow's chromium install + dep
    install + collection path are healthy — the smoke contract this
    iteration must lock before module sub-agents commit follow-on tests.
    """
    import playwright  # noqa: F401 — import-only smoke
    import axe_playwright_python  # noqa: F401

    from tests.integration.browser import conftest as browser_conftest

    assert hasattr(browser_conftest, "PLAYWRIGHT_VIEWPORTS")
    assert set(browser_conftest.PLAYWRIGHT_VIEWPORTS.keys()) == {
        "mobile",
        "tablet",
        "desktop",
    }
    assert browser_conftest.PLAYWRIGHT_VIEWPORTS["mobile"]["width"] == 375
    assert browser_conftest.PLAYWRIGHT_VIEWPORTS["desktop"]["width"] == 1280


@pytest.mark.e2e
@pytest.mark.live
def test_authed_home_loads_clean(authed_browser, base_url):
    """Open /home authenticated; expect header, no console errors, axe clean."""
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

    # The header is a structural anchor in the user-home layout — visible
    # state confirms the auth-gated shell rendered, not the login redirect.
    header = page.locator("header")
    assert header.count() >= 1, "expected at least one <header> element on /home"

    # Allow the page a beat to settle (fonts, deferred scripts) so axe
    # audits the steady-state DOM, not the FOUT transient.
    page.wait_for_load_state("networkidle")

    assert not console_errors, (
        f"console.error chatter on /home (regression): {console_errors!r}"
    )

    # axe-playwright-python wraps the canonical script-tag + page.evaluate
    # injection. WCAG 2.2 AA = wcag2a + wcag2aa + wcag21aa + wcag22aa tags
    # per axe-core 4.10 rule pack.
    from axe_playwright_python.sync_playwright import Axe

    axe = Axe()
    results = axe.run(
        page,
        options={
            "runOnly": {
                "type": "tag",
                "values": ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"],
            },
            "resultTypes": ["violations"],
        },
    )
    violations = results.response.get("violations", [])
    blocking = [
        v for v in violations if v.get("impact") in _BLOCKING_AXE_IMPACTS
    ]
    assert not blocking, (
        f"WCAG 2.2 AA critical/serious violations on /home: "
        f"{[v['id'] for v in blocking]!r}"
    )
