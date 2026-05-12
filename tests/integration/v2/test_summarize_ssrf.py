"""SE-01: SSRF / URL-safety fuzz on the summarization engine entry points.

The orchestrator's ``summarize_url_bundle`` is the only entry that performs
URL validation (via ``website.core.url_utils.validate_url``); every API
route bottoms out there. We attack the validator + ``detect_source_type``
with the canonical SSRF / URL-smuggling payload set:

  * ``file://``, ``gopher://``, ``ftp://``, ``data:``, ``ldap://`` schemes
    must be rejected outright (only http/https allowed).
  * IPv4 private ranges (10/8, 172.16/12, 192.168/16), loopback (127/8),
    link-local (169.254/16), reserved (0.0.0.0, 240/4), CGNAT (100.64/10
    is reserved) — must be blocked.
  * IPv6 loopback ``[::1]``, link-local ``[fe80::]``, IPv4-mapped IPv6
    ``[::ffff:127.0.0.1]`` must be blocked.
  * DNS-rebinding payloads (hostnames that resolve to private IPs) — we
    monkey-patch ``socket.getaddrinfo`` to simulate the rebind.
  * Hostnames bearing private IPs in URL-encoded / decimal-encoded /
    octal / hex forms must still be detected.

Per CLAUDE.md production-change discipline: tests only — no production
code is altered. If a payload slips past ``validate_url``, that is a
real SSRF bug and the test will fail loudly.
"""
from __future__ import annotations

import socket
from typing import Iterable
from unittest.mock import patch

import pytest

from website.core.url_utils import _is_private_ip, validate_url
from website.features.summarization_engine.core.errors import RoutingError
from website.features.summarization_engine.core.orchestrator import summarize_url_bundle
from website.features.summarization_engine.core.router import detect_source_type
from website.features.summarization_engine.core.models import SourceType


# --- Static rejection: non-http(s) schemes ---------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "file://C:/Windows/System32/drivers/etc/hosts",
        "gopher://evil.example.com:11211/_metadata",
        "ftp://internal.example.com/secrets.txt",
        "data:text/html,<script>alert(1)</script>",
        "ldap://localhost:389/cn=admin",
        "javascript:alert(1)",
        "ssh://user@10.0.0.1/",
        "dict://internal:11211/",
        "jar:http://evil/path!/",
    ],
)
def test_validate_url_rejects_non_http_schemes(url: str) -> None:
    assert validate_url(url) is False, f"unsafe scheme accepted: {url!r}"


# --- Static rejection: IPv4 private / reserved hosts -----------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS!
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_validate_url_rejects_private_and_reserved_hosts(url: str) -> None:
    assert validate_url(url) is False, f"private/reserved host accepted: {url!r}"


# Sloppy IPv4 / IPv6 SSRF vectors: short-form ("127.1"), 32-bit decimal
# ("2130706433" == 127.0.0.1), octal ("0177.0.0.1"), and hex ("0x7f000001")
# all parse as 127.0.0.1 via ``socket.inet_aton`` per RFC 3986 host syntax,
# but Python's ``ipaddress.ip_address`` rejects them. Without canonicalising
# first the private-IP allowlist is blind to them. The fix in
# ``website.core.url_utils._canonicalize_host`` runs ``inet_aton`` /
# ``inet_pton(AF_INET6)`` before the private-IP check. Cf. CVE-2017-3735,
# GHSA-w7rc-rwvf-8q5r for prior-art on this vector.
@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/admin",          # short-form loopback
        "http://2130706433/",          # 32-bit decimal == 127.0.0.1
        "http://0177.0.0.1/",          # octal 0177 == 127
        "http://0x7f000001/",          # hex 0x7f000001 == 127.0.0.1
        "http://[::1]/",               # IPv6 loopback
        "http://[fe80::1]/",           # IPv6 link-local
    ],
)
def test_validate_url_blocks_sloppy_ipv4(url: str) -> None:
    assert validate_url(url) is False, f"sloppy-IPv4/IPv6 SSRF vector accepted: {url!r}"


# --- Empty / malformed --------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://",
        "https://",
        "http:///nopath",
        "://no.scheme",
    ],
)
def test_validate_url_rejects_empty_or_malformed(url: str) -> None:
    assert validate_url(url) is False


def test_validate_url_none_raises() -> None:
    with pytest.raises(TypeError):
        validate_url(None)  # type: ignore[arg-type]


# --- DNS-rebinding: hostname that resolves to private IP --------------------

class _FakeAddrInfo:
    """Stub of ``socket.getaddrinfo`` returning a single private IPv4."""

    def __init__(self, ip: str) -> None:
        self.ip = ip

    def __call__(self, host, *_args, **_kwargs):
        # Mimic the 5-tuple shape of getaddrinfo: (family, type, proto,
        # canonname, sockaddr=(ip, port)).
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.ip, 0))]


@pytest.mark.parametrize(
    "private_ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # AWS IMDS
    ],
)
def test_validate_url_blocks_dns_rebind_to_private(private_ip: str) -> None:
    """Public hostname that resolves to a private IP must be blocked.

    Without DNS resolution the validator could be fooled by attacker-
    controlled hostnames pointing at internal endpoints. We assert that
    ``_is_private_ip`` actually performs the lookup and rejects.
    """
    with patch(
        "website.core.url_utils.socket.getaddrinfo",
        side_effect=_FakeAddrInfo(private_ip),
    ):
        assert _is_private_ip("totally-public-looking.example.com") is True
        assert validate_url("https://totally-public-looking.example.com/") is False


def test_validate_url_allows_legit_public_host() -> None:
    """Sanity check: public host that resolves to a public IP is allowed."""

    def _public(*_a, **_kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]

    with patch("website.core.url_utils.socket.getaddrinfo", side_effect=_public):
        assert validate_url("https://example.com/path") is True


# --- Orchestrator wiring: blocked URLs raise RoutingError before Gemini -----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
async def test_orchestrator_rejects_unsafe_url_before_ingest(url: str) -> None:
    """``summarize_url_bundle`` must raise ``RoutingError`` BEFORE invoking
    any ingestor / Gemini call when ``validate_url`` returns False. We pass
    a sentinel object as ``gemini_client`` and as ``user_id`` — a leak past
    the validator would crash with AttributeError on the wrong path."""
    import uuid

    sentinel_user = uuid.UUID("00000000-0000-0000-0000-000000000001")

    class _ExplodingClient:
        def __getattr__(self, name):  # noqa: D401
            raise AssertionError(
                f"validation slipped: gemini_client.{name} was accessed for {url!r}"
            )

    with pytest.raises(RoutingError):
        await summarize_url_bundle(
            url,
            user_id=sentinel_user,
            gemini_client=_ExplodingClient(),
        )


# --- Detection layer must not crash on hostile input ------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "http://",
        "://",
        "http:///nopath",
        "file:///etc/passwd",
        "data:text/html,XXX",
        "http://[::1]/",
    ],
)
def test_detect_source_type_safe_on_garbage(url) -> None:
    # Skip None — detect_source_type guards on falsy first.
    out = detect_source_type(url) if url is not None else detect_source_type("")
    assert isinstance(out, SourceType)
