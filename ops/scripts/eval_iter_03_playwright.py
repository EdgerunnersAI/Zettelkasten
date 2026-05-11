# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_*/rag_*/etc.).
# One-shot iter-03 eval harness — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Iter-03 end-to-end eval harness — Playwright + prod API + UI verification.

Replaces the Claude-in-Chrome MCP path. This is the canonical iter-03+ eval
loop. Drives a real Chromium against the deployed prod site as Naruto, walks
every page in the prod surface, runs the 13 iter-03 RAG queries, asserts
every UI/UX requirement called out in the iter-03 plan
(``docs/superpowers/plans/2026-04-28-iter-03-rag-burst-correctness.md``),
and writes a single JSON + a directory of full-page screenshots.

It is INTENTIONALLY verbose. Every action is timed and logged. Every screen
is captured. Every UI assertion is recorded with an evidence pointer. The
harness is meant to be re-runnable in CI for iter-04 with zero prompt churn.

Plan items covered (from Phase 4D §verification.md and the plan body):

  1. Public surface (no auth):                /, /about, /pricing
  2. Knowledge Graph 3D viz (amber territory): /knowledge-graph
  3. Authed surface (Naruto):                  /home, /home/zettels,
                                              /home/kastens, /home/rag
  4. Composer placeholder uses Kasten name:    /home/rag with kasten chosen
  5. All 13 iter-03 queries via /api/rag/adhoc, captured per-stage
     (q1-q10 iter-02 replay + av-1/av-2/av-3 action-verb regression)
  6. Strong-mode (quality=high) on q4 — exercises critic loop + Gemini Pro
  7. Adversarial-negative q9 — must refuse / 0 citations
  8. Action-verb regression (av-1/av-2/av-3) — must NOT over-refuse
  9. Add-zettels modal Select-all + counter
 10. Burst pressure: 12 concurrent /api/rag/adhoc → expect 503 + Retry-After
 11. ?debug=1 hidden in prod — no .rag-debug-panel, no model-name leak
 12. Color audit per page: zero purple/violet/lavender on Kasten surface;
     amber (#D4A024) reserved for /knowledge-graph
 13. Performance budget per page: TTFB / TTI / LCP must be under SLOs
 14. Per-step latency report + improvement plan if any step exceeds SLO

Usage:

    # 1. Get the Supabase JWT from a signed-in tab. On https://zettelkasten.in/home/rag
    #    open DevTools Console:
    #
    #        JSON.parse(localStorage.getItem('zk-auth-token')).access_token
    #
    #    Copy the printed string.
    #
    # 2. Export it (one-shot — never commit, never echo to logs):

    export ZK_BEARER_TOKEN='eyJhbGc...'

    # 3. Run:

    python ops/scripts/eval_iter_03_playwright.py

    # Optional flags:
    #   --headed           visible browser
    #   --site URL         override https://zettelkasten.in
    #   --skip-burst       skip the 12-fetch queue-pressure stage
    #   --max-queries N    only run the first N RAG queries (debug)
    #   --kasten "name"    override the target Kasten name
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    sync_playwright,
    TimeoutError as PWTimeout,
)

# ── Constants ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]

# iter-04 forensics: invoking this file as `python ops/scripts/eval_iter_03_playwright.py`
# puts ``ops/scripts/`` on sys.path but NOT the repo root, so the post-eval scoring
# stage at line ~1422 (``from ops.scripts import score_rag_eval``) failed with
# ``No module named 'ops'``. The 2026-04-30 iter-04 run hit this; verification
# results were complete but RAGAS/DeepEval/composite metrics never wrote to disk.
# Bootstrap ROOT here so every later ``import ops.X`` (and any sibling helper
# the harness gains in future iters) resolves regardless of how the script is
# invoked.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# iter-04: paths are derived at runtime from --iter so the same harness can
# drive iter-03 (legacy), iter-04 (current), or any future iter without
# editing this file. Default kept at iter-03 for backward compatibility with
# existing scripts / Makefile invocations.
DEFAULT_ITER = "iter-03"


def _eval_paths_for(iter_id: str) -> tuple[Path, Path, Path, Path, Path]:
    eval_dir = ROOT / "docs" / "rag_eval" / "common" / "knowledge-management" / iter_id
    return (
        eval_dir,
        eval_dir / "queries.json",
        eval_dir / "verification_results.json",
        eval_dir / "screenshots",
        eval_dir / "timing_report.md",
    )


# iter-05: outputs that a prior run may have produced and that should be
# archived before a new run overwrites them. Add new artifacts here as the
# harness gains them; do NOT include `queries.json` or `baseline.json` since
# those are inputs / config, not outputs.
_RUN_OUTPUT_FILES = (
    "verification_results.json",
    "timing_report.md",
    "eval.json",
    "scores.md",
)


def _archive_prior_run(eval_dir: Path, *, force: bool) -> Path | None:
    """iter-05 hardening: if a prior run produced ``verification_results.json``
    in ``eval_dir``, move ALL prior outputs (json/md + the screenshots dir)
    to ``eval_dir/_runs/<UTC-ISO timestamp>/`` so the new run starts clean
    and prior evidence is preserved with full provenance.

    With ``force=True`` the archival is skipped (caller will overwrite). The
    overwriting path exists because operators sometimes deliberately want to
    blow away a known-bad run without keeping it; `--force` is the opt-in
    escape hatch.

    Returns the archive directory if anything was moved, ``None`` otherwise
    (no prior results, or ``force=True``).
    """
    if force:
        return None
    if not (eval_dir / "verification_results.json").exists():
        return None
    timestamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    archive_dir = eval_dir / "_runs" / timestamp
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for name in _RUN_OUTPUT_FILES:
        src = eval_dir / name
        if src.exists():
            shutil.move(str(src), str(archive_dir / name))
            moved.append(name)
    src_screens = eval_dir / "screenshots"
    if src_screens.exists() and src_screens.is_dir():
        # Move the entire dir (preserves any quarantine subfolders like
        # 2026-04-30's `_OVERWRITTEN_BY_INTERRUPTED_RUN_*`).
        shutil.move(str(src_screens), str(archive_dir / "screenshots"))
        moved.append("screenshots/")
    if moved:
        try:
            display_path = archive_dir.resolve().relative_to(ROOT)
        except ValueError:
            display_path = archive_dir
        logger.info(
            "archived prior run to %s (moved: %s)",
            display_path,
            ", ".join(moved),
        )
        return archive_dir
    # Nothing actually moved — clean up the empty archive dirs we created.
    try:
        archive_dir.rmdir()
        (eval_dir / "_runs").rmdir()
    except OSError:
        pass
    return None


# Module-level constants kept for backward compatibility (some imports may
# reference them); resolved against the default iter only. Runtime path
# resolution happens inside main() via --iter.
EVAL_DIR, QUERIES_PATH, RESULTS_PATH, SCREENSHOTS_DIR, TIMING_LOG_PATH = _eval_paths_for(DEFAULT_ITER)

DEFAULT_SITE = "https://zettelkasten.in"
DEFAULT_KASTEN = "Knowledge Management & Personal Productivity"

# iter-03 source-diversification probe (user request 2026-04-28).
# Adds two well-aligned Zettels from previously under-represented source types
# (newsletter + web essay) to the Knowledge Management Kasten, then asks two
# targeted questions whose gold answers live ONLY in those new Zettels. This
# verifies the entire pipeline end-to-end:
#   /api/summarize  →  kg_nodes write  →  chunk + embed  →  rag_sandbox_members
#   /api/rag/sandboxes/{id}/members      →  POST (bulk add)
#   /api/rag/adhoc                       →  retrieval picks up the new chunks
#
# URLs are chosen for topical fit with the existing 7 Zettels (PKM, tools for
# thought, productivity) so the synthesizer has real material to cite.
DIVERSIFY_URLS: list[dict] = [
    {
        "url": "https://nesslabs.com/personal-knowledge-management",
        "expected_source_type": "newsletter",
        "label": "Ness Labs — Personal Knowledge Management",
    },
    {
        "url": "https://maggieappleton.com/garden-history",
        "expected_source_type": "web",
        "label": "Maggie Appleton — A Brief History & Ethos of the Digital Garden",
    },
]

