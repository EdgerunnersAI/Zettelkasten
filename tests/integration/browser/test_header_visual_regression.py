"""HD-04 — visual regression for shared header at mobile/tablet/desktop widths.

Per Playwright Visual Comparisons doc (D-5 research): screenshot the
``header.zk-header`` locator (NOT full page) at three responsive widths
to keep snapshots tight and diff-stable. Full-page snapshots would churn
on every body-content change unrelated to the header.

Marked ``@pytest.mark.e2e + @pytest.mark.live`` because the test mints a
real Supabase user via ``authed_browser`` and drives a Chromium instance.
The CI e2e workflow runs ``-m "not live"`` by default, so this test only
fires when ``--live`` is passed alongside a running staging app.

Snapshot dir: ``tests/integration/browser/snapshots/header/``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# /knowledge-graph hosts the header AND has the amber accent rebound — a
# distinct visual surface from teal pages. /home/zettels is the canonical
# teal/inner-page surface. Two routes × 3 widths = 6 baselines, kept low
# to avoid maintenance burden on a moderate-tier module.
TARGET_ROUTES = [
    ("zettels", "/home/zettels"),
    ("knowledge_graph", "/knowledge-graph"),
]

WIDTHS = [
    ("mobile",  375),
    ("tablet",  768),
    ("desktop", 1280),
]

SNAPSHOT_DIR = (
    Path(__file__).parent / "snapshots" / "header"
)


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.parametrize("route_key,path", TARGET_ROUTES, ids=[r for r, _ in TARGET_ROUTES])
@pytest.mark.parametrize("size_key,width", WIDTHS, ids=[s for s, _ in WIDTHS])
def test_header_visual_at_width(
    authed_browser, base_url, route_key, path, size_key, width,
):
    """Snapshot the rendered <header.zk-header> at one (route, width) cell."""
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.set_viewport_size({"width": width, "height": 900})

    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    # Disable animations + caret blink so the diff doesn't oscillate.
    page.add_style_tag(content=(
        "*, *::before, *::after { "
        "animation-duration: 0s !important; "
        "transition-duration: 0s !important; "
        "caret-color: transparent !important; "
        "}"
    ))

    header = page.locator("header.zk-header")
    assert header.count() == 1, (
        f"{path} @ {width}: expected exactly one header.zk-header, "
        f"got {header.count()}"
    )

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / f"header_{route_key}_{size_key}.png"

    # On first run, the screenshot is written and the test passes; on
    # subsequent runs, Playwright's pixelmatch diffs against the on-disk
    # baseline. We use the locator-scoped screenshot API so only the
    # header rect is captured.
    if snapshot_path.exists():
        # Re-shoot to a transient buffer for byte-diff. Pixelmatch via
        # pytest-playwright's expect()/toHaveScreenshot would be cleaner
        # but adds a fixture dependency we don't need here — strict byte
        # equality is acceptable for the moderate-tier baseline lane and
        # is what the D-5 research recommended (max_diff_pixels=0).
        current = header.screenshot()
        baseline = snapshot_path.read_bytes()
        assert current == baseline, (
            f"{path} @ {width}: header visual regression vs {snapshot_path}"
        )
    else:
        header.screenshot(path=str(snapshot_path))
