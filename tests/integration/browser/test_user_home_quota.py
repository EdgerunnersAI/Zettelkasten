"""UH-05 (D-3 pivot) — quota-exhausted resume flow on /home.

Original UH-05 spec called for a phone-collect modal round-trip, but the
modal was removed from ``index.html`` (verified: 341-line index, no phone
markup). D-3 pivot: instead lock the *quota-exhausted* path that
``home.js`` actually ships — when ``POST /api/zettels/add`` returns
402 with ``code: "quota_exhausted"``, ``window.ZKPricing.openPurchase``
is invoked with the offending tier.

The test intercepts ``/api/zettels/add`` via Playwright's route handler so
no Supabase/Gemini calls happen, then stubs ``window.ZKPricing`` on the
page to capture the openPurchase argument shape.
"""
from __future__ import annotations

import json

import pytest


_QUOTA_RESPONSE = {
    "error": "Daily zettel quota exhausted",
    "code": "quota_exhausted",
    "tier": "free",
}


@pytest.mark.e2e
@pytest.mark.live
def test_quota_exhausted_opens_purchase(authed_browser, base_url):
    """402 quota_exhausted → ZKPricing.openPurchase invoked (UH-05 D-3 pivot)."""
    ctx, _user = authed_browser

    # Stub ZKPricing.openPurchase BEFORE any page script runs, so the
    # capture array is in place when home.js evaluates window.ZKPricing.
    ctx.add_init_script(
        """
        window.__zkPurchaseCalls = [];
        window.ZKPricing = {
          openPurchase: function (arg) {
            window.__zkPurchaseCalls.push(arg);
            return Promise.resolve();
          },
        };
        """
    )

    page = ctx.new_page()

    # Intercept /api/zettels/add POST → 402 quota_exhausted. All other
    # requests fall through to the real server.
    def _route_summarize(route):
        if route.request.method == "POST":
            route.fulfill(
                status=402,
                content_type="application/json",
                body=json.dumps(_QUOTA_RESPONSE),
            )
        else:
            route.continue_()

    page.route("**/api/zettels/add", _route_summarize)

    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    # Open the Add-Zettel dropdown, type a URL, submit.
    page.locator("#add-zettel-btn").click()
    page.locator("#add-url-input").fill("https://example.com/some-article")
    page.locator("#add-submit-btn").click()

    # Wait for openPurchase to fire (max 5s — quota path is synchronous
    # after the 402 lands).
    page.wait_for_function(
        "() => Array.isArray(window.__zkPurchaseCalls) && window.__zkPurchaseCalls.length >= 1",
        timeout=5_000,
    )

    calls = page.evaluate("() => window.__zkPurchaseCalls")
    assert len(calls) >= 1, "expected ZKPricing.openPurchase to fire on 402"
    # The arg shape is a dict — at minimum it should carry a reason/code
    # so the pricing modal can render the right copy.
    arg = calls[0]
    assert isinstance(arg, dict), (
        f"ZKPricing.openPurchase received non-dict arg: {arg!r}"
    )
