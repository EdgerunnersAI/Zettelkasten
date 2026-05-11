# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_nodes).
# One-shot UI capture / audit tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Capture every zettel modal as Playwright screenshots and run a content audit.

Workflow:
  1. Pull every row from ``kg_nodes`` via the Supabase REST API using the
     service-role key from ``supabase/.env``.
  2. Normalize each row's ``summary`` cell through
     ``website.core.summary_normalizer.normalize_summary_for_wire`` so the
     captured shot reflects what the LIVE site will render after this commit
     deploys.
  3. Render each node into an offline Chromium page that loads the production
     CSS file (``website/features/user_zettels/css/user_zettels.css``) and a
     vanilla-JS port of the renderer functions (collapsible H1, subtler H3,
     Brief/Detailed split). Three screenshots per node — top, middle, bottom.
  4. Classify each modal text against the audit rules (UA leak, lowercase
     overview, JSON in chapters, missing closing remarks, dangling-tail
     brief, sub-section bullet duplicating top bullet).

Outputs to ``ops/screenshots/zettel-modal-audit-2026-04-25/``:
  * ``<idx>-<source>-<slug>-<position>.png``
  * ``INDEX.md`` listing every capture
  * ``AUDIT.md`` per-zettel pass/fail table

Environment:
  Reads ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` from ``supabase/.env``
  (or environment overrides). Service-role bypasses RLS so we see every row.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from html import escape
from pathlib import Path
from typing import Any

import urllib.request
import urllib.parse
import urllib.error

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from website.core.summary_normalizer import normalize_summary_for_wire  # noqa: E402

OUT_DIR = REPO_ROOT / "ops" / "screenshots" / "zettel-modal-audit-2026-04-25"
CSS_PATH = REPO_ROOT / "website" / "features" / "user_zettels" / "css" / "user_zettels.css"

# ---------------------------------------------------------------------------
# Supabase REST helper (no SDK dependency — we just need a single SELECT)
# ---------------------------------------------------------------------------


def _load_supabase_env() -> tuple[str, str]:
    """Parse supabase/.env for SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY."""
    env_file = REPO_ROOT / "supabase" / ".env"
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if (not url or not key) and env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "SUPABASE_URL" and not url:
                url = v
            elif k == "SUPABASE_SERVICE_ROLE_KEY" and not key:
                key = v
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    return url.rstrip("/"), key


def fetch_all_nodes() -> list[dict[str, Any]]:
    url, key = _load_supabase_env()
    endpoint = (
        f"{url}/rest/v1/kg_nodes"
        f"?select=id,name,source_type,url,summary,tags,created_at"
        f"&order=created_at.desc"
        f"&limit=200"
    )
    req = urllib.request.Request(endpoint, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Slug + filename helpers
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "untitled")[:40]


# ---------------------------------------------------------------------------
# Offline renderer: produce a self-contained HTML page that loads the
# production CSS and renders a single node's modal contents the same way
# user_zettels.js does. Mirrors renderStructuredDetailed + the collapsible
# H1 logic + the Brief/Detailed dual-section layout.
# ---------------------------------------------------------------------------


def build_modal_html(node_name: str, source_type: str, envelope_json: str) -> str:
    css = CSS_PATH.read_text(encoding="utf-8")
    payload_js = json.dumps(envelope_json)
    title_safe = escape(node_name or "Untitled")
    src_safe = escape((source_type or "web").lower())
    return r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Zettel modal capture</title>
<style>
:root {
  --accent: #14b8a6;
  --text-primary: #f5f7fa;
  --text-secondary: #b6bec9;
  --text-muted: #6f7682;
  --border: rgba(255,255,255,0.08);
  --border-light: rgba(255,255,255,0.05);
  --bg-tertiary: rgba(255,255,255,0.03);
  --accent-glow: rgba(20,184,166,0.18);
  --shadow-lg: 0 20px 60px rgba(0,0,0,0.35);
  --font-mono: ui-monospace, SFMono-Regular, Menlo, monospace;
}
body { margin: 0; background: #0b0f15; color: var(--text-primary);
       font-family: 'Inter', -apple-system, system-ui, sans-serif; }
.modal-frame { width: 720px; margin: 32px auto; padding: 28px 32px;
               background: linear-gradient(155deg, #131923, #0e131c);
               border: 1px solid rgba(255,255,255,0.08);
               border-radius: 16px; box-shadow: var(--shadow-lg); }
__CSS__
</style>
</head>
<body>
<div class="modal-frame">
  <div class="zettels-summary-meta-row">
    <span class="zettels-source-badge __SRC__">__SRC__</span>
  </div>
  <h2 class="zettels-summary-title" id="summary-title">__TITLE__</h2>
  <div class="zettels-summary-text" id="summary-text"></div>
</div>
<script>
var ENVELOPE = JSON.parse(__PAYLOAD__);
function tryParseEnvelope(raw) {
  if (raw == null) return null;
  if (typeof raw === 'object') return raw;
  try { return JSON.parse(raw); } catch (e) { return null; }
}
function buildChevronSpan() {
  var s = document.createElement('span');
  s.className = 'zettels-summary-h2-chevron';
  s.setAttribute('aria-hidden','true');
  s.innerHTML = '<svg viewBox="0 0 24 24" fill="none">'+
    '<path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" '+
    'stroke-linecap="round" stroke-linejoin="round"></path></svg>';
  return s;
}
function appendBody(parent, bullets, subs) {
  if (bullets && bullets.length) {
    var ul = document.createElement('ul');
    ul.className = 'zettels-summary-list';
    bullets.forEach(function(b){
      var t = (b==null?'':String(b)).trim(); if (!t) return;
      var li = document.createElement('li');
      li.className = 'zettels-summary-list-item';
      li.textContent = t; ul.appendChild(li);
    });
    if (ul.childNodes.length) parent.appendChild(ul);
  }
  if (subs && typeof subs === 'object' && !Array.isArray(subs)) {
    Object.keys(subs).forEach(function(k){
      var arr = subs[k];
      if (!Array.isArray(arr) || !arr.length) return;
      var h5 = document.createElement('h5');
      h5.className = 'zettels-summary-h3';
      h5.textContent = String(k||'').trim();
      parent.appendChild(h5);
      var ul = document.createElement('ul');
      ul.className = 'zettels-summary-list';
      arr.forEach(function(b){
        var t = (b==null?'':String(b)).trim(); if (!t) return;
        var li = document.createElement('li');
        li.className = 'zettels-summary-list-item';
        li.textContent = t; ul.appendChild(li);
      });
      if (ul.childNodes.length) parent.appendChild(ul);
    });
  }
}
function setExpanded(h, p, expanded) {
  h.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  if (expanded) p.removeAttribute('data-collapsed');
  else p.setAttribute('data-collapsed','true');
}
function renderStructured(container, sections) {
  var first = false;
  sections.forEach(function(section){
    if (!section || typeof section !== 'object') return;
    var heading = section.heading == null ? '' : String(section.heading).trim();
    var bullets = Array.isArray(section.bullets) ? section.bullets : [];
    var subs = section.sub_sections;
    if (!heading) { appendBody(container, bullets, subs); return; }
    var h4 = document.createElement('h4');
    h4.className = 'zettels-summary-h2';
    h4.setAttribute('role','button');
    h4.setAttribute('tabindex','0');
    var label = document.createElement('span');
    label.className = 'zettels-summary-h2-label';
    label.textContent = heading;
    h4.appendChild(label);
    h4.appendChild(buildChevronSpan());
    container.appendChild(h4);
    var panel = document.createElement('div');
    panel.className = 'zettels-summary-panel';
    appendBody(panel, bullets, subs);
    container.appendChild(panel);
    setExpanded(h4, panel, !first);  // ALL EXPANDED for screenshot capture
    setExpanded(h4, panel, true);
    first = true;
  });
}
function render() {
  var env = tryParseEnvelope(ENVELOPE);
  var brief = env && env.brief_summary ? String(env.brief_summary).trim() : '';
  var detailed = env && env.detailed_summary;
  var closing = env && env.closing_remarks ? String(env.closing_remarks).trim() : '';
  var c = document.getElementById('summary-text');
  if (brief) {
    var bw = document.createElement('div');
    bw.className = 'zettels-summary-section zettels-summary-brief';
    var bh = document.createElement('h3');
    bh.className = 'zettels-summary-section-heading';
    bh.textContent = 'Brief';
    var bp = document.createElement('p');
    bp.className = 'zettels-summary-section-body';
    bp.textContent = brief;
    bw.appendChild(bh); bw.appendChild(bp);
    c.appendChild(bw);
  }
  if (Array.isArray(detailed) && detailed.length) {
    if (brief) {
      var hr = document.createElement('hr');
      hr.className = 'zettels-summary-divider';
      c.appendChild(hr);
    }
    var dw = document.createElement('div');
    dw.className = 'zettels-summary-section zettels-summary-detailed';
    var dh = document.createElement('h3');
    dh.className = 'zettels-summary-section-heading';
    dh.textContent = 'Detailed';
    dw.appendChild(dh);
    renderStructured(dw, detailed);
    c.appendChild(dw);
  }
  if (closing) {
    var cw = document.createElement('div');
    cw.className = 'zettels-summary-section';
    var ch = document.createElement('h3');
    ch.className = 'zettels-summary-section-heading';
    ch.textContent = 'Closing remarks';
    var cp = document.createElement('p');
    cp.className = 'zettels-summary-section-body';
    cp.textContent = closing;
    cw.appendChild(ch); cw.appendChild(cp);
    c.appendChild(cw);
  }
}
render();
window.__READY__ = true;
</script>
</body>
</html>
""".replace("__CSS__", css).replace("__TITLE__", title_safe)\
      .replace("__SRC__", src_safe).replace("__PAYLOAD__", payload_js)


# ---------------------------------------------------------------------------
# Audit checks — run against the rendered modal text
# ---------------------------------------------------------------------------

UA_RE = re.compile(r"\b(Mac OS X|Windows NT|Mozilla|Chrome OS|iPhone OS|WebKit|Gecko)\b")
JSON_BULLET_RE = re.compile(r'\{[^}]*"(?:timestamp|title|bullets)"', re.I)
DANGLING_RE = re.compile(r"\b(?:in|the|a|an|of|to|for|with|by|on|at)\.\s*$", re.I)


def jaccard(a: str, b: str) -> float:
    aw = set(re.findall(r"[a-z0-9]+", a.lower()))
    bw = set(re.findall(r"[a-z0-9]+", b.lower()))
    aw = {w for w in aw if len(w) > 3}
    bw = {w for w in bw if len(w) > 3}
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


def audit_envelope(env: dict[str, Any]) -> dict[str, Any]:
    brief = (env.get("brief_summary") or "").strip()
    closing = (env.get("closing_remarks") or "").strip()
    detailed = env.get("detailed_summary") or []
    full_text_parts = [brief, closing]
    # UA-leak check only scans brief/overview/closing — chapter bodies may
    # legitimately mention "Mac OS X" or "WebKit" as products (the speaker
    # is discussing them substantively), which is different from a stray
    # User-Agent leak in the speaker line.
    ua_scan_parts: list[str] = [brief, closing]
    overview_top = ""
    overview_subs_text: list[str] = []
    has_closing_section = bool(closing)
    has_overview = False
    h3_subs: list[str] = []
    for section in detailed if isinstance(detailed, list) else []:
        if not isinstance(section, dict):
            continue
        h = (section.get("heading") or "").strip()
        bullets = section.get("bullets") or []
        subs = section.get("sub_sections") or {}
        for b in bullets:
            full_text_parts.append(str(b))
        if isinstance(subs, dict):
            for k, v in subs.items():
                h3_subs.append(str(k))
                if isinstance(v, list):
                    for x in v:
                        full_text_parts.append(str(x))
        if h.lower() == "overview":
            has_overview = True
            for b in bullets:
                ua_scan_parts.append(str(b))
            if bullets:
                overview_top = str(bullets[0])
            if isinstance(subs, dict):
                for k, v in subs.items():
                    if isinstance(v, list):
                        # Format-and-speakers + Publication subs are still
                        # speaker-context — scan them. Other subs may be
                        # narrative bullets (less risk) but we include them
                        # too since Overview is the anchor section.
                        ua_scan_parts.extend(str(x) for x in v)
                        overview_subs_text.extend(str(x) for x in v)
        if h.lower() == "closing remarks":
            has_closing_section = True
    full_text = " ".join(p for p in full_text_parts if p)
    ua_scan_text = " ".join(p for p in ua_scan_parts if p)

    checks: dict[str, bool] = {}
    # Brand names that legitimately start lowercase (beehiiv, iPhone, etc.)
    # are NOT capitalization failures — Overview top bullets that start with
    # one of these are accepted.
    _LOWER_BRAND_PREFIXES = ("beehiiv", "iphone", "ipad", "imac", "ebay")
    if not overview_top:
        checks["overview_top_capitalized"] = True
    else:
        first = overview_top.lstrip("\"'")
        if not first:
            checks["overview_top_capitalized"] = True
        elif not first[0].isalpha():
            checks["overview_top_capitalized"] = True
        elif first[0].isupper():
            checks["overview_top_capitalized"] = True
        else:
            lower_lead = first.lower()
            checks["overview_top_capitalized"] = any(
                lower_lead.startswith(brand) for brand in _LOWER_BRAND_PREFIXES
            )
    checks["no_ua_leak"] = not bool(UA_RE.search(ua_scan_text))
    checks["no_json_bullet"] = not bool(JSON_BULLET_RE.search(full_text))
    checks["no_overview_or_summary_sub"] = all(
        s.strip().lower() not in {"overview", "summary"} for s in h3_subs
    )
    checks["has_closing_or_brief"] = bool(closing) or has_closing_section or bool(brief)
    sentences = re.split(r"(?<=[.!?])\s+", brief)
    last_sentence = sentences[-1] if sentences else brief
    checks["brief_no_dangling_tail"] = not bool(DANGLING_RE.search(last_sentence))
    # No "X. The Y is X. The..." mid-fragment double-clause
    checks["brief_no_runon_fragment"] = "is The " not in brief and "is the The " not in brief
    # Overview top doesn't repeat any sub bullet (Jaccard >= 0.55)
    overlap = max((jaccard(overview_top, s) for s in overview_subs_text), default=0.0)
    checks["overview_unique_top"] = overlap < 0.55 if overview_top else True
    return {
        "checks": checks,
        "pass_count": sum(1 for v in checks.values() if v),
        "total_checks": len(checks),
        "overlap_jaccard": round(overlap, 2),
    }


# ---------------------------------------------------------------------------
# Main: orchestrate fetch + render + screenshot + audit
# ---------------------------------------------------------------------------


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[capture] fetching kg_nodes from Supabase...", flush=True)
    nodes = fetch_all_nodes()
    print(f"[capture] fetched {len(nodes)} nodes", flush=True)

    from playwright.sync_api import sync_playwright

    index_lines = ["# Zettel Modal Capture Index", "", f"Total nodes: {len(nodes)}", ""]
    audit_rows: list[tuple[str, dict[str, Any]]] = []
    captured = 0
    failed: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 820, "height": 900})
        page = ctx.new_page()
        for idx, node in enumerate(nodes):
            name = (node.get("name") or "").strip() or "Untitled"
            src = (node.get("source_type") or "web").lower()
            slug = _slug(name)
            try:
                envelope_str = normalize_summary_for_wire(node.get("summary"), src)
            except Exception as exc:  # noqa: BLE001
                print(f"[capture] node {idx} normalize fail: {exc}", flush=True)
                failed.append(f"{idx}-{src}-{slug}")
                continue
            envelope = json.loads(envelope_str)
            audit = audit_envelope(envelope)
            audit_rows.append((f"{idx:02d}-{src}-{slug}", audit))

            html = build_modal_html(name, src, envelope_str)
            html_path = OUT_DIR / f"_render-{idx:02d}.html"
            html_path.write_text(html, encoding="utf-8")
            page.goto("file:///" + str(html_path).replace("\\", "/"))
            page.wait_for_function("window.__READY__ === true", timeout=8000)
            page.wait_for_timeout(120)

            modal = page.locator(".modal-frame")
            box = modal.bounding_box()
            if not box:
                failed.append(f"{idx}-{src}-{slug}")
                continue

            # Total content height
            content_h = page.evaluate(
                "() => document.querySelector('.modal-frame').scrollHeight"
            )
            viewport_h = 900
            positions = {"top": 0}
            if content_h > viewport_h:
                positions["middle"] = max(0, (content_h - viewport_h) // 2)
                positions["bottom"] = max(0, content_h - viewport_h)
            else:
                positions["middle"] = 0
                positions["bottom"] = 0

            for pos_name, scroll_y in positions.items():
                page.evaluate(f"window.scrollTo(0, {scroll_y})")
                page.wait_for_timeout(50)
                file_name = f"{idx:02d}-{src}-{slug}-{pos_name}.png"
                out_path = OUT_DIR / file_name
                page.screenshot(path=str(out_path), full_page=False)
                captured += 1
                index_lines.append(
                    f"- `{file_name}` — **{name}** · source=`{src}` · pos=`{pos_name}` · "
                    f"audit_pass={audit['pass_count']}/{audit['total_checks']}"
                )
            html_path.unlink(missing_ok=True)
        browser.close()

    (OUT_DIR / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # AUDIT.md
    audit_lines = [
        "# Zettel Audit (post-fix render)",
        "",
        f"Nodes audited: {len(audit_rows)}",
        "",
        "| # | Zettel | overview_cap | no_ua | no_json | no_dup_sub | closing | brief_tail | brief_runon | overview_unique | pass |",
        "| - | ------ | ------------ | ----- | ------- | ---------- | ------- | ---------- | ----------- | --------------- | ---- |",
    ]
    pass_count = 0
    for slug, a in audit_rows:
        c = a["checks"]
        row_pass = a["pass_count"] == a["total_checks"]
        if row_pass:
            pass_count += 1
        cells = [
            "Y" if c["overview_top_capitalized"] else "N",
            "Y" if c["no_ua_leak"] else "N",
            "Y" if c["no_json_bullet"] else "N",
            "Y" if c["no_overview_or_summary_sub"] else "N",
            "Y" if c["has_closing_or_brief"] else "N",
            "Y" if c["brief_no_dangling_tail"] else "N",
            "Y" if c["brief_no_runon_fragment"] else "N",
            "Y" if c["overview_unique_top"] else "N",
        ]
        audit_lines.append(
            f"| {slug.split('-')[0]} | `{slug}` | "
            + " | ".join(cells)
            + f" | **{a['pass_count']}/{a['total_checks']}** |"
        )
    audit_lines.append("")
    audit_lines.append(f"**Pass rate:** {pass_count}/{len(audit_rows)}")
    (OUT_DIR / "AUDIT.md").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    print(f"[capture] screenshots: {captured}, audit pass: {pass_count}/{len(audit_rows)}", flush=True)
    if failed:
        print(f"[capture] failed: {failed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
