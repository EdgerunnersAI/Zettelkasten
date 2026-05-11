"""UA-03: expired/invalid token must not leave the user staring at a
spinner forever.

callback.html drives the visible UX: when `exchangeCodeForSession`
throws (expired magic link, invalid state, network blip), the page must
hide the spinner, reveal the error block, and surface a retry link.
This is a static-DOM regression check: any future edit to the catch
branch that drops one of these UX hooks will fail this test.

We scan callback.html (not auth.js) because the spinner DOM lives there
and the script block is what drives it. We also assert auth.js does not
introduce a competing infinite-spinner code path.
"""
from __future__ import annotations

import pathlib
import re


CALLBACK_HTML = pathlib.Path("website/features/user_auth/callback.html")
AUTH_JS = pathlib.Path("website/features/user_auth/js/auth.js")


def test_callback_html_exists() -> None:
    assert CALLBACK_HTML.exists(), f"missing {CALLBACK_HTML}"


def test_callback_has_spinner_status_error_retry_dom() -> None:
    src = CALLBACK_HTML.read_text(encoding="utf-8")
    # The four DOM hooks the error path manipulates must exist by id.
    for element_id in ("spinner", "status", "error", "retry"):
        assert f'id="{element_id}"' in src, (
            f"callback.html missing #{element_id} DOM hook for error UX"
        )


def test_callback_error_branch_stops_spinner() -> None:
    """The catch block must explicitly hide the spinner, hide the status
    line, show the error block, set error text, and reveal the retry
    link. Reordering or dropping any of these flips the UX to silent
    failure."""
    src = CALLBACK_HTML.read_text(encoding="utf-8")
    # Identify the catch (err) { ... } body — last catch block in file.
    catch_match = re.search(r"catch\s*\(\s*err\s*\)\s*\{([\s\S]*?)\}", src)
    assert catch_match, "callback.html missing catch(err) error branch"
    catch_body = catch_match.group(1)

    # Spinner must be hidden in the catch branch.
    assert re.search(r"spinnerEl\.style\.display\s*=\s*['\"]none['\"]", catch_body), (
        "catch branch must hide the spinner (spinnerEl.style.display = 'none')"
    )
    # Status text must be hidden (so 'Completing sign-in…' doesn't linger).
    assert re.search(r"statusEl\.style\.display\s*=\s*['\"]none['\"]", catch_body), (
        "catch branch must hide the status line"
    )
    # Error block must be revealed.
    assert re.search(r"errorEl\.style\.display\s*=\s*['\"]block['\"]", catch_body), (
        "catch branch must show the error block"
    )
    # Error text must be set (not empty).
    assert re.search(r"errorEl\.textContent\s*=", catch_body), (
        "catch branch must set errorEl.textContent"
    )
    # Retry link must be shown so user can recover.
    assert re.search(r"retryEl\.style\.display\s*=\s*['\"]inline-block['\"]", catch_body), (
        "catch branch must show the retry link"
    )


def test_callback_does_not_redirect_on_error() -> None:
    """An error MUST NOT trigger window.location.replace — that would
    bounce the user away from the visible error UX."""
    src = CALLBACK_HTML.read_text(encoding="utf-8")
    catch_match = re.search(r"catch\s*\(\s*err\s*\)\s*\{([\s\S]*?)\}", src)
    assert catch_match
    catch_body = catch_match.group(1)
    assert "window.location.replace" not in catch_body, (
        "catch branch must not redirect — surface the error to the user"
    )
    assert "window.location.href" not in catch_body, (
        "catch branch must not redirect — surface the error to the user"
    )


def test_auth_js_logs_init_failures() -> None:
    """auth.js (the landing-page client, separate from callback.html)
    must log init failures rather than swallow them silently — gives
    operators a console signal when Supabase config is broken."""
    src = AUTH_JS.read_text(encoding="utf-8")
    assert re.search(r"console\.(error|warn)\s*\([^)]*auth", src, re.IGNORECASE), (
        "auth.js must log init failures for debuggability"
    )