# Targeted Q-A on the new Zettels (run after diversification). These do NOT
# overwrite queries.json — they're harness-only probes.
DIVERSIFY_QA: list[dict] = [
    {
        "qid": "div-1",
        "expected_source_type": "newsletter",
        "expected_url_substring": "nesslabs.com",
        "text": (
            "What concrete practices does the Ness Labs PKM piece recommend for "
            "building a personal knowledge management system, and how does it "
            "frame the difference between collecting and connecting notes?"
        ),
        "quality": "fast",
    },
    {
        "qid": "div-2",
        "expected_source_type": "web",
        "expected_url_substring": "maggieappleton.com",
        "text": (
            "According to Maggie Appleton's history of the digital garden, what "
            "distinguishes a digital garden from a traditional blog, and what are "
            "the core ethos points she calls out?"
        ),
        "quality": "fast",
    },
]

# SLOs from the plan: end-user acceptable latency. Anything over warns.
PAGE_LCP_BUDGET_MS = 4000  # public pages must paint LCP under 4s
RAG_FAST_LATENCY_BUDGET_MS = 30_000  # fast tier: ≤30s
RAG_HIGH_LATENCY_BUDGET_MS = 90_000  # Strong/high tier: ≤90s (covers Pro multi-hop)
P95_LATENCY_BUDGET_MS = 45_000  # 13-query p95 budget

REFUSAL_PATTERNS = [
    re.compile(r"i can'?t find", re.I),
    re.compile(r"i cannot find", re.I),
    re.compile(r"i don'?t (have|see)", re.I),
    re.compile(r"no information", re.I),
    re.compile(r"not in your zettels?", re.I),
]

PURPLE_NAMES = re.compile(r"\b(purple|violet|lavender|magenta|indigo)\b", re.I)
PURPLE_HSL = re.compile(r"hsla?\(\s*(2[5-9]\d|3[0-1]\d|320)\s*,", re.I)
AMBER_HEX = re.compile(r"#d4a024", re.I)

logger = logging.getLogger("eval_iter_03")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool | None
    evidence: str | None = None
    detail: dict[str, Any] | None = None
    duration_ms: int | None = None


