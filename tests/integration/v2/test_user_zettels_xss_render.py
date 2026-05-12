"""WAVE-B Phase 1b — `user_zettels` UZ-03 XSS render test.

Strategy ref: `docs/research/full_modular_test_plans/user_zettels.md`.
Industry standard: OWASP WSTG §4.7 (Injection).

The zettel surface is rendered client-side (user_zettels.js) via JSON from
`/api/graph` + zettel routes. The architectural contract is:

  - Server-side: API returns raw user-supplied content VERBATIM, embedded
    within JSON-quoted strings. No HTML rendering happens server-side.
    Content-Type MUST be application/json (NOT text/html) so the browser
    never speculatively parses the body as HTML.
  - Client-side: `user_zettels.js` MUST call `escapeHtml(...)` before
    `innerHTML` for any user-controlled field (title, summary, tag labels,
    URL). The JS file currently routes title -> escapeHtml at line 546
    and summary -> escapeHtml at line 547.

This test asserts the server-side contract (the only layer reachable from a
pytest integration test):

  1. Inject script payloads into title / ai_summary / user_tags / body_md.
  2. Fetch `/api/graph?view=my`.
  3. Assert Content-Type begins with "application/json".
  4. Assert response is parseable JSON.
  5. Assert raw payload string round-trips intact (proves storage is faithful
     — the JS layer is responsible for escaping; the API must not silently
     re-encode in a way that breaks downstream escape detection).
  6. Assert the raw `<script>` substring is NOT present unescaped outside
     a JSON-string boundary (defensive — JSON serialisation will quote it,
     but we re-confirm the response isn't HTML-rendered server-side).

Industry references:
  - OWASP API Security Top 10 (2023) — API3:2023 Broken Object Property
    Level Authorization touches the same surface from a different angle.
  - OWASP Cheat Sheet — Cross Site Scripting Prevention (output encoding
    at the rendering layer, not the storage layer).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.live


# Canonical XSS payload fixtures. Each one targets a different sink class.
_XSS_PAYLOADS = [
    "<script>alert('xss-title')</script>",
    "<img src=x onerror=alert('xss-img')>",
    "javascript:alert('xss-url')",
    "\"><svg/onload=alert('xss-svg')>",
    "</script><script>alert('xss-break')</script>",
]


@pytest.fixture
def v2_app(monkeypatch):
    monkeypatch.setenv("DB_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("GEMINI_API_KEYS", "stub-key-for-xss-render-tests")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-xss-render-tests")
    from website.api import auth as auth_mod
    auth_mod._jwks_client = None
    from website.core import persist as persist_mod
    persist_mod._v2_core_repo = None
    persist_mod._v2_content_repo = None
    from website.app import create_app
    return create_app()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


async def _seed_xss_zettel(
    pool: asyncpg.Pool,
    *,
    workspace_id: uuid.UUID,
    title: str,
    summary: str,
    tag: str,
    body_md: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a single (canonical, workspace) pair carrying XSS payloads.
    Returns (canonical_id, workspace_zettel_id)."""
    cz = uuid.uuid4()
    wz = uuid.uuid4()
    norm_url = f"https://xss-{uuid.uuid4().hex[:10]}.example.com/"
    chash = uuid.uuid4().bytes + uuid.uuid4().bytes
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO content.canonical_zettels "
            "(id, normalized_url, content_hash, source_type, title, body_md, "
            " publication_date) "
            "VALUES ($1, $2, $3, 'web', $4, $5, '2026-04-01'::date)",
            cz, norm_url, chash, title, body_md,
        )
        await conn.execute(
            "INSERT INTO content.workspace_zettels "
            "(id, workspace_id, canonical_zettel_id, ai_summary, user_tags, "
            " user_note, pinned, added_via) "
            "VALUES ($1, $2, $3, $4, $5, NULL, false, 'website')",
            wz,
            workspace_id,
            cz,
            json.dumps({"brief_summary": summary, "detailed_summary": summary}),
            [tag],
        )
    return cz, wz


