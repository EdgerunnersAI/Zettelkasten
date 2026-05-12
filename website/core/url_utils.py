"""URL validation, normalization, redirect resolution, and shortener detection."""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
    }
)

_SHORTENER_HOSTS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "is.gd",
        "buff.ly",
        "adf.ly",
        "shorturl.at",
        "rb.gy",
        "v.gd",
        "tiny.cc",
        "lnkd.in",
        "amzn.to",
        "youtu.be",
        "redd.it",
    }
)


def _canonicalize_host(host: str) -> str:
    """Normalize sloppy IPv4 / IPv6 forms to canonical text.

    ``socket.inet_aton`` accepts short-form (``127.1``), 32-bit decimal
    (``2130706433``), octal (``0177.0.0.1``), and hex (``0x7f000001``) — all
    of which ``ipaddress.ip_address`` rejects, leaving the SSRF allowlist
    blind. We canonicalize first so the downstream private-IP check sees
    the dotted-quad / RFC 5952 form.
    """
    if not host:
        return host

    # IPv6 literals arrive bracket-stripped from urllib's ``hostname``.
    try:
        packed6 = socket.inet_pton(socket.AF_INET6, host)
        return socket.inet_ntop(socket.AF_INET6, packed6)
    except (OSError, ValueError):
        pass

    try:
        packed4 = socket.inet_aton(host)
        return socket.inet_ntoa(packed4)
    except OSError:
        pass

    return host


def _is_private_ip(hostname: str) -> bool:
    canonical = _canonicalize_host(hostname)

    try:
        addr = ipaddress.ip_address(canonical)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        pass

    try:
        results = socket.getaddrinfo(canonical, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in results:
            ip_str = sockaddr[0]
            # IPv6 sockaddrs may carry a scope suffix (e.g. "fe80::1%eth0"); strip.
            ip_str = ip_str.split("%", 1)[0]
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return True
    except (socket.gaierror, OSError, ValueError):
        pass
    return False


def validate_url(url: str) -> bool:
    if url is None:
        raise TypeError("url must be a str, not None")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False

    hostname = parsed.hostname or ""
    if _is_private_ip(hostname):
        logger.warning("Blocked private/reserved IP in URL: %s", url)
        return False

    return True


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query_params if k.lower() not in _TRACKING_PARAMS]
    filtered.sort(key=lambda kv: kv[0])
    new_query = urllib.parse.urlencode(filtered)

    normalized = urllib.parse.urlunparse(
        (scheme, netloc, parsed.path, parsed.params, new_query, "")
    )
    return normalized


async def resolve_redirects(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            try:
                response = await client.head(url)
                if response.status_code >= 400:
                    response = await client.get(url)
            except httpx.UnsupportedProtocol:
                response = await client.get(url)
            return str(response.url)
    except httpx.TimeoutException:
        logger.warning("Timeout resolving redirects for %s — returning original URL", url)
        return url
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error resolving redirects for %s: %s — returning original URL", url, exc)
        return url


def is_shortener(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    return host in _SHORTENER_HOSTS
