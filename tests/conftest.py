"""Shared pytest fixtures for the website test suite.

WAVE-D Phase 1 additions:
  * ``slack_webhook_mock`` — respx-based mock for the 3 Slack webhook env vars
    consumed by ``website/features/web_monitor/`` (App_Errors, DO_Alerts,
    User_Activity). Supports forced 200/429/500 status + Retry-After header
    injection so backoff/circuit-breaker tests can drive deterministic paths
    without burning real Slack quota.
  * ``static_color_scan`` — regex helper that fails a test if the supplied
    CSS/HTML text contains banned purple/violet/lavender values OUTSIDE the
    ``/knowledge-graph`` scope. Enforces the "no purple anywhere except KG"
    rule from CLAUDE.md.
  * ``frozen_clock`` — freezegun wrapper anchored at 2026-05-12T00:00:00Z,
    yielding the FrozenDateTimeFactory so tests can ``.tick(timedelta(...))``
    without sleeping. Mirrors the v2-integration variant in
    ``tests/integration/v2/conftest.py`` so unit-level tests have the same
    surface available without pulling the v2-Supabase plumbing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pytest


def pytest_addoption(parser):
    try:
        parser.addoption(
            '--live',
            action='store_true',
            default=False,
            help='Run live API integration tests',
        )
    except ValueError as exc:
        if '--live' not in str(exc):
            raise
    try:
        parser.addoption(
            '--destructive',
            action='store_true',
            default=False,
            help='Run destructive tests (mutate shared state, e.g. delete users)',
        )
    except ValueError as exc:
        if '--destructive' not in str(exc):
            raise


@pytest.fixture(autouse=True)
def skip_live(request):
    if request.node.get_closest_marker('live') and not request.config.getoption('--live'):
        pytest.skip('Live test — pass --live to run')


@pytest.fixture(autouse=True)
def skip_destructive(request):
    if (
        request.node.get_closest_marker('destructive')
        and not request.config.getoption('--destructive')
    ):
        pytest.skip('Destructive test — pass --destructive to run')


@pytest.fixture
def sample_reddit_url() -> str:
    return "https://www.reddit.com/r/python/comments/abc123/test_post/"


@pytest.fixture
def sample_youtube_url() -> str:
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def sample_github_url() -> str:
    return "https://github.com/user/repo"


@pytest.fixture
def sample_newsletter_url() -> str:
    return "https://example.substack.com/p/my-post"


@pytest.fixture
def sample_generic_url() -> str:
    return "https://example.com/article"


# ---------------------------------------------------------------------------
# WAVE-D Phase 1 — shared infra fixtures
# ---------------------------------------------------------------------------


# Three Slack webhook env vars consumed by website/features/web_monitor/*.py.
# Stub URLs used inside respx — the routes match the real
# hooks.slack.com/services/<T>/<B>/<token> shape so production URLs would also
# route through the mock if accidentally injected. Kept short to avoid
# log-noise spam in test output.
_SLACK_WEBHOOK_ENV_VARS = (
    "SLACK_WEBHOOK_APP_ERRORS",
    "SLACK_WEBHOOK_DO_ALERT",
    "SLACK_WEBHOOK_USER_ACTIVITY",
)
_SLACK_STUB_URLS = {
    "SLACK_WEBHOOK_APP_ERRORS":
        "https://hooks.slack.com/services/TTESTAPP/BTESTAPP/tokAppErrors",
    "SLACK_WEBHOOK_DO_ALERT":
        "https://hooks.slack.com/services/TTESTDO/BTESTDO/tokDoAlert",
    "SLACK_WEBHOOK_USER_ACTIVITY":
        "https://hooks.slack.com/services/TTESTUA/BTESTUA/tokUserActivity",
}


@dataclass
class SlackWebhookRecorder:
    """Captured state from a ``slack_webhook_mock`` session.

    ``calls`` is keyed by env-var name (``SLACK_WEBHOOK_APP_ERRORS`` etc.) and
    holds the JSON payload posted on each call, in arrival order. Tests can
    assert call counts, payload shape, and ordering.

    ``router`` is the respx ``MockRouter`` so tests can register additional
    routes (e.g. an unexpected URL) without re-creating the fixture.
    """

    calls: dict[str, list[dict]] = field(default_factory=dict)
    router: object | None = None  # respx.MockRouter — typed loosely to dodge
    # the import at fixture-definition time (respx is dev-only).

    def total_calls(self) -> int:
        return sum(len(v) for v in self.calls.values())


@pytest.fixture
def slack_webhook_mock(monkeypatch):
    """Patch the 3 SLACK_WEBHOOK_* env vars + mock the resulting hooks.

    Yields a callable. Calling it with no args installs default
    ``200 OK`` responses for all three webhooks. Pass ``status=429`` (or any
    HTTP status), and optional ``retry_after`` seconds, to drive backoff
    paths::

        def test_429_retry(slack_webhook_mock):
            rec = slack_webhook_mock(status=429, retry_after=2)
            # ... drive code that posts to Slack ...
            assert rec.total_calls() >= 1

    The recorder tracks every payload so assertions can be written against
    the captured JSON bodies. The fixture is built on respx so it composes
    cleanly with other httpx-mocking patterns already in the suite.
    """
    import respx
    import httpx

    # Stub env vars BEFORE any web_monitor code reads them. Each call
    # to the factory below registers respx routes; the env vars stay
    # pointing at the stub URLs for the test's full lifetime.
    for env_name in _SLACK_WEBHOOK_ENV_VARS:
        monkeypatch.setenv(env_name, _SLACK_STUB_URLS[env_name])

    recorder = SlackWebhookRecorder(
        calls={env: [] for env in _SLACK_WEBHOOK_ENV_VARS}
    )

    router = respx.MockRouter(assert_all_called=False)
    recorder.router = router

    def _build_response(
        request: httpx.Request,
        *,
        env_name: str,
        status: int,
        retry_after: float | None,
    ) -> httpx.Response:
        # Best-effort JSON parse — Slack webhook payloads are always JSON
        # in this codebase but we tolerate non-JSON for forward-compat.
        try:
            payload = request.read()
            recorder.calls[env_name].append(
                __import__("json").loads(payload) if payload else {}
            )
        except Exception:  # noqa: BLE001 — recorder is best-effort
            recorder.calls[env_name].append({"_raw": True})
        headers: dict[str, str] = {}
        if retry_after is not None and status == 429:
            headers["Retry-After"] = str(retry_after)
        return httpx.Response(status, headers=headers, text="ok")

    def _factory(
        *,
        status: int = 200,
        retry_after: float | None = None,
    ) -> SlackWebhookRecorder:
        # Each call rebuilds routes so tests can toggle status mid-test by
        # calling the factory again with different kwargs.
        router.reset()
        for env_name, url in _SLACK_STUB_URLS.items():
            router.post(url).mock(
                side_effect=lambda req, _e=env_name: _build_response(
                    req, env_name=_e, status=status, retry_after=retry_after,
                )
            )
        return recorder

    with router:
        yield _factory


# ---------------------------------------------------------------------------
# static_color_scan — purple/violet/lavender guard
# ---------------------------------------------------------------------------


# Banned values per CLAUDE.md "No purple" rule:
#   * Named tokens: purple / violet / lavender (case-insensitive, word-bound)
#   * Tailwind/common hex: #A78BFA, #7C3AED
#   * Any HSL hue in [250, 290] — covers Indigo→Magenta band
# Allow-listed scopes: any file path containing /knowledge-graph (the 3D viz
# may use amber/gold which can occasionally read as warm-violet on diff
# tooling — we explicitly do NOT scan that surface). Caller can also pass
# additional allow-listed substrings via ``allow_paths``.
_PURPLE_NAMED = re.compile(r"\b(purple|violet|lavender)\b", re.IGNORECASE)
_PURPLE_HEX = re.compile(r"#(?:A78BFA|7C3AED)\b", re.IGNORECASE)
_PURPLE_HSL = re.compile(
    r"hsla?\(\s*(\d{1,3})(?:\.\d+)?\s*(?:,|\s)",
    re.IGNORECASE,
)


@dataclass
class ColorScanFinding:
    file: str
    line: int
    match: str
    rule: str  # "named" | "hex" | "hsl"


@pytest.fixture
def static_color_scan() -> Callable[..., list[ColorScanFinding]]:
    """Scan one or more CSS/HTML strings for banned purple values.

    Returns a callable: ``scan(text, *, source="inline", allow_paths=())``
    yielding a list of ``ColorScanFinding`` (empty list = clean). Tests
    typically assert ``not findings``.

    The ``source`` parameter is used to attribute findings; pass the file
    path when scanning files so failure messages are actionable. Pass
    ``allow_paths=("/knowledge-graph",)`` (default) to skip files whose
    source path contains the allow-listed substring — the 3D viz surface
    is the only place amber/gold is permitted, and its diff tooling can
    flag amber as warm-violet.
    """
    def _scan(
        text: str,
        *,
        source: str = "inline",
        allow_paths: Iterable[str] = ("/knowledge-graph",),
    ) -> list[ColorScanFinding]:
        # Allow-list short-circuit: if the source path contains any
        # allow-listed substring, skip entirely.
        norm_source = source.replace("\\", "/")
        if any(allow in norm_source for allow in allow_paths):
            return []

        findings: list[ColorScanFinding] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _PURPLE_NAMED.finditer(line):
                findings.append(
                    ColorScanFinding(
                        file=source, line=lineno, match=m.group(0), rule="named"
                    )
                )
            for m in _PURPLE_HEX.finditer(line):
                findings.append(
                    ColorScanFinding(
                        file=source, line=lineno, match=m.group(0), rule="hex"
                    )
                )
            for m in _PURPLE_HSL.finditer(line):
                try:
                    hue = int(m.group(1))
                except ValueError:
                    continue
                if 250 <= hue <= 290:
                    findings.append(
                        ColorScanFinding(
                            file=source,
                            line=lineno,
                            match=m.group(0),
                            rule="hsl",
                        )
                    )
        return findings

    return _scan


# ---------------------------------------------------------------------------
# frozen_clock — root-level (mirrors tests/integration/v2/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_clock():
    """Wrap each test in ``freezegun.freeze_time`` anchored at
    2026-05-12T00:00:00Z. Yields the FrozenDateTimeFactory so tests can
    advance time via ``frozen_clock.tick(timedelta(seconds=N))`` without
    sleeping.

    Mirror of the variant in ``tests/integration/v2/conftest.py`` — defined
    at root so unit-level tests have the same surface available without
    pulling in v2-Supabase plumbing.
    """
    from freezegun import freeze_time

    with freeze_time("2026-05-12T00:00:00Z") as frozen:
        yield frozen
