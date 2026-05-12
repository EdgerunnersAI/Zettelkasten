"""UH-06 — axe-core WCAG 2.2 AA scan of /home.

Uses ``axe-playwright-python`` (the canonical Python wrapper for the
Deque axe-core script — D-5 research §5, Option A). Fails on
critical/serious violations; allows minor/moderate (the standard floor
for production gates).

Also lands UH-03's computed-style portion: after the page is fully
rendered, scrape every element's ``color`` / ``background-color`` /
``border-color`` and assert no purple band hue (HSL 250-290).
"""
from __future__ import annotations

import re

import pytest


_BLOCKING_AXE_IMPACTS = {"critical", "serious"}

# rgb(R,G,B) / rgba(R,G,B,A) — captures the 3 channel ints so we can
# convert to HSL for the purple-hue gate.
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
    re.IGNORECASE,
)


def _rgb_to_hsl_hue(r: int, g: int, b: int) -> float | None:
    """Return hue in degrees [0, 360) or None for achromatic (grey) pixels."""
    r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
    mx = max(r_f, g_f, b_f)
    mn = min(r_f, g_f, b_f)
    if mx == mn:
        return None  # grey — no hue
    d = mx - mn
    if mx == r_f:
        h = (g_f - b_f) / d + (6 if g_f < b_f else 0)
    elif mx == g_f:
        h = (b_f - r_f) / d + 2
    else:
        h = (r_f - g_f) / d + 4
    return (h * 60) % 360


@pytest.mark.e2e
@pytest.mark.live
def test_home_axe_wcag22aa(authed_browser, base_url):
    """No critical/serious WCAG 2.2 AA violations on /home (UH-06)."""
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

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
    blocking = [v for v in violations if v.get("impact") in _BLOCKING_AXE_IMPACTS]
    assert not blocking, (
        "WCAG 2.2 AA critical/serious violations on /home: "
        f"{[(v['id'], v['impact']) for v in blocking]!r}"
    )


@pytest.mark.e2e
@pytest.mark.live
def test_home_computed_styles_no_purple(authed_browser, base_url):
    """Computed color/background/border on /home avoids HSL purple band (UH-03)."""
    ctx, _user = authed_browser
    page = ctx.new_page()
    page.goto(f"{base_url}/home", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")

    # Pull computed colors for every visible element. We keep the payload
    # bounded by skipping transparent / 0-alpha values browser-side.
    computed = page.evaluate(
        """() => {
          const out = [];
          const props = ['color', 'backgroundColor', 'borderTopColor',
                         'borderRightColor', 'borderBottomColor', 'borderLeftColor'];
          for (const el of document.querySelectorAll('*')) {
            const cs = getComputedStyle(el);
            for (const p of props) {
              const v = cs[p];
              if (!v || v === 'rgba(0, 0, 0, 0)' || v === 'transparent') continue;
              out.push({tag: el.tagName, id: el.id || '', prop: p, value: v});
            }
          }
          return out;
        }"""
    )

    purple_hits: list[dict] = []
    for row in computed:
        m = _RGB_RE.search(row["value"])
        if not m:
            continue
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hue = _rgb_to_hsl_hue(r, g, b)
        if hue is None:
            continue
        if 250 <= hue <= 290:
            purple_hits.append({**row, "hue": round(hue, 1)})

    assert not purple_hits, (
        "computed purple-band hues (HSL 250-290) detected on /home:\n"
        + "\n".join(
            f"  <{h['tag'].lower()} id={h['id']!r}> {h['prop']}={h['value']} "
            f"hue={h['hue']}"
            for h in purple_hits[:20]
        )
    )
