"""Podcast show-notes ingestor without audio transcription."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.source_ingest.base import BaseIngestor
from website.features.summarization_engine.source_ingest.utils import extract_html_text, fetch_json, fetch_text, join_sections, utc_now


class PodcastIngestor(BaseIngestor):
    source_type = SourceType.PODCAST

    async def ingest(self, url: str, *, config: dict[str, Any]) -> IngestResult:
        if config.get("feed_url"):
            rss_result = await _try_rss_ingest(url, str(config["feed_url"]))
            if rss_result is not None:
                return rss_result

        html, final_url = await fetch_text(url, headers={"User-Agent": "zettelkasten-engine/2.0"})
        text, metadata = extract_html_text(html)
        metadata["audio_transcription"] = bool(config.get("audio_transcription", False))
        return IngestResult(
            source_type=self.source_type,
            url=final_url,
            original_url=url,
            raw_text=text,
            sections={"Show Notes": text},
            metadata=metadata,
            extraction_confidence="medium" if text else "low",
            confidence_reason="podcast page show notes extracted",
            fetched_at=utc_now(),
        )


async def _try_rss_ingest(url: str, feed_url: str) -> IngestResult | None:
    xml, _ = await fetch_text(feed_url, headers={"User-Agent": "zettelkasten-engine/2.0"})
    root = ET.fromstring(xml)
    namespace = {"podcast": "https://podcastindex.org/namespace/1.0"}
    matched_item = None
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if link.rstrip("/") == url.rstrip("/"):
            matched_item = item
            break
    if matched_item is None:
        matched_item = root.find(".//item")
    if matched_item is None:
        return None

    transcript_url = ""
    transcript_el = matched_item.find("podcast:transcript", namespace)
    if transcript_el is not None:
        transcript_url = transcript_el.attrib.get("url", "")
    chapters_url = ""
    chapters_el = matched_item.find("podcast:chapters", namespace)
    if chapters_el is not None:
        chapters_url = chapters_el.attrib.get("url", "")

    transcript = ""
    if transcript_url:
        transcript, _ = await fetch_text(transcript_url, headers={"User-Agent": "zettelkasten-engine/2.0"})
    chapters_text = ""
    if chapters_url:
        try:
            chapters_payload, _ = await fetch_json(chapters_url, headers={"User-Agent": "zettelkasten-engine/2.0"})
        except Exception:
            chapters_payload = json.loads((await fetch_text(chapters_url))[0])
        chapters = chapters_payload.get("chapters", []) if isinstance(chapters_payload, dict) else []
        chapter_titles = [
            str(chapter.get("title") or "").strip()
            for chapter in chapters
            if isinstance(chapter, dict) and chapter.get("title")
        ]
        chapters_text = "\n".join(chapter_titles)

    if not transcript and not chapters_text:
        return None

    title = (matched_item.findtext("title") or "").strip()
    sections = {
        "Episode": title,
        "Transcript": transcript,
        "Chapters": chapters_text,
    }
    return IngestResult(
        source_type=SourceType.PODCAST,
        url=url,
        original_url=url,
        raw_text=join_sections(sections),
        sections=sections,
        metadata={
            "title": title,
            "feed_url": feed_url,
            "transcript_url": transcript_url,
            "chapters_url": chapters_url,
            "transcript_source": "rss" if transcript else None,
            "chapters_source": "rss" if chapters_text else None,
            "audio_transcription": False,
        },
        extraction_confidence="high" if transcript else "medium",
        confidence_reason="RSS transcript/chapters extracted",
        fetched_at=utc_now(),
    )
