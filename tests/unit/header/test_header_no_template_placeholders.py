"""HD-02 (reframed) — regression guard against tainted template placeholders.

Phase 0 discovery flagged: ``header.html`` has NO Jinja/f-string/JS-template
fields today. There is no live taint surface. HD-02 is therefore reframed as
a regression guard — a future commit MUST NOT introduce un-escaped templated
fields into the shared header fragment without also adding explicit XSS
mitigations.

If a templated placeholder ever needs to land here, the fix is to (a) escape
it server-side AND (b) update this test with a justified allow-list.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


HEADER_HTML = (
    Path(__file__).resolve().parents[3]
    / "website"
    / "features"
    / "header"
    / "header.html"
)


# Jinja2 / Liquid / Handlebars / Mustache / JS-template tokens. The single
# allow-listed exception is ``<!--ZK_HEADER-->`` — that is the placeholder
# in OTHER files where this fragment is injected, not in this file itself.
_PLACEHOLDER_PATTERNS = [
    (re.compile(r"\{\{[^}]+\}\}"), "jinja-mustache `{{ ... }}`"),
    (re.compile(r"\$\{[^}]+\}"), "js-template `${ ... }`"),
    (re.compile(r"\{%[^%]+%\}"), "jinja-block `{% ... %}`"),
]


def test_header_html_exists():
    assert HEADER_HTML.exists(), f"header.html missing at {HEADER_HTML}"


def test_header_html_has_no_template_placeholders():
    """Regression guard: header.html must contain no taintable placeholders.

    See module docstring for the reframe rationale.
    """
    text = HEADER_HTML.read_text(encoding="utf-8")
    findings = []
    for pattern, label in _PLACEHOLDER_PATTERNS:
        for m in pattern.finditer(text):
            findings.append((label, m.group(0)))
    assert not findings, (
        "header.html contains templated placeholder(s). If a dynamic field "
        "MUST be injected here, escape it server-side AND extend this test's "
        "allow-list with a comment justifying the new field. Findings: "
        f"{findings!r}"
    )


def test_header_html_avatar_button_has_aria_menubutton_attrs():
    """D-4 surgical fix is in place: aria-haspopup + aria-expanded on button.

    WAI-ARIA 1.2 menubutton pattern requires both attributes on the trigger.
    This pins them so a future style cleanup cannot silently drop them.
    """
    text = HEADER_HTML.read_text(encoding="utf-8")
    # Single avatar button line — match the attributes anywhere on it.
    button_match = re.search(
        r'<button[^>]*id="avatar-btn"[^>]*>', text, re.DOTALL,
    )
    assert button_match, "avatar button not found in header.html"
    btn_tag = button_match.group(0)
    assert 'aria-haspopup="menu"' in btn_tag, (
        f"avatar button missing aria-haspopup=\"menu\": {btn_tag!r}"
    )
    assert 'aria-expanded="false"' in btn_tag, (
        f"avatar button missing initial aria-expanded=\"false\": {btn_tag!r}"
    )


def test_header_html_dropdown_has_role_menu():
    text = HEADER_HTML.read_text(encoding="utf-8")
    drop_match = re.search(
        r'<div[^>]*id="avatar-dropdown"[^>]*>', text, re.DOTALL,
    )
    assert drop_match, "avatar dropdown not found in header.html"
    assert 'role="menu"' in drop_match.group(0), (
        "dropdown container missing role=\"menu\" — WAI-ARIA 1.2 menubutton"
    )


def test_header_html_menu_items_have_role_menuitem():
    """Each interactive child of #avatar-dropdown must declare role=menuitem.

    Verifies the WAI-ARIA 1.2 menu pattern: every focusable item carries
    role=menuitem. We assert per-anchor / per-button that the role token
    appears in its open tag.
    """
    text = HEADER_HTML.read_text(encoding="utf-8")
    # Extract the dropdown subtree to scope the assertion.
    sub = re.search(
        r'<div[^>]*id="avatar-dropdown"[^>]*>(.*?)</div>\s*</div>',
        text,
        re.DOTALL,
    )
    assert sub, "could not isolate dropdown subtree"
    body = sub.group(1)
    # Look at every <a class="home-dropdown-item"...> and <button
    # class="home-dropdown-item"...> tag. The divider <div> is not interactive.
    item_tags = re.findall(
        r'<(?:a|button)[^>]*class="[^"]*home-dropdown-item[^"]*"[^>]*>', body,
    )
    assert item_tags, "no dropdown items found"
    missing = [t for t in item_tags if 'role="menuitem"' not in t]
    assert not missing, (
        f"dropdown items missing role=\"menuitem\": {missing!r}"
    )
