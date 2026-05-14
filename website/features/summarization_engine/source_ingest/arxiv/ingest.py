"""arXiv paper ingestor."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

import feedparser
from bs4 import BeautifulSoup

from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.source_ingest.base import BaseIngestor
from website.features.summarization_engine.source_ingest.utils import compact_text, fetch_text, join_sections, utc_now


class ArxivIngestor(BaseIngestor):
    source_type = SourceType.ARXIV

    async def ingest(self, url: str, *, config: dict[str, Any]) -> IngestResult:
        paper_id = _parse_arxiv_id(url)
        api_base = config.get("api_base", "http://export.arxiv.org/api/query")
        api_fetched = True
        try:
            xml, _ = await fetch_text(f"{api_base}?id_list={quote(paper_id)}")
            parsed = feedparser.parse(xml)
            entry = parsed.entries[0] if parsed.entries else {}
        except Exception:
            api_fetched = False
            entry = {}
        title = compact_text(entry.get("title", ""))
        summary = compact_text(entry.get("summary", ""))
        authors = [author.get("name", "") for author in entry.get("authors", []) if author.get("name")]
        pdf_url = ""
        for link in entry.get("links", []):
            if link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
        sections = {
            "Paper": f"{title}\nAuthors: {', '.join(authors)}\nPublished: {entry.get('published', '')}",
            "Abstract": summary,
        }
        html_fetched = False
        if config.get("prefer_html", False) or not api_fetched:
            try:
                html_url = config.get(
                    "html_base",
                    "https://ar5iv.labs.arxiv.org/html/{paper_id}",
                ).format(paper_id=paper_id)
                html, _ = await fetch_text(html_url)
                html_sections = _extract_html_sections(
                    html,
                    max_chars=int(config.get("html_section_max_chars", 16000)),
                )
                if html_sections:
                    sections.update(html_sections)
                    html_fetched = True
            except Exception:
                html_fetched = False
        return IngestResult(
            source_type=self.source_type,
            url=f"https://arxiv.org/abs/{paper_id}",
            original_url=url,
            raw_text=join_sections(sections),
            sections=sections,
            metadata={
                "paper_id": paper_id,
                "title": title,
                "authors": authors,
                "published": entry.get("published"),
                "pdf_url": pdf_url or f"https://arxiv.org/pdf/{paper_id}",
                "html_fetched": html_fetched,
                "api_fetched": api_fetched,
            },
            extraction_confidence=(
                "high" if title and (summary or html_fetched)
                else "medium" if html_fetched
                else "low"
            ),
            confidence_reason=(
                "arXiv export API metadata fetched; HTML sections fetched"
                if api_fetched and html_fetched
                else "arXiv export API unavailable; HTML sections fetched"
                if html_fetched
                else "arXiv export API metadata fetched"
            ),
            fetched_at=utc_now(),
        )


def _parse_arxiv_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/(?:abs|pdf|html)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1).removesuffix(".pdf")
    return parsed.path.strip("/").removesuffix(".pdf")


def _extract_html_sections(html: str, *, max_chars: int) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "noscript", "svg"]):
        bad.decompose()
    wanted = {"abstract", "introduction", "method", "methods", "results", "discussion", "conclusion", "conclusions"}
    sections: dict[str, str] = {}
    for section in soup.find_all(["section", "div"]):
        heading_tag = section.find(["h1", "h2", "h3"])
        if not heading_tag:
            continue
        heading = compact_text(heading_tag.get_text(" ", strip=True))
        key = heading.lower().strip(" .0123456789")
        if key not in wanted and not any(name in key for name in wanted):
            continue
        text = compact_text(section.get_text(" ", strip=True), max_chars=max_chars)
        if text:
            sections[heading or key.title()] = text
    return sections