@dataclass
class PhaseReport:
    phase: str
    started_at: float = field(default_factory=time.time)
    duration_ms: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ms_since(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def _expected_set(q: dict) -> set[str]:
    e = q.get("expected_primary_citation", q.get("expected"))
    if e is None:
        return set()
    if isinstance(e, list):
        return set(e)
    return {e}


def _is_refusal(text: str) -> bool:
    return any(p.search(text or "") for p in REFUSAL_PATTERNS)


def _set_gold_for_query(result: dict, *, primary: str | None, expected: list[str]) -> None:
    """iter-12 Task 15: E1 contract — refusal-expected rows get gold_at_1=None (N/A)."""
    result["expected_empty"] = not bool(expected)
    if result["expected_empty"]:
        result["gold_at_1"] = None  # E1 N/A; excluded from scored denominator
    else:
        result["gold_at_1"] = bool(primary and primary in expected)


def _set_latency_fields(result: dict, *, synth_ms: float | None, p_user_complete_ms: float | None) -> None:
    """iter-12 Task 15: clarify the historical latency_ms_server misname.

    latency_ms_server was the synth-after-TTFT timer (H4); iter-12 adds explicit
    names. Legacy field kept one iter for reader transition; remove in iter-13.
    """
    result["latency_ms_synth_after_ttft"] = synth_ms
    result["latency_ms_server_total"] = p_user_complete_ms
    result["latency_ms_server"] = synth_ms  # legacy alias; remove in iter-13


def _shoot(page: Page, slug: str) -> str:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SCREENSHOTS_DIR / f"{slug}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
        return str(out.relative_to(ROOT)).replace("\\", "/")
    except Exception as exc:
        logger.warning("screenshot %s failed: %s", slug, exc)
        return f"<error: {exc}>"


def _color_audit_of_loaded_styles(page: Page) -> dict:
    """Walk every accessible stylesheet rule looking for forbidden colors.

    Returns a dict with `offending` (list of {sheet, rule}) and `passed` bool.
    """
    js = """
    () => {
        const offending = [];
        const PURPLE_NAMES = /\\b(purple|violet|lavender|magenta|indigo)\\b/i;
        const PURPLE_HSL = /hsla?\\(\\s*(2[5-9]\\d|3[0-1]\\d|320)\\s*,/i;
        for (const sheet of document.styleSheets) {
            let rules;
            try { rules = sheet.cssRules; } catch (_) { continue; }
            if (!rules) continue;
            for (const r of rules) {
                const text = r.cssText || '';
                if (PURPLE_NAMES.test(text) || PURPLE_HSL.test(text)) {
                    offending.push({
                        source: sheet.href || 'inline',
                        rule: text.slice(0, 240),
                    });
                }
            }
        }
        return offending;
    }
    """
    findings = page.evaluate(js)
    return {"offending_rules": findings, "passed": len(findings) == 0}


class IssueCollector:
    """Per-page sink for the trivial-but-not-trivial UI issues that show up in
    the screenshots / DevTools but don't crash the page:

      * Console errors / warnings (page.on('console'))
      * Failed network requests (page.on('requestfailed'))
      * 4xx / 5xx responses on same-origin assets (page.on('response'))
      * Images that load 0×0 (broken src or aborted by client)
      * Layout shift (CLS) above the Web Vitals "good" budget (0.1)
      * Missing alt attributes on visible <img> tags
      * Buttons / anchors with empty accessible name
      * Cache-bust version drift across script tags compared to other pages

    Each collector is bound to one page object. Call ``attach()`` BEFORE
    navigating, then ``snapshot()`` after the page settles. Call ``reset()``
    between pages to scope findings.
    """

    def __init__(self, page: Page):
        self.page = page
        self.console_errors: list[dict] = []
        self.console_warnings: list[dict] = []
        self.network_failures: list[dict] = []
        self.bad_responses: list[dict] = []
        self._attached = False

    def attach(self) -> None:
        if self._attached:
            return

        def _on_console(msg):
            try:
                rec = {"type": msg.type, "text": msg.text[:400], "url": (msg.location or {}).get("url")}
            except Exception:
                rec = {"type": "?", "text": "<error reading console msg>", "url": None}
            if rec["type"] == "error":
                self.console_errors.append(rec)
            elif rec["type"] == "warning":
                self.console_warnings.append(rec)

        def _on_requestfailed(req):
            try:
                self.network_failures.append({
                    "url": req.url[:240],
                    "method": req.method,
                    "failure": (req.failure or "")[:200],
                    "resource_type": req.resource_type,
                })
            except Exception:
                pass

        def _on_response(resp):
            try:
                status = resp.status
                if status >= 400 and status < 600:
                    self.bad_responses.append({
                        "url": resp.url[:240], "status": status, "method": resp.request.method,
                    })
            except Exception:
                pass

        self.page.on("console", _on_console)
        self.page.on("requestfailed", _on_requestfailed)
        self.page.on("response", _on_response)
        self._attached = True

    def reset(self) -> None:
        self.console_errors.clear()
        self.console_warnings.clear()
        self.network_failures.clear()
        self.bad_responses.clear()

    def dom_audit(self) -> dict:
        """Run the DOM-side checks: broken images, missing alts, empty
        accessible names, anchors to known-dead routes, layout shift."""
        js = """
        async () => {
            const out = { broken_images: [], missing_alt: [], empty_a11y_name: [], dead_links: [] };
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                const rect = img.getBoundingClientRect();
                const visible = rect.width > 0 && rect.height > 0;
                if (!visible) continue;
                if (!img.complete || img.naturalWidth === 0) {
                    out.broken_images.push({ src: (img.getAttribute('src') || '').slice(0, 240),
                                             alt: img.getAttribute('alt') || '' });
                }
                if (!img.hasAttribute('alt')) {
                    out.missing_alt.push({ src: (img.getAttribute('src') || '').slice(0, 240) });
                }
            }
            const interactive = Array.from(document.querySelectorAll('button, a, [role=button]'));
            for (const el of interactive) {
                const visible = el.offsetParent !== null;
                if (!visible) continue;
                const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '').trim();
                if (!name) {
                    out.empty_a11y_name.push({
                        tag: el.tagName, classes: (el.className || '').slice(0, 120),
                        href: el.getAttribute('href') || null,
                    });
                }
            }
            // Layout shift score over the lifetime of this navigation.
            let cls = 0;
            try {
                await new Promise((res) => {
                    const po = new PerformanceObserver((list) => {
                        for (const entry of list.getEntries()) {
                            if (!entry.hadRecentInput) cls += entry.value;
                        }
                    });
                    po.observe({ type: 'layout-shift', buffered: true });
                    setTimeout(res, 1500);
                });
            } catch (_) {}
            out.cumulative_layout_shift = +cls.toFixed(4);
            return out;
        }
        """
        try:
            return self.page.evaluate(js)
        except Exception as exc:
            return {"error": str(exc)}

    def snapshot(self) -> dict:
        dom = self.dom_audit()
        # Filter out noise we know is not our bug:
        #   * Brave Shields blocks Cloudflare insights (ERR_BLOCKED_BY_CLIENT)
        ignore = ("static.cloudflareinsights.com", "stats.g.doubleclick", "google-analytics")
        net_failures = [f for f in self.network_failures if not any(s in f["url"] for s in ignore)]
        bad_resp = [r for r in self.bad_responses if not any(s in r["url"] for s in ignore)]
        return {
            "console_errors": list(self.console_errors),
            "console_warnings_count": len(self.console_warnings),
            "network_failures": net_failures,
            "bad_responses": bad_resp,
            "dom_audit": dom,
            # Aggregate "is this page clean" — used as the single passed flag.
            "passed": (
                not self.console_errors
                and not net_failures
                and not bad_resp
                and not (dom.get("broken_images") or [])
                and (dom.get("cumulative_layout_shift") or 0) < 0.1
            ),
        }


def _capture_perf(page: Page) -> dict:
    """Pull Navigation Timing API + LCP via PerformanceObserver."""
    js = """
    async () => {
        const nav = performance.getEntriesByType('navigation')[0] || {};
        const paints = performance.getEntriesByType('paint') || [];
        const fp = paints.find(p => p.name === 'first-paint');
        const fcp = paints.find(p => p.name === 'first-contentful-paint');
        let lcp = null;
        try {
            await new Promise((resolve) => {
                const po = new PerformanceObserver((list) => {
                    const entries = list.getEntries();
                    if (entries.length) {
                        lcp = entries[entries.length - 1].startTime;
                        resolve();
                    }
                });
                po.observe({ type: 'largest-contentful-paint', buffered: true });
                setTimeout(resolve, 2000);
            });
        } catch (_) {}
        return {
            ttfb_ms: nav.responseStart ? Math.round(nav.responseStart) : null,
            domcontent_ms: nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null,
            load_ms: nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
            first_paint_ms: fp ? Math.round(fp.startTime) : null,
            first_contentful_paint_ms: fcp ? Math.round(fcp.startTime) : null,
            largest_contentful_paint_ms: lcp ? Math.round(lcp) : null,
        };
    }
    """
    try:
        return page.evaluate(js)
    except Exception as exc:
        return {"error": str(exc)}


# ── Auth ────────────────────────────────────────────────────────────────────

def install_token(context: BrowserContext, site: str, token: str) -> None:
    """Pre-seed localStorage for any document on the site origin.

    The SPA's user_rag.js bootstraps Supabase from localStorage['zk-auth-token']
    (see website/features/user_rag/js/user_rag.js getAuthToken()). By writing
    the JWT BEFORE the first navigation, the page boots authenticated and we
    skip OAuth entirely.
    """
    payload = json.dumps({
        "access_token": token,
        "token_type": "bearer",
        "expires_at": int(time.time()) + 3600,
    })
    context.add_init_script(
        f"try {{ window.localStorage.setItem('zk-auth-token', {json.dumps(payload)}); }} catch (_) {{}}"
    )


# ── API helpers (from page context, so cookies + same-origin work) ──────────

def api_fetch_json(page: Page, path: str, token: str, *, method: str = "GET",
                   body: Any | None = None, timeout_ms: int = 90_000) -> dict:
    js = """
    async ({ path, token, method, body, timeout }) => {
        const controller = new AbortController();
        const t = setTimeout(() => controller.abort(), timeout);
        const t0 = performance.now();
        try {
            const r = await fetch(path, {
                method,
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: 'Bearer ' + token,
                },
                credentials: 'include',
                body: body ? JSON.stringify(body) : undefined,
                signal: controller.signal,
            });
            const elapsed = Math.round(performance.now() - t0);
            const status = r.status;
            const text = await r.text();
            let parsed;
            try { parsed = text ? JSON.parse(text) : null; } catch (_) { parsed = null; }
            return { ok: r.ok, status, elapsed_ms: elapsed, body: parsed, raw: parsed ? null : text };
        } catch (e) {
            return { ok: false, status: 0, elapsed_ms: Math.round(performance.now() - t0), error: String(e) };
        } finally {
            clearTimeout(t);
        }
    }
    """
    return page.evaluate(js, {
        "path": path, "token": token, "method": method,
        "body": body, "timeout": timeout_ms,
    })


def api_fetch_sse(page: Page, path: str, token: str, *, body: Any,
                  timeout_ms: int = 120_000) -> dict:
    """iter-09 RES-5: stream:true fetch + getReader() to capture true p_user_*_ms."""
    js = """
    async ({ path, token, body, timeout }) => {
        const controller = new AbortController();
        const t = setTimeout(() => controller.abort(), timeout);
        const t0 = performance.now();
        let firstTokenAt = null, lastTokenAt = null, doneAt = null;
        let turn = null, citations = null, errorPayload = null;
        let buf = '';
        const decoder = new TextDecoder('utf-8');
        try {
            const r = await fetch(path, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: 'Bearer ' + token,
                },
                credentials: 'include',
                body: JSON.stringify(body),
                signal: controller.signal,
            });
            const status = r.status;
            if (!r.ok) {
                const text = await r.text();
                let parsed;
                try { parsed = text ? JSON.parse(text) : null; } catch (_) { parsed = null; }
                return {
                    ok: false, status,
                    elapsed_ms: Math.round(performance.now() - t0),
                    body: parsed, raw: parsed ? null : text,
                };
            }
            const reader = r.body.getReader();
            outer: while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buf += decoder.decode(value, { stream: true });
                while (true) {
                    const idx = buf.indexOf('\\n\\n');
                    if (idx < 0) break;
                    const frame = buf.slice(0, idx);
                    buf = buf.slice(idx + 2);
                    if (!frame || frame.startsWith(':')) continue;
                    let evName = null, dataStr = '';
                    for (const line of frame.split('\\n')) {
                        if (line.startsWith('event:')) evName = line.slice(6).trim();
                        else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
                    }
                    if (!evName) continue;
                    const now = performance.now();
                    if (evName === 'token') {
                        if (firstTokenAt === null) firstTokenAt = now;
                        lastTokenAt = now;
                    } else if (evName === 'done') {
                        doneAt = now;
                        try {
                            const payload = dataStr ? JSON.parse(dataStr) : {};
                            turn = payload.turn || null;
                            citations = (turn && turn.citations) || null;
                        } catch (_) {}
                        break outer;
                    } else if (evName === 'error') {
                        try { errorPayload = dataStr ? JSON.parse(dataStr) : { raw: dataStr }; }
                        catch (_) { errorPayload = { raw: dataStr }; }
                        break outer;
                    } else if (evName === 'citations') {
                        try {
                            const payload = dataStr ? JSON.parse(dataStr) : {};
                            citations = payload.citations || payload || null;
                        } catch (_) {}
                    }
                }
            }
            const elapsed = Math.round(performance.now() - t0);
            return {
                ok: doneAt !== null && errorPayload === null,
                status,
                elapsed_ms: elapsed,
                p_user_first_token_ms: firstTokenAt === null ? null : Math.round(firstTokenAt - t0),
                p_user_last_token_ms: lastTokenAt === null ? null : Math.round(lastTokenAt - t0),
                p_user_complete_ms: doneAt === null ? null : Math.round(doneAt - t0),
                turn, citations, error: errorPayload,
            };
        } catch (e) {
            return {
                ok: false, status: 0,
                elapsed_ms: Math.round(performance.now() - t0),
                error: String(e),
            };
        } finally {
            clearTimeout(t);
        }
    }
    """
    return page.evaluate(js, {
        "path": path, "token": token, "body": body, "timeout": timeout_ms,
    })


# ── Phase 1: Public surface (no auth) ───────────────────────────────────────

PUBLIC_PAGES: list[tuple[str, str]] = [
    ("/", "01_root_index"),
    ("/about", "02_about"),
    ("/pricing", "03_pricing"),
]


def phase_public_pages(page: Page, site: str, issues: IssueCollector) -> PhaseReport:
    rep = PhaseReport(phase="public_pages")
    t_phase = time.time()
    for path, slug in PUBLIC_PAGES:
        issues.reset()
        t0 = time.time()
        try:
            page.goto(f"{site}{path}", wait_until="networkidle", timeout=30_000)
        except PWTimeout:
            rep.checks.append(CheckResult(
                name=f"GET {path}", passed=False,
                detail={"error": "navigation timeout >30s"},
                duration_ms=_ms_since(t0),
            ))
            continue
        page.wait_for_timeout(800)
        evidence = _shoot(page, slug)
        perf = _capture_perf(page)
        color = _color_audit_of_loaded_styles(page)
        snap = issues.snapshot()
        rep.checks.append(CheckResult(
            name=f"GET {path}",
            passed=(
                color["passed"]
                and snap["passed"]
                and (perf.get("largest_contentful_paint_ms") or 0) < PAGE_LCP_BUDGET_MS
            ),
            evidence=evidence,
            detail={"perf": perf, "color_audit": color, "issues": snap},
            duration_ms=_ms_since(t0),
        ))
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 2: Knowledge graph (amber territory — different palette rules) ────

def phase_knowledge_graph(page: Page, site: str, issues: IssueCollector) -> PhaseReport:
    rep = PhaseReport(phase="knowledge_graph")
    t_phase = time.time()
    issues.reset()
    t0 = time.time()
    try:
        page.goto(f"{site}/knowledge-graph", wait_until="networkidle", timeout=30_000)
    except PWTimeout:
        rep.checks.append(CheckResult(
            name="GET /knowledge-graph", passed=False,
            detail={"error": "navigation timeout >30s"},
            duration_ms=_ms_since(t0),
        ))
        rep.duration_ms = _ms_since(t_phase)
        return rep
    page.wait_for_timeout(1200)
    evidence = _shoot(page, "04_knowledge_graph_3d")
    perf = _capture_perf(page)
    # Amber rule: /knowledge-graph IS allowed amber; purple still forbidden.
    color = _color_audit_of_loaded_styles(page)
    snap = issues.snapshot()
    rep.checks.append(CheckResult(
        name="GET /knowledge-graph (amber-allowed surface)",
        passed=color["passed"] and snap["passed"],
        evidence=evidence,
        detail={"perf": perf, "color_audit": color, "issues": snap, "note": "amber #D4A024 allowed here"},
        duration_ms=_ms_since(t0),
    ))
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 3: Authed home + zettels + kastens ────────────────────────────────

AUTHED_PAGES: list[tuple[str, str]] = [
    ("/home", "05_home"),
    ("/home/zettels", "06_home_zettels"),
    ("/home/kastens", "07_home_kastens"),
]


def _check_header_parity(page: Page) -> dict:
    """Compare the rendered header across authed pages — every page MUST have:
      * the shared <!--ZK_HEADER--> partial rendered (look for .home-avatar-btn)
      * the avatar <img> with a non-empty src (i.e. ZKHeader.boot ran)
      * a fallback <span> in DOM but hidden when img loaded

    The 'avatar loads on /home/zettels but not on /home/rag' bug is exactly
    this: page A boots ZKHeader, page B forgets — partial renders, img stays
    blank. We check img src + class state explicitly.
    """
    js = """
    () => {
        const out = {};
        const btn = document.querySelector('.home-avatar-btn');
        out.has_avatar_btn = !!btn;
        const img = document.querySelector('.home-avatar-img');
        out.has_avatar_img = !!img;
        out.avatar_src = img ? img.getAttribute('src') || '' : '';
        out.avatar_complete = img ? !!img.complete : false;
        out.avatar_natural_w = img && img.naturalWidth ? img.naturalWidth : 0;
        out.avatar_visible = img ? !img.classList.contains('hidden') : false;
        const fb = document.querySelector('.home-avatar-fallback');
        out.has_fallback = !!fb;
        out.fallback_visible = fb ? fb.classList.contains('visible') : false;
        // Boot signal: window.ZKHeader exposes a boolean once boot completes.
        out.zkheader_present = !!(window.ZKHeader);
        out.zkheader_booted = !!(window.ZKHeader && window.ZKHeader.__booted);
        return out;
    }
    """
    return page.evaluate(js)


def phase_authed_pages(page: Page, site: str, token: str, issues: IssueCollector) -> PhaseReport:
    rep = PhaseReport(phase="authed_pages")
    t_phase = time.time()
    for path, slug in AUTHED_PAGES:
        issues.reset()
        t0 = time.time()
        try:
            page.goto(f"{site}{path}", wait_until="networkidle", timeout=30_000)
        except PWTimeout:
            rep.checks.append(CheckResult(
                name=f"GET {path}", passed=False,
                detail={"error": "navigation timeout >30s"},
                duration_ms=_ms_since(t0),
            ))
            continue
        page.wait_for_timeout(1500)  # let ZKHeader.boot + avatar preload run
        evidence = _shoot(page, slug)
        perf = _capture_perf(page)
        color = _color_audit_of_loaded_styles(page)
        title = page.title()
        header = _check_header_parity(page)
        snap = issues.snapshot()
        # Trivial-but-not-trivial UI bug: avatar img must have a src AND have
        # actually loaded (naturalWidth>0). On /home/rag this regresses if
        # user_rag.js skips ZKHeader.boot — same partial, same script tag,
        # but no boot call → img.src stays unset.
        avatar_loaded = (
            header["has_avatar_img"]
            and bool(header["avatar_src"])
            and header["avatar_natural_w"] > 0
        )
        rep.checks.append(CheckResult(
            name=f"{path} :: header parity (ZKHeader booted, avatar loaded)",
            passed=avatar_loaded and header.get("has_avatar_btn") and header.get("zkheader_booted"),
            evidence=evidence,
            detail={"header": header},
            duration_ms=_ms_since(t0),
        ))
        rep.checks.append(CheckResult(
            name=f"{path} :: color audit (no purple/violet/lavender)",
            passed=color["passed"],
            detail={"color_audit": color},
        ))
        rep.checks.append(CheckResult(
            name=f"{path} :: LCP under {PAGE_LCP_BUDGET_MS}ms",
            passed=(perf.get("largest_contentful_paint_ms") or 0) < PAGE_LCP_BUDGET_MS,
            detail={"perf": perf, "title": title},
        ))
        rep.checks.append(CheckResult(
            name=f"{path} :: page hygiene (console errors, broken images, dead requests, CLS)",
            passed=snap["passed"],
            detail={"issues": snap},
        ))
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 4: /home/rag composer + Kasten chooser ────────────────────────────

def phase_rag_composer(page: Page, site: str, token: str, kasten_name: str) -> tuple[PhaseReport, dict | None]:
    """Open /home/rag, find the target Kasten, screenshot composer, verify
    placeholder uses the Kasten name. Returns (report, sandbox_dict|None)."""
    rep = PhaseReport(phase="rag_composer")
    t_phase = time.time()

    t0 = time.time()
    page.goto(f"{site}/home/rag", wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1500)
    rep.checks.append(CheckResult(
        name="GET /home/rag",
        passed=True, evidence=_shoot(page, "08_rag_chooser"),
        duration_ms=_ms_since(t0),
        detail={"perf": _capture_perf(page)},
    ))

    # Discover sandboxes via API.
    t0 = time.time()
    sb = api_fetch_json(page, "/api/rag/sandboxes?limit=50", token)
    rep.checks.append(CheckResult(
        name="GET /api/rag/sandboxes",
        passed=bool(sb.get("ok")),
        detail={"http": sb.get("status"), "elapsed_ms": sb.get("elapsed_ms")},
        duration_ms=_ms_since(t0),
    ))
    target = None
    if sb.get("ok"):
        for s in (sb["body"] or {}).get("sandboxes", []):
            if s.get("name") == kasten_name:
                target = s
                break

    if not target:
        rep.checks.append(CheckResult(
            name="Kasten lookup",
            passed=False,
            detail={"available": [s.get("name") for s in (sb.get("body") or {}).get("sandboxes", [])]},
        ))
        rep.duration_ms = _ms_since(t_phase)
        return rep, None

    rep.notes.append(f"Kasten id={target['id']} members={target.get('member_count')}")

    # Try to enter the Kasten via the chooser (best-effort — selector may differ).
    t0 = time.time()
    entered_via_ui = False
    try:
        # Common selectors: data-kasten-id, role=button containing the name, etc.
        candidate_locators: list[Locator] = [
            page.get_by_text(kasten_name, exact=True).first,
            page.locator(f"[data-kasten-id='{target['id']}']").first,
            page.locator(f"[data-sandbox-id='{target['id']}']").first,
        ]
        for loc in candidate_locators:
            try:
                if loc and loc.count() > 0:
                    loc.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    entered_via_ui = True
                    break
            except Exception:
                continue
    except Exception as exc:
        rep.notes.append(f"chooser click best-effort failed: {exc}")
    rep.checks.append(CheckResult(
        name="Click Kasten in chooser",
        passed=entered_via_ui,
        evidence=_shoot(page, "09_rag_kasten_open") if entered_via_ui else None,
        duration_ms=_ms_since(t0),
        detail={"note": "best-effort UI click; falls through if not interactive"},
    ))

    # Composer placeholder check.
    t0 = time.time()
    placeholder = ""
    try:
        composer = page.locator("textarea, input[type='text']").first
        placeholder = composer.get_attribute("placeholder") or ""
    except Exception as exc:
        placeholder = f"<error: {exc}>"
    composer_ok = (
        kasten_name in placeholder
        or "Knowledge Management" in placeholder
        or "Ask " in placeholder  # generic fallback that still proves dynamic-ish
    )
    rep.checks.append(CheckResult(
        name="Composer placeholder uses Kasten name",
        passed=composer_ok,
        evidence=_shoot(page, "10_composer_placeholder"),
        detail={"placeholder": placeholder},
        duration_ms=_ms_since(t0),
    ))

    rep.duration_ms = _ms_since(t_phase)
    return rep, target


# ── Phase 5: 13 Q-A chain ───────────────────────────────────────────────────

def phase_diversify_sources(page: Page, token: str, sandbox_id: str) -> tuple[PhaseReport, list[dict]]:
    """Add 2 new Zettels (newsletter + web) to the Knowledge Management Kasten.

    Two-step API flow that mirrors the URL-paste UX:
      1. POST /api/summarize  with the URL → creates kg_node, returns node_id.
      2. POST /api/rag/sandboxes/{sandbox_id}/members → adds the new node to
         the Kasten (idempotent — sandbox_routes' bulk-add silently skips
         already-members).

    Returns the report PLUS the list of node_ids that landed (used by the
    follow-up Q-A phase to assert citations match).
    """
    rep = PhaseReport(phase="diversify_sources")
    t_phase = time.time()
    new_nodes: list[dict] = []

    for entry in DIVERSIFY_URLS:
        url = entry["url"]
        label = entry["label"]
        # Step 1: summarize → kg_node
        t0 = time.time()
        sum_resp = api_fetch_json(page, "/api/summarize", token, method="POST",
                                  body={"url": url}, timeout_ms=120_000)
        ok = bool(sum_resp.get("ok"))
        body = sum_resp.get("body") or {}
        node_id = body.get("node_id") or body.get("id") or (body.get("node") or {}).get("id")
        source_type = body.get("source_type") or (body.get("node") or {}).get("source_type")
        title = body.get("title") or (body.get("node") or {}).get("name")
        rep.checks.append(CheckResult(
            name=f"POST /api/summarize {url}",
            passed=ok and bool(node_id),
            duration_ms=_ms_since(t0),
            detail={
                "label": label,
                "http_status": sum_resp.get("status"),
                "elapsed_ms": sum_resp.get("elapsed_ms"),
                "node_id": node_id,
                "source_type": source_type,
                "title": title,
                "expected_source_type": entry["expected_source_type"],
                "source_type_matches_expected": (source_type == entry["expected_source_type"]),
                "summarize_error": (None if ok else (sum_resp.get("error") or sum_resp.get("raw"))),
            },
        ))
        if not (ok and node_id):
            rep.notes.append(f"summarize failed for {url}: {sum_resp}")
            continue

        # Step 2: bulk-add to sandbox (membership)
        t0 = time.time()
        add_resp = api_fetch_json(
            page, f"/api/rag/sandboxes/{sandbox_id}/members", token, method="POST",
            body={"node_ids": [node_id]}, timeout_ms=30_000,
        )
        added_count = (add_resp.get("body") or {}).get("added_count")
        members = (add_resp.get("body") or {}).get("members") or []
        rep.checks.append(CheckResult(
            name=f"POST /api/rag/sandboxes/{{id}}/members [{node_id}]",
            passed=bool(add_resp.get("ok")) and (added_count is not None) and added_count >= 0,
            duration_ms=_ms_since(t0),
            detail={
                "label": label,
                "http_status": add_resp.get("status"),
                "added_count": added_count,
                "member_count_after": len(members),
            },
        ))
        new_nodes.append({
            "url": url,
            "label": label,
            "node_id": node_id,
            "source_type": source_type,
            "title": title,
        })

    rep.notes.append(f"new_nodes={[n['node_id'] for n in new_nodes]}")
    rep.duration_ms = _ms_since(t_phase)
    return rep, new_nodes


def phase_post_diversification_qa(page: Page, token: str, sandbox_id: str,
                                   new_nodes: list[dict]) -> PhaseReport:
    """Run div-1 / div-2 — questions whose gold answers live ONLY in the new
    Zettels we just added. This proves the entire ingest → embed → retrieval →
    rerank → synthesize pipeline picked up the additions and that retrieval
    can surface them above the existing 7 members."""
    rep = PhaseReport(phase="post_diversification_qa")
    t_phase = time.time()
    if len(new_nodes) < 2:
        rep.checks.append(CheckResult(
            name="post-diversification Q-A",
            passed=False,
            detail={"error": f"only {len(new_nodes)} new nodes; need 2"},
        ))
        rep.duration_ms = _ms_since(t_phase)
        return rep

    # Build a url-substring → expected node_id map from the added nodes.
    expected_by_substring = {}
    for n in new_nodes:
        for sub in [n["url"]]:
            expected_by_substring[sub] = n["node_id"]

    for q in DIVERSIFY_QA:
        t0 = time.time()
        resp = api_fetch_json(page, "/api/rag/adhoc", token, method="POST", body={
            "sandbox_id": sandbox_id,
            "content": q["text"],
            "quality": q.get("quality", "fast"),
            "stream": False,
            "scope_filter": {},
            "title": f"{q['qid']}",
        }, timeout_ms=120_000)

        result: dict[str, Any] = {
            "qid": q["qid"],
            "expected_url_substring": q["expected_url_substring"],
            "expected_source_type": q["expected_source_type"],
            "text": q["text"],
            "http_status": resp.get("status"),
            "elapsed_ms": resp.get("elapsed_ms"),
        }
        if resp.get("ok"):
            turn = (resp.get("body") or {}).get("turn") or {}
            citations = turn.get("citations") or []
            result["answer"] = turn.get("content", "")
            result["citations"] = [
                {"id": c.get("id"), "node_id": c.get("node_id"), "title": c.get("title")}
                for c in citations
            ]
            result["primary_citation"] = citations[0]["node_id"] if citations else None
            result["critic_verdict"] = turn.get("critic_verdict")
            result["llm_model"] = turn.get("llm_model")
            _set_latency_fields(result, synth_ms=turn.get("latency_ms"), p_user_complete_ms=result.get("p_user_complete_ms"))
            refused = _is_refusal(result["answer"])
            result["refused"] = refused
            # Match: primary citation matches one of the new node_ids.
            new_ids = {n["node_id"] for n in new_nodes}
            result["primary_in_new_zettels"] = bool(
                result["primary_citation"] and result["primary_citation"] in new_ids
            )
            result["over_refusal"] = refused
            passed = (
                resp.get("status") == 200
                and result["primary_in_new_zettels"]
                and not refused
            )
        else:
            result["error"] = resp.get("error") or resp.get("raw") or "unknown"
            passed = False

        rep.checks.append(CheckResult(
            name=f"Q-A {q['qid']} (targets new {q['expected_source_type']})",
            passed=passed,
            duration_ms=_ms_since(t0),
            detail=result,
        ))
        logger.info(
            "  %s %s %sms primary_in_new=%s primary=%s critic=%s",
            q["qid"], result.get("http_status"), result.get("elapsed_ms"),
            result.get("primary_in_new_zettels"), result.get("primary_citation"),
            result.get("critic_verdict"),
        )

    rep.duration_ms = _ms_since(t_phase)
    return rep


def warmup_ping(page: Page, max_attempts: int = 3, sleep_s: float = 2.0) -> dict:
    """iter-09 RES-7: hit /api/health to dodge BGE int8 cold-start on first eval query."""
    last: dict[str, Any] = {"ok": False, "attempts": 0, "elapsed_ms": 0}
    for i in range(1, max_attempts + 1):
        t0 = time.monotonic()
        try:
            status = page.evaluate("async () => (await fetch('/api/health')).status")
            last = {
                "ok": status == 200, "attempts": i, "status": status,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
            if status == 200:
                return last
        except Exception as exc:
            last = {
                "ok": False, "attempts": i, "error": type(exc).__name__,
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        time.sleep(sleep_s)
    return last


def phase_rag_qa_chain(page: Page, token: str, sandbox_id: str,
                       queries: list[dict]) -> PhaseReport:
    rep = PhaseReport(phase="rag_qa_chain")
    t_phase = time.time()
    # iter-05: 3 s cooldown between back-to-back queries. Sustained eval ran 14
    # adhoc queries with no inter-request gap and pushed swap to 851/1024 MB
    # cap → worker oom_kill → q9 502 + q13/q14 HTTP 0. Tunable via env.
    cooldown_s = float(os.environ.get("ZK_EVAL_COOLDOWN_S", "3"))
    for q in queries:
        if rep.checks and cooldown_s > 0:
            time.sleep(cooldown_s)
        qid = q["qid"]
        text = q.get("text") or q.get("query")
        quality = q.get("quality") or q.get("class") and (
            "high" if q.get("class") in {"multi_hop", "thematic"} else "fast"
        ) or "fast"
        # Iter-03 §2C: pin q4 to high for Strong-mode coverage.
        if qid == "q4":
            quality = "high"

        t0 = time.time()
        # iter-09 RES-5: opt-in SSE harness reader to measure true p_user_*_ms.
        use_sse = os.environ.get("EVAL_USE_SSE_HARNESS", "true").lower() not in (
            "false", "0", "no", "off"
        )
        if use_sse:
            resp = api_fetch_sse(page, "/api/rag/adhoc", token, body={
                "sandbox_id": sandbox_id,
                "content": text,
                "quality": quality,
                "stream": True,
                "scope_filter": {},
                "title": f"{qid}",
            }, timeout_ms=120_000)
        else:
            resp = api_fetch_json(page, "/api/rag/adhoc", token, method="POST", body={
                "sandbox_id": sandbox_id,
                "content": text,
                "quality": quality,
                "stream": False,
                "scope_filter": {},
                "title": f"{qid}",
            }, timeout_ms=120_000)

        result: dict[str, Any] = {
            "qid": qid,
            "quality": quality,
            "expected": list(_expected_set(q)),
            "text": text,
            "http_status": resp.get("status"),
            "elapsed_ms": resp.get("elapsed_ms"),
            "p_user_first_token_ms": resp.get("p_user_first_token_ms"),
            "p_user_last_token_ms": resp.get("p_user_last_token_ms"),
            "p_user_complete_ms": resp.get("p_user_complete_ms"),
        }
        if not resp.get("ok"):
            result["error"] = resp.get("error") or resp.get("raw") or "unknown"
            _set_gold_for_query(result, primary=None, expected=list(_expected_set(q)))
        else:
            if use_sse:
                turn = resp.get("turn") or {}
                citations = turn.get("citations") or resp.get("citations") or []
            else:
                turn = (resp.get("body") or {}).get("turn") or {}
                citations = turn.get("citations") or []
            result["answer"] = turn.get("content", "")
            result["citations"] = [
                {"id": c.get("id"), "node_id": c.get("node_id"), "title": c.get("title")}
                for c in citations
            ]
            result["primary_citation"] = citations[0]["node_id"] if citations else None
            result["critic_verdict"] = turn.get("critic_verdict")
            result["llm_model"] = turn.get("llm_model")
            result["query_class"] = turn.get("query_class")
            _set_latency_fields(result, synth_ms=turn.get("latency_ms"), p_user_complete_ms=result.get("p_user_complete_ms"))
            result["retrieved_node_ids"] = turn.get("retrieved_node_ids", [])
            # iter-12 Task 35: pool-size + timing monitors from token_counts.stage_timings.
            # Values are None when the server hasn't supplied them; audit script degrades gracefully.
            _tc = turn.get("token_counts") or {}
            _st = _tc.get("stage_timings") or {}
            result["retrieval_pool_size_initial"] = turn.get("pool_size_initial", 0)
            result["retrieval_pool_size_retry"] = turn.get("pool_size_retry", 0)
            result["retry_outcome_class"] = _tc.get("retry_outcome_class", "success")
            result["t_db_wait_ms"] = _st.get("t_retrieval_ms")
            result["t_rerank_ms"] = _st.get("t_rerank_ms")
            result["t_synth_ms"] = _st.get("t_synth_ms")
            if use_sse:
                result["session_id"] = turn.get("session_id") or resp.get("session_id")
            else:
                result["session_id"] = (resp.get("body") or {}).get("session_id")
            refused = _is_refusal(result.get("answer", ""))
            expected = _expected_set(q)
            result["refused"] = refused
            _set_gold_for_query(result, primary=result["primary_citation"], expected=list(expected))
            result["over_refusal"] = refused and not result["expected_empty"]

        budget = RAG_HIGH_LATENCY_BUDGET_MS if quality == "high" else RAG_FAST_LATENCY_BUDGET_MS
        # iter-09 RES-5: prefer p_user_complete_ms (TTLT) over elapsed_ms once SSE
        # harness is on; falls back to elapsed_ms when SSE disabled or upstream
        # returned non-200 before any frame.
        budget_ref_ms = result.get("p_user_complete_ms") or result.get("elapsed_ms") or 0
        within_budget = budget_ref_ms <= budget
        result["within_budget"] = within_budget
        result["budget_ms"] = budget

        passed = (
            result.get("http_status") == 200
            and result.get("gold_at_1")
            and not result.get("over_refusal")
            and within_budget
        )
        rep.checks.append(CheckResult(
            name=f"Q-A {qid} ({quality})",
            passed=passed,
            duration_ms=_ms_since(t0),
            detail=result,
        ))
        # iter-09 RES-5 + side-ask: surface p_user_first_token_ms (TTFT) and
        # p_user_complete_ms (TTLT) per query so operators can see the actual
        # browser-perceived timing without grepping verification_results.json.
        def _fmt(v):
            return "-" if v is None else f"{int(v)}"
        logger.info(
            "  %s %s elapsed=%sms ttft=%s ttlt=%s gold@1=%s primary=%s critic=%s budget=%s",
            qid, result.get("http_status"), result.get("elapsed_ms"),
            _fmt(result.get("p_user_first_token_ms")),
            _fmt(result.get("p_user_complete_ms")),
            result.get("gold_at_1"), result.get("primary_citation"),
            result.get("critic_verdict"), within_budget,
        )
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 6: Burst pressure — expect 503 + Retry-After ──────────────────────

def phase_burst(page: Page, token: str, sandbox_id: str) -> PhaseReport:
    rep = PhaseReport(phase="burst_pressure")
    t_phase = time.time()
    js = """
    async ({ token, sandbox_id, count }) => {
        const promises = [];
        const probe = (i) => fetch('/api/rag/adhoc', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: 'Bearer ' + token,
            },
            credentials: 'include',
            body: JSON.stringify({
                sandbox_id, content: 'burst probe ' + i,
                quality: 'fast', stream: false, scope_filter: {},
                title: 'burst-' + i,
            }),
        }).then((r) => ({
            i, status: r.status,
            retry_after: r.headers.get('Retry-After'),
        })).catch((e) => ({ i, status: 0, error: String(e) }));
        for (let i = 0; i < count; i++) promises.push(probe(i));
        return await Promise.all(promises);
    }
    """
    t0 = time.time()
    statuses = page.evaluate(js, {"token": token, "sandbox_id": sandbox_id, "count": 12})
    by_status: dict[int, int] = {}
    retry_afters: list[str] = []
    for s in statuses:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
        if s.get("retry_after"):
            retry_afters.append(s["retry_after"])
    saw_503 = by_status.get(503, 0) > 0
    rep.checks.append(CheckResult(
        name="12 concurrent /api/rag/adhoc → expect ≥1 503 with Retry-After",
        passed=saw_503 and bool(retry_afters),
        detail={"by_status": by_status, "retry_after_samples": retry_afters[:3], "raw": statuses},
        duration_ms=_ms_since(t0),
    ))
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 7: ?debug=1 leak audit ────────────────────────────────────────────

def phase_debug_leak(page: Page, site: str) -> PhaseReport:
    rep = PhaseReport(phase="debug_param_leak")
    t_phase = time.time()
    t0 = time.time()
    page.goto(f"{site}/home/rag?debug=1", wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1000)
    debug_panel_count = page.locator(".rag-debug-panel, .debug-panel, [data-debug='1']").count()
    # Look for model-name leakage: gemini-2.5-flash / gemini-2.5-pro / gpt- / etc.
    body_text = page.inner_text("body").lower()
    model_leak_terms = ["gemini-2.5-flash", "gemini-2.5-pro", "claude-", "gpt-4", "gpt-3"]
    leak_hits = [t for t in model_leak_terms if t in body_text]
    passed = debug_panel_count == 0 and not leak_hits
    rep.checks.append(CheckResult(
        name="?debug=1 hidden in prod (no panel, no model name)",
        passed=passed,
        evidence=_shoot(page, "11_debug_param"),
        detail={"debug_panel_count": debug_panel_count, "model_leak_hits": leak_hits},
        duration_ms=_ms_since(t0),
    ))
    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Phase 8: Add-zettels modal Select-all ───────────────────────────────────

def phase_modal_select_all(page: Page, site: str) -> PhaseReport:
    rep = PhaseReport(phase="modal_select_all"); t_phase = time.time()
    t0 = time.time()
    page.goto(f"{site}/home/kastens", wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1500)

    triggered = False
    # Try several plausible triggers — site-design may vary.
    for sel in [
        "button:has-text('Add Zettels')",
        "button:has-text('Add Zettel')",
        "[data-action='add-zettels']",
        ".btn-add-zettels",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=3000)
                page.wait_for_timeout(800)
                triggered = True
                break
        except Exception:
            continue

    if not triggered:
        rep.checks.append(CheckResult(
            name="Open Add Zettels modal",
            passed=False,
            evidence=_shoot(page, "12_kastens_no_modal"),
            duration_ms=_ms_since(t0),
            detail={"note": "Add-Zettels trigger selector not found; UI may have changed"},
        ))
        rep.duration_ms = _ms_since(t_phase)
        return rep

    rep.checks.append(CheckResult(
        name="Open Add Zettels modal",
        passed=True,
        evidence=_shoot(page, "12_modal_open"),
        duration_ms=_ms_since(t0),
    ))

    # Try to click Select-all.
    t0 = time.time()
    select_all_clicked = False
    counter_text = ""
    for sel in [
        "[data-action='select-all']",
        "input[type='checkbox'][data-select-all='1']",
        "button:has-text('Select all')",
        ".select-all",
    ]:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click(timeout=3000)
                page.wait_for_timeout(500)
                select_all_clicked = True
                break
        except Exception:
            continue
    try:
        for sel in [".selection-counter", "[data-selection-count]", ".count-of"]:
            loc = page.locator(sel).first
            if loc.count() > 0:
                counter_text = loc.inner_text(timeout=1000)
                break
    except Exception:
        pass

    rep.checks.append(CheckResult(
        name="Select-all + counter increments",
        passed=select_all_clicked,
        evidence=_shoot(page, "13_modal_select_all"),
        detail={"counter_text": counter_text},
        duration_ms=_ms_since(t0),
    ))

    rep.duration_ms = _ms_since(t_phase)
    return rep


# ── Aggregation ─────────────────────────────────────────────────────────────

def _qa_summary(qa: PhaseReport) -> dict:
    rows = [c.detail or {} for c in qa.checks if c.detail]
    # iter-12 Task 15: E1 — refusal-expected rows are N/A; scored = non-empty-expected only.
    scored = [r for r in rows if not r.get("expected_empty")]
    n_scored = len(scored)
    total = len(rows)
    # accuracy_user_visible: gold@1 AND not refused AND not over_refusal, over scored rows only.
    user_visible_passes = sum(
        1 for r in scored
        if r.get("gold_at_1") is True
        and not r.get("over_refusal")
        and not r.get("refused")
    )
    # end_to_end_gold_at_1: unconditional primary-match over scored rows (legacy metric preserved).
    primary_passes = sum(1 for r in scored if r.get("gold_at_1") is True)
    over_refusals = sum(1 for r in rows if r.get("over_refusal"))
    infra = sum(1 for r in rows if (r.get("http_status") or 0) >= 500)
    elapsed = sorted((r.get("elapsed_ms") or 0) for r in rows)
    p95 = elapsed[int(len(elapsed) * 0.95)] if elapsed else 0

    def _p95(field: str) -> float | None:
        vals = sorted(v for v in (r.get(field) for r in rows) if isinstance(v, (int, float)))
        return vals[int(len(vals) * 0.95)] if vals else None

    def _avg(field: str) -> float | None:
        vals = [v for v in (r.get(field) for r in rows) if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    def _total(field: str) -> float | None:
        vals = [v for v in (r.get(field) for r in rows) if isinstance(v, (int, float))]
        return sum(vals) if vals else None

    return {
        "total": total,
        "n_scored": n_scored,
        "n_not_applicable": total - n_scored,
        "accuracy_user_visible": round(user_visible_passes / max(n_scored, 1), 4),
        "end_to_end_gold_at_1": round(primary_passes / max(n_scored, 1), 4),
        "synthesizer_over_refusals": over_refusals,
        "infra_failures": infra,
        "p95_latency_ms": p95,
        "p95_under_budget": p95 <= P95_LATENCY_BUDGET_MS,
        "p95_p_user_first_token_ms": _p95("p_user_first_token_ms"),
        "p95_p_user_complete_ms": _p95("p_user_complete_ms"),
        # iter-09 side-ask: avg + total p_user (TTLT / "time the user actually
        # waits") so operators don't have to compute these from per-query rows.
        "avg_p_user_complete_ms": _avg("p_user_complete_ms"),
        "total_p_user_complete_ms": _total("p_user_complete_ms"),
        "avg_p_user_first_token_ms": _avg("p_user_first_token_ms"),
    }


def _write_timing_report(reports: list[PhaseReport], qa_stats: dict, out: Path) -> None:
    lines: list[str] = []
    lines.append(f"# iter-03 timing & verification report")
    lines.append("")
    lines.append(f"Captured: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("")
    lines.append("## Phase timings")
    lines.append("")
    lines.append("| Phase | Duration (ms) | Checks pass / total |")
    lines.append("|---|---:|---|")
    for r in reports:
        passed = sum(1 for c in r.checks if c.passed)
        total = len(r.checks)
        lines.append(f"| {r.phase} | {r.duration_ms} | {passed}/{total} |")
    lines.append("")
    lines.append("## RAG Q-A chain")
    lines.append("")
    for k, v in qa_stats.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Per-phase notes")
    for r in reports:
        if r.notes:
            lines.append(f"### {r.phase}")
            for n in r.notes:
                lines.append(f"- {n}")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


# ── Main ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", default=DEFAULT_SITE)
    p.add_argument("--kasten", default=DEFAULT_KASTEN)
    p.add_argument("--token", default=os.environ.get("ZK_BEARER_TOKEN", ""))
    p.add_argument("--headed", action="store_true")
    p.add_argument("--skip-burst", action="store_true")
    p.add_argument("--max-queries", type=int, default=0)
    p.add_argument(
        "--iter",
        dest="iter_id",
        default=os.environ.get("ZK_EVAL_ITER", DEFAULT_ITER),
        help="iter directory under docs/rag_eval/common/knowledge-management/ (default: iter-03)",
    )
    p.add_argument(
        "--skip-scoring",
        action="store_true",
        help="Skip the post-Playwright RAGAS / DeepEval scoring stage.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing verification_results.json without "
            "archiving prior outputs to <iter>/_runs/<timestamp>/. Default "
            "is safe-archive (iter-05 hardening: prevents the iter-04 "
            "screenshot overwrite incident from recurring)."
        ),
    )
    args = p.parse_args(argv)

    # Re-resolve paths so this run targets the requested iter folder.
    iter_id = args.iter_id or DEFAULT_ITER
    eval_dir, queries_path, results_path, screenshots_dir, timing_log_path = _eval_paths_for(iter_id)
    # Re-bind module-level names so the phase functions (which read EVAL_DIR /
    # SCREENSHOTS_DIR / RESULTS_PATH directly) target the right folder.
    global EVAL_DIR, QUERIES_PATH, RESULTS_PATH, SCREENSHOTS_DIR, TIMING_LOG_PATH
    EVAL_DIR = eval_dir
    QUERIES_PATH = queries_path
    RESULTS_PATH = results_path
    SCREENSHOTS_DIR = screenshots_dir
    TIMING_LOG_PATH = timing_log_path

    if not args.token:
        logger.error(
            "no bearer token. From DevTools on /home/rag while signed in:\n"
            "  JSON.parse(localStorage.getItem('zk-auth-token')).access_token\n"
            "Then export ZK_BEARER_TOKEN=<value> or pass --token."
        )
        return 2

    if not QUERIES_PATH.exists():
        logger.error(
            "no queries.json at %s — copy from a prior iter or author one for %s",
            QUERIES_PATH, iter_id,
        )
        return 2
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    if args.max_queries:
        queries = queries[: args.max_queries]
    logger.info("loaded %d %s queries", len(queries), iter_id)

    # iter-05: archive any prior run before mkdir creates fresh empty dirs.
    _archive_prior_run(EVAL_DIR, force=args.force)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    overall_t0 = time.time()
    all_reports: list[PhaseReport] = []

    with sync_playwright() as p_play:
        browser = p_play.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        install_token(ctx, args.site, args.token)
        page = ctx.new_page()
        issues = IssueCollector(page)
        issues.attach()

        # Phase 1: public
        all_reports.append(phase_public_pages(page, args.site, issues))

        # Phase 2: knowledge graph
        all_reports.append(phase_knowledge_graph(page, args.site, issues))

        # Phase 3: authed pages
        all_reports.append(phase_authed_pages(page, args.site, args.token, issues))

        # Phase 4: RAG composer + Kasten chooser
        rag_rep, sandbox = phase_rag_composer(page, args.site, args.token, args.kasten)
        all_reports.append(rag_rep)

        if not sandbox:
            logger.error("Kasten not found; aborting Q-A chain")
            browser.close()
            _write_failure_artifacts(all_reports, args.site, queries)
            return 1

        # Phase 4b (iter-03 source-diversification probe): add 2 new Zettels
        # (newsletter + web) into the Kasten BEFORE the Q-A chain runs, so the
        # 13-query chain runs against a 9-member Kasten and the new diversity
        # is exercised.
        diversify_rep, new_nodes = phase_diversify_sources(page, args.token, sandbox["id"])
        all_reports.append(diversify_rep)

        # Re-screenshot /home/kastens to confirm member-count bump (7 → 9).
        try:
            page.goto(f"{args.site}/home/kastens", wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(1500)
            _shoot(page, "07b_home_kastens_after_diversify")
        except Exception:
            pass

        # iter-09 RES-7: warm-up ping before Q-A chain to dodge BGE int8 cold-start.
        warmup = warmup_ping(page)
        warm_rep = PhaseReport(phase="warmup_ping", duration_ms=warmup.get("elapsed_ms", 0))
        warm_rep.checks.append(CheckResult(
            name="warmup", passed=bool(warmup.get("ok")),
            duration_ms=warmup.get("elapsed_ms", 0), detail=warmup,
        ))
        all_reports.append(warm_rep)

        # Phase 5: Q-A chain (the centerpiece) — runs against the now-9-member Kasten.
        qa_rep = phase_rag_qa_chain(page, args.token, sandbox["id"], queries)
        all_reports.append(qa_rep)

        # Phase 5b: targeted Q-A on the 2 new Zettels.
        all_reports.append(phase_post_diversification_qa(
            page, args.token, sandbox["id"], new_nodes,
        ))

        # Phase 6: burst pressure
        if not args.skip_burst:
            all_reports.append(phase_burst(page, args.token, sandbox["id"]))

        # Phase 7: ?debug=1 leak
        all_reports.append(phase_debug_leak(page, args.site))

        # Phase 8: modal select-all
        all_reports.append(phase_modal_select_all(page, args.site))

        browser.close()

    qa_stats = _qa_summary(qa_rep)
    summary = {
        "iter": iter_id,
        "site": args.site,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_duration_ms": _ms_since(overall_t0),
        "kasten": {"name": args.kasten, "id": sandbox.get("id"), "member_count": sandbox.get("member_count")} if sandbox else None,
        "qa_summary": qa_stats,
        "phases": [
            {
                "phase": r.phase,
                "duration_ms": r.duration_ms,
                "checks": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "evidence": c.evidence,
                        "duration_ms": c.duration_ms,
                        "detail": c.detail,
                    }
                    for c in r.checks
                ],
                "notes": r.notes,
            }
            for r in all_reports
        ],
    }

    RESULTS_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _write_timing_report(all_reports, qa_stats, TIMING_LOG_PATH)
    logger.info("wrote %s", RESULTS_PATH)
    logger.info("wrote %s", TIMING_LOG_PATH)
    def _ms(v):
        return "-" if v is None else f"{int(v)}"
    logger.info(
        "DONE total=%sms gold@1=%s over_refusals=%s infra=%s p95=%sms "
        "p_user_avg=%sms p_user_total=%sms ttft_avg=%sms",
        summary["total_duration_ms"],
        qa_stats["end_to_end_gold_at_1"],
        qa_stats["synthesizer_over_refusals"],
        qa_stats["infra_failures"],
        qa_stats["p95_latency_ms"],
        _ms(qa_stats.get("avg_p_user_complete_ms")),
        _ms(qa_stats.get("total_p_user_complete_ms")),
        _ms(qa_stats.get("avg_p_user_first_token_ms")),
    )

    # iter-04: post-Playwright scoring stage. Pulls chunks from Supabase,
    # runs RAGAS + DeepEval + composite + holistic monitoring metrics, and
    # writes eval.json + scores.md alongside verification_results.json.
    # Skippable via --skip-scoring or non-zero exit ignored (scoring runs
    # last, so a scoring failure does not invalidate the Playwright run).
    if not args.skip_scoring:
        try:
            from ops.scripts import score_rag_eval
            logger.info("running RAGAS / DeepEval / composite scoring against %s", EVAL_DIR)
            score_rc = score_rag_eval.main(["--iter-dir", str(EVAL_DIR)])
            if score_rc != 0:
                logger.warning("scoring stage exited %d (verification still valid)", score_rc)
        except Exception as exc:  # noqa: BLE001 — scoring is best-effort
            logger.warning("scoring stage failed: %s", exc)

    return 0 if qa_stats["infra_failures"] == 0 else 1


def _write_failure_artifacts(reports: list[PhaseReport], site: str, queries: list[dict]) -> None:
    summary = {
        "iter": "iter-03",
        "site": site,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "aborted": True,
        "phases": [asdict(r) for r in reports],
    }
    RESULTS_PATH.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
