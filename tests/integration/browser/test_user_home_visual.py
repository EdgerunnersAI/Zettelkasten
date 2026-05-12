"""UH-02 — visual regression baseline for /home at 3 widths.

3-width matrix (mobile=375, tablet=768, desktop=1280) per D-5 research
flake-mitigation checklist:

  * ``device_scale_factor=1`` (no retina drift)
  * animations disabled via ``page.add_init_script`` (belt-and-braces on
    top of Playwright's ``animations="disabled"`` default)
  * ``document.fonts.ready`` awaited so FOUT doesn't bake into baselines
  * ``max_diff_pixel_ratio=0.002`` (~0.2% — the spec tolerance)
  * single worker enforced by the global ``pytest`` config

Baselines live at ``tests/integration/browser/snapshots/user_home/``.
First run with ``--update-snapshots`` writes the PNGs; subsequent runs
diff against them. Marked ``@pytest.mark.live`` so default CI skips
until a snapshot-refresh PR lands.
"""
from __future__ import annotations

import pytest


# Inline CSS that nukes every animation/transition for deterministic
# screenshots. Goes in via ``add_init_script`` so it's present before
# any page script renders, plus a follow-up add_style_tag after navigation
# in case the page re-injects styles.
_KILL_ANIMATIONS_JS = """
() => {
  const style = document.createElement('style');
  style.setAttribute('data-zk-test', 'kill-anim');
  style.textContent = `*,*::before,*::after{
    animation-duration:0s !important;
    animation-delay:0s !important;
    transition-duration:0s !important;
    transition-delay:0s !important;
    caret-color:transparent !important;
  }`;
  if (document.head) document.head.appendChild(style);
  else document.addEventListener('DOMContentLoaded', () => document.head.appendChild(style));
}
"""


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.parametrize("device", ["mobile", "tablet", "desktop"])
def test_home_visual_baseline(
    browser, base_url, playwright_devices, device,
):
    """Visual baseline at one of 3 widths (UH-02).

    Uses Playwright's canonical ``expect(...).to_have_screenshot()`` API.
    Baselines land at ``tests/integration/browser/snapshots/user_home/``
    via the ``output_dir`` arg. First run with
    ``--update-snapshots`` writes the PNG; subsequent runs diff with a
    0.2% pixel-ratio tolerance.
    """
    from pathlib import Path
    from playwright.sync_api import expect

    viewport = playwright_devices[device]
    ctx = browser.new_context(
        viewport=viewport,
        device_scale_factor=1,
        locale="en-US",
        timezone_id="UTC",
    )
    snapshot_dir = (
        Path(__file__).parent / "snapshots" / "user_home"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    try:
        ctx.add_init_script(_KILL_ANIMATIONS_JS)
        page = ctx.new_page()
        page.goto(f"{base_url}/home", wait_until="domcontentloaded")
        # Wait for fonts so FOUT doesn't bake into baselines.
        page.evaluate("document.fonts && document.fonts.ready")
        page.wait_for_load_state("networkidle")
        # Belt-and-braces: re-apply kill-anim CSS after final paint.
        page.add_style_tag(
            content=(
                "*,*::before,*::after{animation-duration:0s !important;"
                "transition-duration:0s !important;}"
            )
        )

        # 0.002 = 0.2% pixel-ratio tolerance per spec UH-02.
        expect(page).to_have_screenshot(
            f"home-{device}.png",
            full_page=True,
            max_diff_pixel_ratio=0.002,
            animations="disabled",
        )
    finally:
        ctx.close()