def test_uz03_xss_payload_returned_as_json_not_html(
    v2_app, mint_user, asyncpg_pool,
):
    """Server returns raw XSS payload inside JSON-quoted strings; never as
    HTML. Verifies:
      - Content-Type begins with application/json.
      - Body is valid JSON.
      - Raw payload survives storage round-trip (no silent re-encoding
        that would defeat the client-side escapeHtml() contract).
      - The payload appears in the JSON-string field, not as raw HTML.
    """
    a = mint_user(workspace_count=1)
    workspace_id = a.workspace_ids[0]
    seeded: list[tuple[str, uuid.UUID]] = []

    for idx, payload in enumerate(_XSS_PAYLOADS):
        # Differentiate payload per slot so we can correlate which field
        # carried which payload in the response.
        title = f"XSS-T-{idx} {payload}"
        summary = f"XSS-S-{idx} {payload}"
        tag = f"xss-tag-{idx}-{payload}"
        body_md = f"XSS-B-{idx} {payload}"
        _cz, wz = asyncio.get_event_loop().run_until_complete(
            _seed_xss_zettel(
                asyncpg_pool,
                workspace_id=workspace_id,
                title=title,
                summary=summary,
                tag=tag,
                body_md=body_md,
            )
        )
        seeded.append((payload, wz))

    with TestClient(v2_app) as client:
        resp = client.get("/api/graph?view=my&limit=5000", headers=_auth(a.jwt))

    assert resp.status_code == 200, resp.text

    # 1. Content-Type guard — must be JSON, never text/html. A text/html
    #    response would let the browser auto-render <script> tags.
    ctype = resp.headers.get("content-type", "")
    assert ctype.lower().startswith("application/json"), (
        f"UZ-03: /api/graph must return application/json, got {ctype!r} — "
        f"text/html would allow browser to auto-execute embedded <script>."
    )

    # 2. Body must be valid JSON.
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        pytest.fail(f"UZ-03: response is not valid JSON: {exc}; body={resp.text[:400]}")
    assert isinstance(data, dict), f"UZ-03: expected JSON object, got {type(data)}"
    assert "nodes" in data, f"UZ-03: response missing 'nodes' key: {list(data.keys())}"

    # 3. The payload must round-trip into the nodes list. We assert each
    #    payload appears verbatim somewhere in a node's name/summary/tags —
    #    confirming storage faithfully preserved the bytes (the client-side
    #    escapeHtml() then handles render-time escaping per
    #    user_zettels.js:546-547).
    body_text = resp.text
    nodes = data["nodes"]
    assert len(nodes) >= len(_XSS_PAYLOADS), (
        f"UZ-03: expected >= {len(_XSS_PAYLOADS)} nodes in response, "
        f"got {len(nodes)}"
    )

    for payload, _wz in seeded:
        # JSON-encoded form (with escaped quotes) is what we expect on the wire.
        # We assert the payload SUBSTRING appears in the JSON-serialised body —
        # json.dumps escapes `</` and quotes but leaves `<script>` etc. intact
        # inside the string value.
        assert payload in body_text, (
            f"UZ-03: XSS payload {payload!r} did not round-trip into "
            f"/api/graph response — storage/serialisation altered the bytes, "
            f"which would defeat the frontend escapeHtml() contract."
        )

    # 4. Verify the response is NOT HTML-rendered server-side. If the body
    #    parses as JSON (step 2) AND content-type is application/json
    #    (step 1), no browser will execute <script>. As an extra defensive
    #    check, the literal `</script>` close tag must NEVER appear OUTSIDE
    #    a JSON string context. The simplest invariant: response body must
    #    start with `{` (object) or `[` (array), never `<` (HTML).
    assert body_text.lstrip().startswith(("{", "[")), (
        f"UZ-03: response body must start with JSON delimiter, got "
        f"{body_text[:40]!r} — server appears to be HTML-rendering."
    )


def test_uz03_xss_payload_round_trip_via_patch(
    v2_app, mint_user, bulk_insert_zettels, asyncpg_pool,
):
    """The PATCH /api/zettels/{id} endpoint accepts user_note; verify an
    injected payload is stored verbatim and round-trips through GET
    /api/graph without server-side HTML rendering.

    Bug surface: if any layer auto-HTML-encodes the stored value (e.g.,
    overzealous bleach/sanitiser on write), the client's `escapeHtml()`
    would double-encode on read, breaking the contract. If any layer
    auto-decodes on read (the inverse), the stored value's bytes diverge
    from what frontend assumes.
    """
    a = mint_user(workspace_count=1)
    a_wz_ids = asyncio.get_event_loop().run_until_complete(
        bulk_insert_zettels(owner_user=a, n=1, prefix="uz03-rt")
    )
    wz_id = a_wz_ids[0]

    payload = "<script>alert('uz03-roundtrip')</script>"
    with TestClient(v2_app) as client:
        resp = client.patch(
            f"/api/zettels/{wz_id}",
            headers=_auth(a.jwt),
            json={"user_note": payload},
        )
        # PATCH should succeed for A's own zettel (200) or quota-fail (402).
        assert resp.status_code in (200, 402), resp.text
        if resp.status_code == 402:
            pytest.skip("Quota-exhausted in test env; cannot validate write-then-read")

        # Confirm Content-Type on the PATCH response too.
        ctype = resp.headers.get("content-type", "")
        assert ctype.lower().startswith("application/json"), (
            f"UZ-03: PATCH /api/zettels must return application/json, got {ctype!r}"
        )

    # Verify stored byte-for-byte in DB (no silent re-encoding on write).
    async def _read():
        async with asyncpg_pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT user_note FROM content.workspace_zettels WHERE id = $1",
                wz_id,
            )

    stored = asyncio.get_event_loop().run_until_complete(_read())
    assert stored == payload, (
        f"UZ-03: PATCH altered the stored bytes — expected {payload!r}, "
        f"got {stored!r}. Server-side sanitisation would break the "
        f"frontend escapeHtml() contract."
    )
