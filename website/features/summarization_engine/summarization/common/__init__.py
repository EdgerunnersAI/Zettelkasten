"""Structured extraction phase."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from website.features.summarization_engine.core.budget import get_budget
from website.features.summarization_engine.core.config import EngineConfig
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import (
    DetailedSummarySection,
    IngestResult,
    SummaryMetadata,
    SummaryResult,
)
from website.features.summarization_engine.summarization.common.json_utils import parse_json_object
from website.features.summarization_engine.summarization.common.prompts import SYSTEM_PROMPT, source_context


class StructuredSummaryPayload(BaseModel):
    mini_title: str
    brief_summary: str
    tags: list[str]
    detailed_summary: list[DetailedSummarySection]


class StructuredExtractor:
    def __init__(self, client: TieredGeminiClient, config: EngineConfig):
        self._client = client
        self._config = config

    async def extract(
        self,
        ingest: IngestResult,
        summary_text: str,
        *,
        pro_tokens: int,
        flash_tokens: int,
        latency_ms: int,
        cod_iterations_used: int,
        self_check_missing_count: int,
        patch_applied: bool,
    ) -> SummaryResult:
        prompt = (
            f"{source_context(ingest.source_type)}\n\n"
            "Return a JSON object with these exact keys:\n"
            '- "mini_title": short title (max 8 words)\n'
            '- "brief_summary": 1-2 sentence summary\n'
            '- "tags": array of 8-15 lowercase hyphenated tags\n'
            '- "detailed_summary": array of section objects, each with '
            '"heading" (string), "bullets" (array of strings), '
            'and "sub_sections" (object mapping heading strings to arrays of bullet strings)\n\n'
            "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
            f"SUMMARY:\n{summary_text}"
        )
        get_budget().consume(role="summarizer")  # C6: 3-call budget
        result = await self._client.generate(
            prompt,
            tier="flash",
            response_schema=StructuredSummaryPayload,
            system_instruction=SYSTEM_PROMPT,
        )
        flash_tokens += result.input_tokens + result.output_tokens
        try:
            payload = StructuredSummaryPayload(**parse_json_object(result.text))
        except Exception:
            payload = _fallback_payload(ingest, summary_text, self._config)

        return SummaryResult(
            mini_title=payload.mini_title[: self._config.structured_extract.mini_title_max_chars],
            brief_summary=payload.brief_summary[: self._config.structured_extract.brief_summary_max_chars],
            tags=_normalize_tags(payload.tags, self._config.structured_extract.tags_min, self._config.structured_extract.tags_max),
            detailed_summary=payload.detailed_summary,
            metadata=SummaryMetadata(
                source_type=ingest.source_type,
                url=ingest.url,
                author=ingest.metadata.get("author"),
                date=_date_or_none(ingest.metadata.get("published") or ingest.metadata.get("date")),
                extraction_confidence=ingest.extraction_confidence,
                confidence_reason=ingest.confidence_reason,
                total_tokens_used=pro_tokens + flash_tokens,
                gemini_pro_tokens=pro_tokens,
                gemini_flash_tokens=flash_tokens,
                total_latency_ms=latency_ms,
                cod_iterations_used=cod_iterations_used,
                self_check_missing_count=self_check_missing_count,
                patch_applied=patch_applied,
            ),
        )


def _fallback_payload(ingest: IngestResult, summary_text: str, config: EngineConfig) -> StructuredSummaryPayload:
    title = ingest.metadata.get("title") or ingest.metadata.get("full_name") or "Captured source"
    words = summary_text.split()
    brief = " ".join(words[:50]) or "No summary text was available."
    tags = [ingest.source_type.value, "zettelkasten", "summary", "capture", "research", "source", "notes", "ai"]
    return StructuredSummaryPayload(
        mini_title=" ".join(str(title).split()[: config.structured_extract.mini_title_max_words]),
        brief_summary=brief,
        tags=tags,
        detailed_summary=[DetailedSummarySection(heading="Summary", bullets=[brief])],
    )


def _normalize_tags(tags: list[str], tags_min: int, tags_max: int) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        cleaned = tag.strip().lower().replace(" ", "-")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    for fallback in ("zettelkasten", "summary", "capture", "research", "source", "notes", "ai", "knowledge"):
        if len(normalized) >= tags_min:
            break
        if fallback not in normalized:
            normalized.append(fallback)
    return normalized[:tags_max]


def _date_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None

