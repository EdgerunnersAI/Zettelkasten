"""FastAPI application factory for the web frontend.

Serves the static web UI and the /api routes.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from fastapi.responses import JSONResponse

from website.api.chat_routes import router as chat_router
from website.api.nexus import router as nexus_router
from website.api.routes import router as api_router
from website.api.sandbox_routes import router as sandbox_router
from website.api.zettels_routes import router as zettels_router
from website.features.summarization_engine.api import router as engine_v2_router
from website.features.user_pricing.routes import router as pricing_router
from website.features.web_monitor import router as web_monitor_router
from website.features.web_monitor.App_Errors import notify_app_error
from website.features.web_monitor._env_validation import (
    log_web_monitor_env_warnings,
)
from website.api.admin_routes import router as admin_router
from website.api import _memory_guard

logger = logging.getLogger("website.app")

STATIC_DIR = Path(__file__).parent / "static"
KG_DIR = Path(__file__).parent / "features" / "knowledge_graph"
MOBILE_DIR = Path(__file__).parent / "mobile"
AUTH_DIR = Path(__file__).parent / "features" / "user_auth"
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
HOME_DIR = Path(__file__).parent / "features" / "user_home"
USER_ZETTELS_DIR = Path(__file__).parent / "features" / "user_zettels"
BROWSER_CACHE_DIR = Path(__file__).parent / "features" / "browser_cache"
USER_KASTENS_DIR = Path(__file__).parent / "features" / "user_kastens"
USER_RAG_DIR = Path(__file__).parent / "features" / "user_rag"
USER_PRICING_DIR = Path(__file__).parent / "features" / "user_pricing"
FOOTER_DIR = Path(__file__).parent / "footer"
ABOUT_DIR = FOOTER_DIR / "about"
PRICING_DIR = FOOTER_DIR / "pricing"
NEXUS_DIR = Path(__file__).parent / "experimental_features" / "nexus"
SUMMARIZATION_ENGINE_DIR = Path(__file__).parent / "features" / "summarization_engine"
HEADER_DIR = Path(__file__).parent / "features" / "header"
_HEADER_PLACEHOLDER = "<!--ZK_HEADER-->"
_FOOTER_PLACEHOLDER = "<!--ZK_FOOTER-->"


def _render_with_shell(path: Path) -> HTMLResponse:
    """Read an HTML page and inject shared header and footer at their placeholders.

    Placeholders are the literal comments ``<!--ZK_HEADER-->`` and ``<!--ZK_FOOTER-->``.
    Re-reads on every request so live edits to the shared fragments show up without
    restart. Falls back to returning the raw page unchanged if a placeholder or
    fragment file is absent.
    """
    html = path.read_text(encoding="utf-8")
    if _HEADER_PLACEHOLDER in html:
        header_html = (HEADER_DIR / "header.html").read_text(encoding="utf-8")
        html = html.replace(_HEADER_PLACEHOLDER, header_html)
    if _FOOTER_PLACEHOLDER in html:
        footer_html = (FOOTER_DIR / "footer.html").read_text(encoding="utf-8")
        html = html.replace(_FOOTER_PLACEHOLDER, footer_html)
    return HTMLResponse(content=html)


# Backward-compat alias; keep callers working while incrementally migrating.
_render_with_header = _render_with_shell

# Regex to detect mobile user-agents
_MOBILE_RE = re.compile(
    r"Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile|mobile",
    re.IGNORECASE,
)


def _nexus_enabled() -> bool:
    raw_value = os.environ.get("NEXUS_ENABLED", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "")
    return bool(_MOBILE_RE.search(ua))


def _mount_static_if_exists(app: FastAPI, url: str, directory: Path, name: str) -> None:
    if directory.exists():
        app.mount(url, StaticFiles(directory=str(directory)), name=name)
    else:
        logger.info("Skipping missing static mount %s -> %s", url, directory)


def create_app(lifespan=None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        lifespan: Optional async context manager for startup/shutdown events.
                  Used by ``website.main`` for the proc-stats logger task.
    """
    kwargs = dict(
        title="Zettelkasten Summarizer",
        description="Summarize any link with AI",
        docs_url=None,
        redoc_url=None,
    )
    if lifespan is not None:
        kwargs["lifespan"] = lifespan

    app = FastAPI(**kwargs)
    nexus_enabled = _nexus_enabled()

    # WAVE-D WM-14: log a warning at boot for each unset SLACK_WEBHOOK_* env
    # var so missing webhook config is visible BEFORE the first event would
    # have fired. Non-fatal — channels degrade to log-only on missing vars.
    # m-3: suppress under pytest so the per-test ``create_app`` calls in the
    # mocked CI lane don't drown the log stream with stub-env warnings.
    if not os.getenv("PYTEST_CURRENT_TEST"):
        log_web_monitor_env_warnings()

    # WAVE-C 1c-A.4 (D-KG-8): payload compression with Accept-Encoding
    # negotiation. brotli-asgi serves br when supported, falls back to gzip,
    # else identity. Compresses /api/graph (often >100KB) by ~3-5x. Threshold
    # = 1024 bytes so tiny health-check responses skip compression.
    try:
        from brotli_asgi import BrotliMiddleware

        app.add_middleware(BrotliMiddleware, minimum_size=1024, quality=4)
    except ImportError:
        # Fallback: stdlib GZipMiddleware. Lower compression ratio but no
        # extra dep. Logged once at startup so the deploy bot can flag.
        from fastapi.middleware.gzip import GZipMiddleware

        app.add_middleware(GZipMiddleware, minimum_size=1024)
        logger.info("brotli-asgi unavailable — using GZipMiddleware fallback")

    # API routes
    app.include_router(api_router)
    app.include_router(zettels_router)
    app.include_router(engine_v2_router)
    app.include_router(chat_router)
    app.include_router(sandbox_router)
    app.include_router(pricing_router)
    app.include_router(web_monitor_router)
    app.include_router(admin_router)
    if nexus_enabled:
        app.include_router(nexus_router)
    # iter-03 mem-bounded §2.9: install AFTER routers so middleware wraps every route.
    _memory_guard.install(app)

    # iter-03 §B (2026-04-29): post-response aggressive memory release.
    # Runs gc.collect() + glibc malloc_trim(0) AFTER each response is sent
    # to the client (no user-perceived latency impact). Targets the
    # ~180-430 MB per-query native residual that gc alone cannot free
    # (ONNX internal buffers, Gemini/Supabase httpx body buffers, glibc
    # arena freelist). Exempts /api/health* and /favicon.* so cheap probes
    # don't pay the trim cost. Safe on non-glibc platforms (no-op).
    from website.api._mem_release import aggressive_release as _aggressive_release

    _RELEASE_EXEMPT_PREFIXES = (
        "/api/health",
        "/favicon.",
    )

    @app.middleware("http")
    async def _post_response_release(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in _RELEASE_EXEMPT_PREFIXES):
            try:
                _aggressive_release()
            except Exception:  # noqa: BLE001 — never let release break the response
                logger.exception("post-response release failed")
        return response

    # iter-03 §B (2026-04-29): convert intra-request stage-2 memory pressure
    # to a clean 503 with Retry-After. The middleware above only sees RSS at
    # request dispatch; once a query is admitted, stage-2 may discover the
    # baseline has crept above the ceiling (residual from prior queries on
    # this worker) and refuse to allocate the forward-pass tensors. That
    # raises MemoryPressureError; we convert here so eval/clients get the
    # same Retry-After=5 contract as the dispatch-time guard.
    from website.features.rag_pipeline.rerank.cascade import MemoryPressureError

    @app.exception_handler(MemoryPressureError)
    async def _on_memory_pressure(request: Request, exc: MemoryPressureError):
        logger.warning(
            "stage-2 memory pressure shedding: %s path=%s",
            exc, request.url.path,
        )
        return JSONResponse(
            {"error": "server_under_memory_pressure", "retry_after_seconds": 5},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    # ── Unhandled-exception alerting ──
    # Any uncaught error in a request handler fans out to the #app-errors
    # Slack channel via notify_app_error, then returns a generic 500 to the
    # client. Missing SLACK_WEBHOOK_APP_ERRORS falls back to a logged warning
    # (see post_to_slack); the handler itself never raises on Slack failure.
    @app.exception_handler(RequestValidationError)
    async def _on_request_validation_error(request: Request, exc: RequestValidationError):
        if request.url.path != "/api/zettels/add":
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            {
                "type": "https://zettelkasten.in/problems/errors/invalid-add-zettel-request",
                "title": "Invalid Add Zettel request",
                "status": 422,
                "detail": "The Add Zettel request body did not match the API contract.",
                "instance": "/api/zettels/add",
                "errors": jsonable_encoder(exc.errors()),
            },
            status_code=422,
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def _on_unhandled_exception(request: Request, exc: Exception):
        try:
            await notify_app_error(
                route=request.url.path,
                exc_type=type(exc).__name__,
                message=str(exc)[:400],
                request_id=request.headers.get("x-request-id"),
            )
        except Exception:  # noqa: BLE001 — never let alerting break the response path
            logger.exception("web_monitor: alert dispatch failed")
        logger.exception("Unhandled exception on %s", request.url.path)
        return JSONResponse({"error": "internal_server_error"}, status_code=500)

    # ── Mobile static assets (/m/) ──
    app.mount("/m/css", StaticFiles(directory=str(MOBILE_DIR / "css")), name="m-css")
    app.mount("/m/js", StaticFiles(directory=str(MOBILE_DIR / "js")), name="m-js")

    # ── Desktop static assets ──
    app.mount("/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")

    # Knowledge Graph static assets (shared by both mobile and desktop)
    app.mount("/kg/css", StaticFiles(directory=str(KG_DIR / "css")), name="kg-css")
    app.mount("/kg/js", StaticFiles(directory=str(KG_DIR / "js")), name="kg-js")
    app.mount("/kg/content", StaticFiles(directory=str(KG_DIR / "content")), name="kg-data")

    # User Auth static assets
    app.mount("/auth/css", StaticFiles(directory=str(AUTH_DIR / "css")), name="auth-css")
    app.mount("/auth/js", StaticFiles(directory=str(AUTH_DIR / "js")), name="auth-js")
    app.mount(
        "/browser-cache/js",
        StaticFiles(directory=str(BROWSER_CACHE_DIR / "js")),
        name="browser-cache-js",
    )

    # Home page static assets
    app.mount("/home/css", StaticFiles(directory=str(HOME_DIR / "css")), name="home-css")
    app.mount("/home/js", StaticFiles(directory=str(HOME_DIR / "js")), name="home-js")
    if nexus_enabled:
        _mount_static_if_exists(app, "/home/nexus/css", NEXUS_DIR / "css", "home-nexus-css")
        _mount_static_if_exists(app, "/home/nexus/js", NEXUS_DIR / "js", "home-nexus-js")
    app.mount(
        "/home/zettels/css",
        StaticFiles(directory=str(USER_ZETTELS_DIR / "css")),
        name="home-zettels-css",
    )
    app.mount(
        "/home/zettels/js",
        StaticFiles(directory=str(USER_ZETTELS_DIR / "js")),
        name="home-zettels-js",
    )
    app.mount(
        "/home/kastens/css",
        StaticFiles(directory=str(USER_KASTENS_DIR / "css")),
        name="home-kastens-css",
    )
    app.mount(
        "/home/kastens/js",
        StaticFiles(directory=str(USER_KASTENS_DIR / "js")),
        name="home-kastens-js",
    )
    app.mount(
        "/home/rag/css",
        StaticFiles(directory=str(USER_RAG_DIR / "css")),
        name="home-rag-css",
    )
    app.mount(
        "/home/rag/js",
        StaticFiles(directory=str(USER_RAG_DIR / "js")),
        name="home-rag-js",
    )
    _mount_static_if_exists(
        app,
        "/home/rag/content",
        USER_RAG_DIR / "content",
        "home-rag-content",
    )
    app.mount("/about/css", StaticFiles(directory=str(ABOUT_DIR / "css")), name="about-css")
    app.mount("/about/js", StaticFiles(directory=str(ABOUT_DIR / "js")), name="about-js")
    app.mount(
        "/pricing/css",
        StaticFiles(directory=str(PRICING_DIR / "css")),
        name="pricing-css",
    )
    app.mount("/pricing/js", StaticFiles(directory=str(PRICING_DIR / "js")), name="pricing-js")
    app.mount(
        "/user-pricing/css",
        StaticFiles(directory=str(USER_PRICING_DIR / "css")),
        name="user-pricing-css",
    )
    app.mount(
        "/user-pricing/js",
        StaticFiles(directory=str(USER_PRICING_DIR / "js")),
        name="user-pricing-js",
    )

    # Shared site header (single source of truth for inner-page header markup)
    app.mount("/header/css", StaticFiles(directory=str(HEADER_DIR / "css")), name="header-css")
    app.mount("/header/js", StaticFiles(directory=str(HEADER_DIR / "js")), name="header-js")

    # Shared artifacts (logos, icons, etc.)
    app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")
    app.mount(
        "/summarization-engine/css",
        StaticFiles(directory=str(SUMMARIZATION_ENGINE_DIR / "ui" / "css")),
        name="summarization-engine-css",
    )
    app.mount(
        "/summarization-engine/js",
        StaticFiles(directory=str(SUMMARIZATION_ENGINE_DIR / "ui" / "js")),
        name="summarization-engine-js",
    )

    # ── Favicon ──
    # Browsers auto-fetch /favicon.ico on every page load; without a route the
    # request 404s on every navigation (visible in the DevTools console).
    # Serve a single SVG asset for both /favicon.ico and /favicon.svg with
    # long browser cache headers — the icon never changes per request.
    _favicon_path = STATIC_DIR / "favicon.svg"

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon_ico():
        return FileResponse(
            str(_favicon_path),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg():
        return FileResponse(
            str(_favicon_path),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400, immutable"},
        )

    # ── Mobile routes ──
    @app.get("/m/")
    async def mobile_index():
        return FileResponse(str(MOBILE_DIR / "index.html"))

    @app.get("/m/knowledge-graph")
    async def mobile_knowledge_graph():
        return FileResponse(str(MOBILE_DIR / "knowledge-graph.html"))

    # ── Desktop routes (auto-redirect mobile browsers) ──
    @app.get("/")
    async def index(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(STATIC_DIR / "index.html")

    @app.get("/knowledge-graph")
    async def knowledge_graph(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/knowledge-graph", status_code=302)
        return _render_with_shell(KG_DIR / "index.html")

    @app.get("/auth/callback")
    async def auth_callback():
        return FileResponse(str(AUTH_DIR / "callback.html"))

    @app.get("/home")
    async def home(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(HOME_DIR / "index.html")

    if nexus_enabled:
        @app.get("/home/nexus")
        async def home_nexus(request: Request):
            if _is_mobile(request):
                return RedirectResponse(url="/m/", status_code=302)
            nexus_index = NEXUS_DIR / "index.html"
            if not nexus_index.exists():
                raise HTTPException(status_code=503, detail="Nexus UI assets are not available")
            return _render_with_shell(nexus_index)

    @app.get("/home/zettels")
    async def user_zettels(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(USER_ZETTELS_DIR / "index.html")

    @app.get("/home/kastens")
    async def user_kastens(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(USER_KASTENS_DIR / "index.html")

    @app.get("/home/rag")
    async def user_rag(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(USER_RAG_DIR / "index.html")

    @app.get("/summarization-engine")
    async def summarization_engine_dashboard(request: Request):
        return FileResponse(str(SUMMARIZATION_ENGINE_DIR / "ui" / "index.html"))

    @app.get("/about")
    async def about(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        return _render_with_shell(ABOUT_DIR / "index.html")

    @app.get("/pricing")
    async def pricing(request: Request):
        if _is_mobile(request):
            return RedirectResponse(url="/m/", status_code=302)
        # Fire-and-forget Slack alert (throttled per-IP inside the notifier).
        # Never awaited — we don't want Slack latency on the pricing page.
        try:
            import asyncio

            from website.features.web_monitor import notify_pricing_visit

            asyncio.get_running_loop().create_task(notify_pricing_visit(request))
        except Exception:  # noqa: BLE001 — alert must never break the page
            logger.exception("notify_pricing_visit scheduling failed")
        return _render_with_shell(PRICING_DIR / "index.html")

    return app
