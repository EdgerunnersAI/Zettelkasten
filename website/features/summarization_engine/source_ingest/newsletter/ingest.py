"""Newsletter ingestor with paywall bypass and structural signal extraction."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from website.features.summarization_engine.core.errors import NewsletterURLUnreachable
from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.source_ingest.base import BaseIngestor
from website.features.summarization_engine.source_ingest.newsletter.conclusions import (
    extract_conclusions,
)
from website.features.summarization_engine.source_ingest.newsletter.cta import (
    extract_ctas,
)
from website.features.summarization_engine.source_ingest.newsletter.preheader import (
    extract_preheader,
)
from website.features.summarization_engine.source_ingest.newsletter.site_extractors import (
    StructuredNewsletter,
    extract_structured,
)
from website.features.summarization_engine.source_ingest.newsletter.stance import (
    classify_stance,
)
from website.features.summarization_engine.source_ingest.utils import (
    DEFAULT_TIMEOUT,
    extract_html_text,
    join_sections,
    utc_now,
)

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
_FACEBOOK_UA = "facebookexternalhit/1.1"

_PROVIDER_ORDER = (
    "googlebot",
    "wayback",
    "archive_ph",
    "freedium",
)
# 2026-05-13: dropped "twelveft" — 12ft.io decommissioned July 2025.

_PREFLIGHT_TIMEOUT = 8.0
_PREFLIGHT_GET_RANGE = "bytes=0-256"
# Exponential backoff delays (seconds) between preflight retry attempts.
# 1-2-4 keeps total worst-case latency below the pipeline budget while still
# tolerating transient transport failures. Surfaces as fail-open (raise) only
# after all three tries exhaust.
_PREFLIGHT_BACKOFFS = (1.0, 2.0, 4.0)


async def _preflight_probe(url: str) -> None:
    """Probe a URL with HEAD-then-ranged-GET, retrying with exponential backoff.

    Raises ``NewsletterURLUnreachable`` only after all backoff attempts fail
    for DNS failure, connection errors, timeouts, or any final HTTP status
    >= 400. Returns silently on first success. Each attempt is bounded by
    ``_PREFLIGHT_TIMEOUT`` so very slow URLs are treated as unreachable.
    """
    import asyncio as _asyncio

    if not url or not isinstance(url, str):
        raise NewsletterURLUnreachable(url or "", None, "empty_url")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise NewsletterURLUnreachable(url, None, "malformed_url")

    headers = {"User-Agent": _DEFAULT_UA}
    last_error: NewsletterURLUnreachable | None = None
    attempts = (0.0,) + _PREFLIGHT_BACKOFFS  # first try immediate, then backoffs
    for idx, delay in enumerate(attempts):
        if delay:
            await _asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(
                timeout=_PREFLIGHT_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as client:
                try:
                    resp = await client.head(url)
                except httpx.HTTPError:
                    resp = None
                status = resp.status_code if resp is not None else None
                if resp is None or status is None or status >= 400:
                    # Some servers disallow HEAD (403/405) or mis-report it;
                    # fall back to a ranged GET to confirm liveness.
                    get_resp = await client.get(
                        url,
                        headers={**headers, "Range": _PREFLIGHT_GET_RANGE},
                    )
                    status = get_resp.status_code
                    if status >= 400:
                        raise NewsletterURLUnreachable(
                            url,
                            status,
                            f"http_{status}",
                        )
            # Success on this attempt.
            if idx > 0:
                logger.info(
                    "Newsletter preflight recovered after %d retries for %s",
                    idx, url,
                )
            return
        except NewsletterURLUnreachable:
            # HTTP-status failures are authoritative (4xx/5xx both). Do not
            # retry — the server responded and told us the URL is unreachable
            # for this request. Backoff only covers transport-level failures.
            raise
        except httpx.TimeoutException as exc:
            last_error = NewsletterURLUnreachable(
                url, None, f"timeout: {exc.__class__.__name__}"
            )
        except httpx.ConnectError as exc:
            last_error = NewsletterURLUnreachable(
                url, None, f"dns_or_connect: {exc}"
            )
        except httpx.HTTPError as exc:
            last_error = NewsletterURLUnreachable(
                url, None, f"http_error: {exc.__class__.__name__}"
            )
    if last_error is not None:
        raise last_error


class NewsletterIngestor(BaseIngestor):
    source_type = SourceType.NEWSLETTER

    async def ingest(self, url: str, *, config: dict[str, Any]) -> IngestResult:
        if bool(config.get("preflight_probe_enabled", True)):
            await _preflight_probe(url)

        min_len = int(config.get("min_text_length", 500))
        use_googlebot = bool(config.get("googlebot_ua", True))
        fallback_names: list[str] = list(
            config.get("paywall_fallbacks") or _PROVIDER_ORDER
        )

        best_text = ""
        best_meta: dict[str, Any] = {}
        best_final_url = url
        best_provider = "direct"
        best_html = ""

        direct_ua = _GOOGLEBOT_UA if use_googlebot else _DEFAULT_UA
        direct_text, direct_meta, direct_final, direct_html = await _fetch_and_extract(
            url,
            headers={"User-Agent": direct_ua},
        )
        if direct_text:
            best_text = direct_text
            best_meta = direct_meta
            best_final_url = direct_final
            best_html = direct_html
            logger.info(
                "[newsletter] direct fetch len=%d ua=%s url=%s",
                len(direct_text),
                "googlebot" if use_googlebot else "browser",
                url,
            )

        if len(best_text) < min_len:
            for name in fallback_names:
                if name == "googlebot" and use_googlebot:
                    continue
                try:
                    provider_text, provider_meta, provider_final, provider_html = (
                        await _try_provider(name, url)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[newsletter] provider %s failed for %s: %s",
                        name,
                        url,
                        exc,
                    )
                    continue

                if provider_text and len(provider_text) > len(best_text):
                    logger.info(
                        "[newsletter] provider=%s recovered len=%d (prev %d) url=%s",
                        name,
                        len(provider_text),
                        len(best_text),
                        url,
                    )
                    best_text = provider_text
                    best_meta = {**provider_meta, **{k: v for k, v in best_meta.items() if v}}
                    best_final_url = provider_final or best_final_url
                    best_provider = name
                    best_html = provider_html
                    if len(best_text) >= min_len:
                        break

        body_text = best_text
        structured = _structured_newsletter(best_html, url, config)
        preheader = (
            extract_preheader(
                best_html,
                fallback_chars=int(config.get("preheader_fallback_chars", 150)),
            )
            if best_html
            else ""
        )
        ctas = (
            extract_ctas(
                best_html,
                keyword_regex=config.get(
                    "cta_keyword_regex",
                    "subscribe|sign up|learn more",
                ),
                max_count=int(config.get("cta_max_count", 5)),
            )
            if best_html
            else []
        )
        conclusions = extract_conclusions(
            body_text,
            tail_fraction=float(config.get("conclusions_tail_fraction", 0.3)),
            prefixes=list(config.get("conclusions_prefixes", [])),
            max_count=int(config.get("conclusions_max_count", 6)),
        )
        detected_stance = await _detect_stance(body_text, url, config)

        sections = {"Article": body_text}
        if structured.site != "unknown":
            sections["Title"] = structured.title
            if structured.subtitle:
                sections["Subtitle"] = structured.subtitle
            if len(structured.body_text) > len(body_text):
                body_text = structured.body_text
                sections["Article"] = structured.body_text

        if preheader:
            sections["Preheader"] = preheader
        if ctas:
            sections["CTAs"] = "\n".join(f"- {cta.text} ({cta.href})" for cta in ctas)
        if conclusions:
            sections["Conclusions"] = "\n".join(f"- {item}" for item in conclusions)

        metadata = {
            **best_meta,
            "paywall_provider": best_provider,
            "site": structured.site,
            "publication_identity": structured.publication_identity,
            "preheader": preheader,
            "cta_count": len(ctas),
            "conclusions_count": len(conclusions),
            "detected_stance": detected_stance,
        }
        raw_text = join_sections(sections)

        confidence = (
            "high" if len(body_text) >= min_len else "low" if not body_text else "medium"
        )
        reason = (
            f"HTML article text extracted via {best_provider}"
            if body_text
            else "all paywall-bypass providers failed"
        )

        return IngestResult(
            source_type=self.source_type,
            url=best_final_url or url,
            original_url=url,
            raw_text=raw_text,
            sections=sections,
            metadata=metadata,
            extraction_confidence=confidence,
            confidence_reason=reason,
            fetched_at=utc_now(),
        )


async def _fetch_and_extract(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, dict[str, Any], str, str]:
    """Fetch one URL and return extracted text, metadata, final URL, and raw HTML."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return "", {}, str(response.url), ""
            html = response.text
            final_url = str(response.url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[newsletter] fetch failed for %s: %s", url, exc)
        return "", {}, url, ""

    text, metadata = extract_html_text(html)
    return text, metadata, final_url, html


async def _try_provider(name: str, url: str) -> tuple[str, dict[str, Any], str, str]:
    if name == "googlebot":
        return await _fetch_and_extract(url, headers={"User-Agent": _GOOGLEBOT_UA})
    if name == "facebook":
        return await _fetch_and_extract(url, headers={"User-Agent": _FACEBOOK_UA})
    if name == "wayback":
        return await _wayback(url)
    if name == "archive_ph":
        return await _archive_ph(url)
    if name == "freedium":
        return await _freedium(url)
    logger.debug("[newsletter] unknown provider %s; skipping", name)
    return "", {}, url, ""


async def _wayback(url: str) -> tuple[str, dict[str, Any], str, str]:
    api = f"https://archive.org/wayback/available?url={quote(url, safe='')}"
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    ) as client:
        info_resp = await client.get(api)
        if info_resp.status_code >= 400:
            return "", {}, url, ""
        info = info_resp.json() or {}
        snapshot = (
            ((info.get("archived_snapshots") or {}).get("closest") or {}).get("url")
        )
        if not snapshot:
            return "", {}, url, ""
        return await _fetch_and_extract(snapshot)


