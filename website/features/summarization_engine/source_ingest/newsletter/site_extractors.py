"""Site-specific newsletter DOM extractors."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup


@dataclass
class StructuredNewsletter:
    site: str = "unknown"
    title: str = ""
    subtitle: str = ""
    body_text: str = ""
    cta_links: list[str] = field(default_factory=list)
    publication_identity: str = ""


def _detect_site(url: str, soup: BeautifulSoup) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "substack.com" in host:
        return "substack"
    if "beehiiv.com" in host:
        return "beehiiv"
    if "medium.com" in host or "hackernoon.com" in host:
        return "medium"
    if "buttondown.email" in host or "buttondown.com" in host:
        return "buttondown"
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    if og_site and "substack" in (og_site.get("content") or "").lower():
        return "substack"
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and "ghost" in (generator.get("content") or "").lower():
        return "ghost"
    if soup.select_one(
        "h1.post-title, h1.pencraft, h3.subtitle, div.body.markup, div.available-content"
    ):
        return "substack"
    if soup.select_one(
        ".rendered-post, h1[data-testid='post-title'], h2.post-subtitle, p.post-subtitle, a.subscribe-button"
    ):
        return "beehiiv"
    if soup.select_one(
        "h1[data-testid='storyTitle'], section[data-field='body'], h3.graf--subtitle"
    ):
        return "medium"
    if soup.select_one("article.gh-article, .gh-content, .gh-post-upgrade-cta"):
        return "ghost"
    if soup.select_one(
        "article.email-content, div.email-body, section.email, a[href*='buttondown.email']"
    ):
        return "buttondown"
    return "unknown"


def _substack(soup: BeautifulSoup) -> StructuredNewsletter:
    title_el = soup.select_one("h1.post-title, h1.pencraft")
    subtitle_el = soup.select_one("h3.subtitle, h3.pencraft")
    body_el = soup.select_one("div.body.markup, div.body, div.available-content")
    ctas = [
        anchor.get("href", "")
        for anchor in soup.select("div.post-footer a[href], a.subscribe-btn")
    ]
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    return StructuredNewsletter(
        site="substack",
        title=title_el.get_text(strip=True) if title_el else "",
        subtitle=subtitle_el.get_text(strip=True) if subtitle_el else "",
        body_text=body_el.get_text(separator="\n", strip=True) if body_el else "",
        cta_links=[cta for cta in ctas if cta],
        publication_identity=publication,
    )


def _beehiiv(soup: BeautifulSoup) -> StructuredNewsletter:
    title_el = soup.select_one("h1.post-title, h1[data-testid='post-title'], h1")
    subtitle_el = soup.select_one("h2.post-subtitle, p.post-subtitle")
    body_el = soup.select_one("div.rendered-post, article, div.post-content")
    ctas = [
        anchor.get("href", "")
        for anchor in soup.select("a.subscribe-button, a[data-cta='subscribe']")
    ]
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    if not subtitle_el:
        subtitle_el = soup.find("meta", attrs={"name": "description"})
    return StructuredNewsletter(
        site="beehiiv",
        title=title_el.get_text(strip=True) if title_el else "",
        subtitle=(
            subtitle_el.get_text(strip=True)
            if subtitle_el and hasattr(subtitle_el, "get_text")
            else (subtitle_el.get("content") or "").strip() if subtitle_el else ""
        ),
        body_text=body_el.get_text(separator="\n", strip=True) if body_el else "",
        cta_links=[cta for cta in ctas if cta],
        publication_identity=publication,
    )


def _medium(soup: BeautifulSoup) -> StructuredNewsletter:
    title_el = soup.select_one("h1, h1[data-testid='storyTitle']")
    subtitle_el = soup.select_one("h2, h3.graf--subtitle")
    body_el = soup.select_one("article, section[data-field='body']")
    ctas = [
        anchor.get("href", "")
        for anchor in soup.select("a.button--large, a[data-action='subscribe']")
    ]
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    return StructuredNewsletter(
        site="medium",
        title=title_el.get_text(strip=True) if title_el else "",
        subtitle=subtitle_el.get_text(strip=True) if subtitle_el else "",
        body_text=body_el.get_text(separator="\n", strip=True) if body_el else "",
        cta_links=[cta for cta in ctas if cta],
        publication_identity=publication,
    )


def _ghost(soup: BeautifulSoup) -> StructuredNewsletter:
    title_el = soup.select_one("h1.gh-article-title, h1")
    body_el = soup.select_one("div.gh-content, article")
    ctas = [
        anchor.get("href", "")
        for anchor in soup.select(".gh-post-upgrade-cta a[href], a[href*='subscribe']")
    ]
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    description = soup.find("meta", attrs={"name": "description"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    subtitle = (description.get("content") or "").strip() if description else ""
    return StructuredNewsletter(
        site="ghost",
        title=title_el.get_text(strip=True) if title_el else "",
        subtitle=subtitle,
        body_text=body_el.get_text(separator="\n", strip=True) if body_el else "",
        cta_links=[cta for cta in ctas if cta],
        publication_identity=publication,
    )


def _buttondown(soup: BeautifulSoup) -> StructuredNewsletter:
    """Buttondown native DOM extractor.

    Buttondown archives expose post bodies under ``article.email-content`` /
    ``div.email-body``; titles use ``h1.email-title`` (fallback to plain ``h1``).
    """
    title_el = soup.select_one("h1.email-title, h1")
    subtitle_el = soup.find("meta", attrs={"name": "description"})
    body_el = soup.select_one("article.email-content, div.email-body, section.email")
    ctas = [
        anchor.get("href", "")
        for anchor in soup.select("a[href*='subscribe'], a.subscribe-button")
    ]
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    subtitle = (
        (subtitle_el.get("content") or "").strip() if subtitle_el else ""
    )
    return StructuredNewsletter(
        site="buttondown",
        title=title_el.get_text(strip=True) if title_el else "",
        subtitle=subtitle,
        body_text=body_el.get_text(separator="\n", strip=True) if body_el else "",
        cta_links=[cta for cta in ctas if cta],
        publication_identity=publication,
    )


_EXTRACTORS = {
    "substack": _substack,
    "beehiiv": _beehiiv,
    "medium": _medium,
    "ghost": _ghost,
    "buttondown": _buttondown,
}


def extract_structured(html: str, *, url: str) -> StructuredNewsletter:
    soup = BeautifulSoup(html, "html.parser")
    site = _detect_site(url, soup)
    if site in _EXTRACTORS:
        result = _EXTRACTORS[site](soup)
        if result.title:
            return result
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    og_title = soup.find("meta", attrs={"property": "og:title"})
    publication = (og_site.get("content") or "").strip() if og_site else ""
    title = (og_title.get("content") or "").strip() if og_title else ""
    return StructuredNewsletter(
        site="unknown",
        title=title,
        publication_identity=publication,
    )