async def _archive_ph(url: str) -> tuple[str, dict[str, Any], str, str]:
    target = f"https://archive.ph/newest/{url}"
    return await _fetch_and_extract(target, headers={"User-Agent": _DEFAULT_UA})


async def _freedium(url: str) -> tuple[str, dict[str, Any], str, str]:
    host = (urlparse(url).hostname or "").lower()
    if "medium.com" not in host and not host.endswith(".medium.com"):
        return "", {}, url, ""
    target = f"https://freedium.cfd/{url}"
    return await _fetch_and_extract(target, headers={"User-Agent": _DEFAULT_UA})


def _structured_newsletter(
    html: str,
    url: str,
    config: dict[str, Any],
) -> StructuredNewsletter:
    if not html or not config.get("site_specific_selectors_enabled", True):
        return StructuredNewsletter(site="unknown")
    return extract_structured(html, url=url)


async def _detect_stance(
    body_text: str,
    url: str,
    config: dict[str, Any],
) -> str:
    if not body_text or not config.get("stance_classifier_enabled", True):
        return "neutral"

    try:
        from website.features.summarization_engine.api.routes import _gemini_client

        client = _gemini_client()
        cache_root = (
            Path(__file__).resolve().parents[5] / "docs" / "summary_eval" / "_cache"
        )
        return await classify_stance(
            client=client,
            body_text=body_text,
            cache_root=cache_root,
            url=url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[newsletter] stance classify failed for %s: %s", url, exc)
        return "neutral"


__all__ = ["NewsletterIngestor"]
