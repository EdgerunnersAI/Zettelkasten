"""Structured extraction phase.

Routes payload_class-specific schemas end-to-end: the prompt is built from the
payload class's JSON schema so Gemini emits source-shaped output that matches
`response_schema` exactly. Source-specific rich fields are preserved in
`metadata.structured_payload`; the `detailed_summary` list surface is
back-filled from the rich payload so downstream SummaryResult consumers stay
compatible.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel

from website.features.summarization_engine.core.budget import get_budget
from website.features.summarization_engine.core.config import EngineConfig
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import (
    DetailedSummarySection,
    IngestResult,
    SourceType,
    SummaryMetadata,
    SummaryResult,
)
from website.features.summarization_engine.summarization.common.json_utils import (
    parse_json_object,
)
from website.features.summarization_engine.summarization.common.prompts import (
    SYSTEM_PROMPT,
    source_context,
)
from website.features.summarization_engine.summarization.common.text_guards import (
    _DANGLING_TAIL_WORDS,
    ensure_terminator,
    sanitize_bullets,
    split_sentences,
)

_log = logging.getLogger(__name__)


class StructuredSummaryPayload(BaseModel):
    mini_title: str
    brief_summary: str
    tags: list[str]
    detailed_summary: list[DetailedSummarySection]


class StructuredExtractor:
    def __init__(
        self,
        client: TieredGeminiClient,
        config: EngineConfig,
        payload_class: type[BaseModel] = StructuredSummaryPayload,
        *,
        fallback_builder: Optional[
            Callable[[IngestResult, str, EngineConfig], BaseModel]
        ] = None,
        prompt_builder: Optional[
            Callable[[IngestResult, str, str], str]
        ] = None,
        prompt_instruction: str | None = None,
        missing_facts_hint: list[str] | None = None,
    ):
        self._client = client
        self._config = config
        self._payload_class = payload_class
        self._fallback_builder = fallback_builder
        self._prompt_builder = prompt_builder
        self._prompt_instruction = prompt_instruction
        # Optional coverage hint threaded from the DenseVerify phase. When
        # non-empty, each entry is a source claim the density phase flagged
        # as important-but-not-yet-covered; the extractor appends these to
        # the prompt so the structured payload is guided to cover them. The
        # hint never alters schema or retry behavior — it is purely additive
        # prompt material. Default ``None`` keeps the call byte-identical to
        # pre-refactor callers.
        self._missing_facts_hint = list(missing_facts_hint or [])

    def _schema_snippet(self) -> str:
        """Compact JSON-schema hint included in the prompt.

        Gemini honors `response_schema` most reliably when the prompt ALSO
        describes the shape it should emit. A bare prompt that contradicts
        `response_schema` produces confused output and pydantic fallbacks.
        """
        schema = self._payload_class.model_json_schema()
        return json.dumps(schema, separators=(",", ":"))[:3000]

    def _missing_facts_suffix(self) -> str:
        """Return a prompt suffix instructing the model to cover hinted facts.

        Empty when no hint was provided; otherwise a single explicit line
        listing each flagged fact so the model is guided — but never forced —
        to cover it in the structured payload.
        """
        if not self._missing_facts_hint:
            return ""
        joined = "; ".join(f.strip() for f in self._missing_facts_hint if f.strip())
        if not joined:
            return ""
        return f"\n\nEnsure these facts are covered: {joined}"

    def _build_prompt(self, ingest: IngestResult, summary_text: str) -> str:
        hint_suffix = self._missing_facts_suffix()
        if self._prompt_instruction:
            schema_json = self._schema_snippet()
            try:
                base = self._prompt_instruction.format(
                    summary_text=summary_text,
                    schema_json=schema_json,
                )
            except (KeyError, IndexError):
                base = (
                    f"{self._prompt_instruction}\n\n"
                    f"SCHEMA:\n{schema_json}\n\n"
                    f"SUMMARY:\n{summary_text}"
                )
            return base + hint_suffix
        schema_json = self._schema_snippet()
        if self._prompt_builder is not None:
            return self._prompt_builder(ingest, summary_text, schema_json) + hint_suffix
        return (
            f"{source_context(ingest.source_type)}\n\n"
            f"Return a JSON object that EXACTLY matches the following JSON schema "
            f"for class {self._payload_class.__name__}. Populate every required field "
            f"from the SUMMARY below - do not invent facts. Use temperature 0 judgment.\n\n"
            f"SCHEMA:\n{schema_json}\n\n"
            "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
            f"SUMMARY:\n{summary_text}"
            + hint_suffix
        )

    def _build_repair_prompt(
        self,
        ingest: IngestResult,
        summary_text: str,
        broken_response: str,
        error: Exception,
    ) -> str:
        schema_json = self._schema_snippet()
        broken_preview = (broken_response or "").strip()[:4000]
        return (
            f"{source_context(ingest.source_type)}\n\n"
            f"Your previous JSON response for class {self._payload_class.__name__} "
            f"was invalid. Repair it so it EXACTLY matches this JSON schema.\n\n"
            f"SCHEMA:\n{schema_json}\n\n"
            f"VALIDATION ERROR:\n{type(error).__name__}: {error}\n\n"
            "Use the BROKEN RESPONSE only as a draft to fix structure and escaping. "
            "Preserve grounded facts, do not add new claims, and return raw JSON only.\n\n"
            f"BROKEN RESPONSE:\n{broken_preview}\n\n"
            f"SUMMARY:\n{summary_text}"
        )

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
        telemetry_sink: list | None = None,
    ) -> SummaryResult:
        is_fallback = False
        structured_payload: dict | None = None
        prompt = self._build_prompt(ingest, summary_text)
        max_attempts = 1 + max(
            0, self._config.structured_extract.validation_retries
        )
        payload: BaseModel

        for attempt in range(max_attempts):
            _role = "summarizer" if attempt == 0 else "repair"
            # C6: budget per call (each retry is a billable LLM round-trip).
            get_budget().consume(role=_role)
            result = await self._client.generate(
                prompt,
                tier="flash",
                response_schema=self._payload_class,
                system_instruction=SYSTEM_PROMPT,
                role=_role,
            )
            if telemetry_sink is not None:
                from website.features.summarization_engine.summarization.common.model_trace import (
                    make_call_entry,
                )
                telemetry_sink.append(make_call_entry(role=_role, result=result))
            flash_tokens += result.input_tokens + result.output_tokens

            try:
                raw = parse_json_object(result.text)
                raw = _apply_identifier_hints(raw, ingest)
                coercer = getattr(self._payload_class, "coerce_raw", None)
                if callable(coercer):
                    hint = _mini_title_hint_for(ingest)
                    raw = coercer(raw, mini_title_hint=hint)
                payload = self._payload_class(**raw)
                structured_payload = payload.model_dump(mode="json")
                break
            except Exception as exc:
                if attempt == max_attempts - 1:
                    _log.warning(
                        "structured.extract schema_fallback payload_class=%s err=%s preview=%s",
                        self._payload_class.__name__,
                        type(exc).__name__,
                        (result.text or "")[:160].replace("\n", " "),
                    )
                    if self._fallback_builder is not None:
                        payload = self._fallback_builder(
                            ingest, summary_text, self._config
                        )
                    else:
                        payload = _fallback_payload(
                            ingest, summary_text, self._config
                        )
                    is_fallback = True
                    structured_payload = None
                    break
                _log.info(
                    "structured.extract retry payload_class=%s attempt=%d err=%s",
                    self._payload_class.__name__,
                    attempt + 2,
                    type(exc).__name__,
                )
                prompt = self._build_repair_prompt(
                    ingest,
                    summary_text,
                    result.text or "",
                    exc,
                )

        detailed_list = _coerce_detailed_summary(payload, ingest.source_type)
        tags = _normalize_tags(
            getattr(payload, "tags", []) or [],
            self._config.structured_extract.tags_min,
            self._config.structured_extract.tags_max,
            allow_boilerplate_pad=is_fallback,
            source_type_value=ingest.source_type.value,
        )

        brief_max = self._config.structured_extract.brief_summary_max_chars
        brief_raw = str(getattr(payload, "brief_summary", ""))
        brief_truncated = _smart_truncate(brief_raw, brief_max)
        brief_safe = _safe_brief(brief_truncated)
        return SummaryResult(
            mini_title=str(getattr(payload, "mini_title", ""))[
                : self._config.structured_extract.mini_title_max_chars
            ],
            brief_summary=brief_safe,
            tags=tags,
            detailed_summary=detailed_list,
            metadata=SummaryMetadata(
                source_type=ingest.source_type,
                url=ingest.url,
                author=ingest.metadata.get("author"),
                date=_date_or_none(
                    ingest.metadata.get("published") or ingest.metadata.get("date")
                ),
                extraction_confidence=ingest.extraction_confidence,
                confidence_reason=ingest.confidence_reason,
                total_tokens_used=pro_tokens + flash_tokens,
                gemini_pro_tokens=pro_tokens,
                gemini_flash_tokens=flash_tokens,
                total_latency_ms=latency_ms,
                cod_iterations_used=cod_iterations_used,
                self_check_missing_count=self_check_missing_count,
                patch_applied=patch_applied,
                structured_payload=structured_payload,
                is_schema_fallback=is_fallback,
            ),
        )


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate at a sentence boundary when possible, never mid-clause.

    Prefers the last terminal-punctuation boundary in range. If none exists
    above the half-window mark, drops back to the last whole-word boundary
    — never cuts mid-word and never appends a period to a mid-word cut.
    """
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars]
    best_idx = -1
    for stop in (".!?"):
        idx = window.rfind(stop)
        if idx > best_idx:
            best_idx = idx
    if best_idx >= max_chars // 2:
        return window[: best_idx + 1].strip()
    # No sentence boundary: back off to last whole-word boundary, but ONLY
    # if the next character in the source would extend this word (i.e. the
    # cut lands mid-word). Never append "." to a mid-word stub.
    idx = window.rfind(" ")
    if idx <= 0:
        return window.rstrip(",;: ")
    trimmed = window[:idx].rstrip(",;: ")
    if not trimmed:
        return window.rstrip(",;: ")
    return trimmed


def _safe_brief(text: str) -> str:
    """Return a dangling-free version of the brief.

    Sanitize every sentence: strip trailing dangling connectors, drop
    incomplete tail sentences. If the raw text didn't end with terminal
    punctuation, the last sentence is almost certainly truncated — drop it
    rather than terminating mid-clause.
    """
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    raw_terminated = cleaned[-1] in ".!?"
    sentences = split_sentences(ensure_terminator(cleaned))
    if not raw_terminated and len(sentences) > 1:
        sentences = sentences[:-1]
    safe: list[str] = []
    for sentence in sentences:
        repaired = _strip_dangling_tail(sentence)
        if repaired:
            safe.append(repaired)
    if safe:
        return " ".join(safe).strip()

    return _strip_dangling_tail(cleaned)


def _strip_dangling_tail(sentence: str) -> str:
    """Drop trailing dangling words from a single sentence; terminate safely."""
    body = re.sub(r"\s+", " ", sentence or "").strip().rstrip(".!?,;: ").strip()
    while body:
        match = re.search(r"([A-Za-z][A-Za-z'-]*)\s*$", body)
        if not match:
            break
        if match.group(1).lower() not in _DANGLING_TAIL_WORDS:
            break
        body = body[: match.start()].rstrip(".!?,;: ").strip()
    if not body:
        return ""
    return ensure_terminator(body)


def _coerce_detailed_summary(
    payload: BaseModel,
    source_type: SourceType | None = None,
) -> list[DetailedSummarySection]:
    """Convert a source-specific or generic detailed_summary into a list of sections.

    Per-source composers (``compose_*_detailed``) build a stable Overview →
    walkthrough → Closing remarks hierarchy with populated ``sub_sections``
    so the renderer never emits raw schema-key headings (``thesis``,
    ``reply_clusters``) or JSON-stringified chapter objects. The generic
    path below remains the fallback when source_type is unknown or when the
    payload class does not match the expected per-source schema.
    """
    if source_type == SourceType.YOUTUBE:
        return _coerce_youtube_detailed(payload)
    if source_type == SourceType.REDDIT:
        return _coerce_reddit_detailed(payload)
    if source_type == SourceType.GITHUB:
        return _coerce_github_detailed(payload)
    if source_type == SourceType.NEWSLETTER:
        return _coerce_newsletter_detailed(payload)

    raw = getattr(payload, "detailed_summary", None)
    if raw is None:
        return [DetailedSummarySection(heading="Summary", bullets=[""])]

    if isinstance(raw, list):
        out: list[DetailedSummarySection] = []
        for item in raw:
            if isinstance(item, DetailedSummarySection):
                out.append(item)
            elif isinstance(item, BaseModel):
                data = item.model_dump(mode="json")
                heading = str(
                    data.get("heading")
                    or data.get("title")
                    or data.get("timestamp")
                    or "Section"
                )
                bullets = data.get("bullets") or []
                if not bullets:
                    bullets = [
                        f"{k}: {v}"
                        for k, v in data.items()
                        if k not in {"heading", "bullets", "sub_sections"} and v
                    ]
                bullets = [str(b) for b in bullets if b]
                if not bullets:
                    bullets = [heading]
                out.append(
                    DetailedSummarySection(
                        heading=heading,
                        bullets=bullets,
                        sub_sections=data.get("sub_sections") or {},
                    )
                )
            elif isinstance(item, dict):
                heading = str(item.get("heading") or item.get("title") or "Section")
                bullets = [str(b) for b in (item.get("bullets") or []) if b]
                if not bullets:
                    bullets = [heading]
                out.append(
                    DetailedSummarySection(
                        heading=heading,
                        bullets=bullets,
                        sub_sections=item.get("sub_sections") or {},
                    )
                )
        return out or [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]

    if isinstance(raw, BaseModel):
        data = raw.model_dump(mode="json")
        sections: list[DetailedSummarySection] = []
        for key, value in data.items():
            if value is None or value == [] or value == {}:
                continue
            if isinstance(value, str):
                sections.append(DetailedSummarySection(heading=key, bullets=[value]))
            elif isinstance(value, list):
                bullets: list[str] = []
                for item in value:
                    if isinstance(item, dict):
                        bullets.append(json.dumps(item, ensure_ascii=False))
                    else:
                        bullets.append(str(item))
                if bullets:
                    sections.append(
                        DetailedSummarySection(heading=key, bullets=bullets)
                    )
            elif isinstance(value, dict):
                sections.append(
                    DetailedSummarySection(
                        heading=key,
                        bullets=[json.dumps(value, ensure_ascii=False)],
                    )
                )
            else:
                sections.append(DetailedSummarySection(heading=key, bullets=[str(value)]))
        return sections or [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]

    return [DetailedSummarySection(heading="Summary", bullets=[str(raw)])]


def _sanitize_composed(
    sections: list[DetailedSummarySection],
) -> list[DetailedSummarySection]:
    """Post-process composed sections to drop dangling-tail bullets.

    Composers pull bullets directly from LLM output; the text-guard layer
    catches the class of defect observed in YouTube iter-08 where the
    closing takeaway was ``"The main takeaway is DMT is a powerful."`` —
    a terminated sentence whose tail word is a scaffold requiring a
    following noun. Sections whose primary bullets all drop are kept in
    place with a safe fallback so the renderer's section count stays
    stable; empty sub_sections are pruned.
    """
    out: list[DetailedSummarySection] = []
    for section in sections:
        safe_bullets = sanitize_bullets(section.bullets or [])
        safe_subs = _sanitize_heading_keyed_subs(section.sub_sections or {})
        if not safe_bullets and not safe_subs:
            continue
        if not safe_bullets and safe_subs:
            safe_bullets = []
        safe_heading = _sanitize_heading(section.heading)
        out.append(
            DetailedSummarySection(
                heading=safe_heading,
                bullets=safe_bullets,
                sub_sections=safe_subs,
            )
        )
    if not out:
        return [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]
    return out


def _sanitize_heading(heading: str) -> str:
    """Strip dangling connectors from a section/chapter heading.

    Headings like ``"Love and Loss: Founding and"`` leak a trailing
    conjunction that looks like a truncated clause. Strip the dangling
    word rather than pad with punctuation.
    """
    body = re.sub(r"\s+", " ", heading or "").strip().rstrip(":;,.!? ")
    while body:
        match = re.search(r"([A-Za-z][A-Za-z'-]*)\s*$", body)
        if not match:
            break
        if match.group(1).lower() not in _DANGLING_TAIL_WORDS:
            break
        body = body[: match.start()].rstrip(":;,.!? ").strip()
    return body or (heading or "").strip()


def _sanitize_heading_keyed_subs(
    sub_sections: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Sanitize both the sub_section headings and their bullets."""
    out: dict[str, list[str]] = {}
    for heading, bullets in (sub_sections or {}).items():
        safe_bullets = sanitize_bullets(bullets or [])
        if not safe_bullets:
            continue
        safe_heading = _sanitize_heading(heading)
        if not safe_heading:
            safe_heading = heading
        out[safe_heading] = safe_bullets
    return out


def _coerce_youtube_detailed(payload: BaseModel) -> list[DetailedSummarySection]:
    """Delegate YouTube detailed composition to the dedicated layout module.

    Accepts the OUTER YouTubeStructuredPayload so the composer can fold
    speakers and brief_summary into the Overview section. Falls back to a
    minimal Summary section only when validation fails.
    """
    from website.features.summarization_engine.summarization.youtube.layout import (
        compose_youtube_detailed,
    )
    from website.features.summarization_engine.summarization.youtube.schema import (
        YouTubeStructuredPayload,
    )

    if isinstance(payload, YouTubeStructuredPayload):
        return _sanitize_composed(compose_youtube_detailed(payload))
    try:
        validated = YouTubeStructuredPayload.model_validate(payload.model_dump(mode="json"))
    except Exception:
        return [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]
    return _sanitize_composed(compose_youtube_detailed(validated))


def _coerce_reddit_detailed(payload: BaseModel) -> list[DetailedSummarySection]:
    """Delegate Reddit detailed composition to the dedicated layout module."""
    from website.features.summarization_engine.summarization.reddit.layout import (
        compose_reddit_detailed,
    )
    from website.features.summarization_engine.summarization.reddit.schema import (
        RedditStructuredPayload,
    )

    if isinstance(payload, RedditStructuredPayload):
        return _sanitize_composed(compose_reddit_detailed(payload))
    try:
        validated = RedditStructuredPayload.model_validate(payload.model_dump(mode="json"))
    except Exception:
        return [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]
    return _sanitize_composed(compose_reddit_detailed(validated))


def _coerce_github_detailed(payload: BaseModel) -> list[DetailedSummarySection]:
    """Delegate GitHub detailed composition to the dedicated layout module."""
    from website.features.summarization_engine.summarization.github.layout import (
        compose_github_detailed,
    )
    from website.features.summarization_engine.summarization.github.schema import (
        GitHubStructuredPayload,
    )

    if isinstance(payload, GitHubStructuredPayload):
        return _sanitize_composed(compose_github_detailed(payload))
    try:
        validated = GitHubStructuredPayload.model_validate(payload.model_dump(mode="json"))
    except Exception:
        return [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]
    return _sanitize_composed(compose_github_detailed(validated))


def _coerce_newsletter_detailed(payload: BaseModel) -> list[DetailedSummarySection]:
    """Delegate Newsletter detailed composition to the dedicated layout module."""
    from website.features.summarization_engine.summarization.newsletter.layout import (
        compose_newsletter_detailed,
    )
    from website.features.summarization_engine.summarization.newsletter.schema import (
        NewsletterStructuredPayload,
    )

    if isinstance(payload, NewsletterStructuredPayload):
        return _sanitize_composed(compose_newsletter_detailed(payload))
    try:
        validated = NewsletterStructuredPayload.model_validate(payload.model_dump(mode="json"))
    except Exception:
        return [DetailedSummarySection(heading="Summary", bullets=["(empty)"])]
    return _sanitize_composed(compose_newsletter_detailed(validated))


def _mini_title_hint_for(ingest: IngestResult) -> str:
    """Deterministic mini_title hint (URL-first for GitHub).

    The evaluator compares the emitted ``mini_title`` against the INPUT URL, not
    the repo's current ``full_name``. GitHub resolves redirects (e.g.
    ``tiangolo/typer`` → ``fastapi/typer``), so preferring URL over metadata
    keeps the label aligned with what the user queried.
    """
    st = ingest.source_type
    if st == SourceType.GITHUB:
        url = str(ingest.url or "")
        match = re.search(
            r"github\.com/([^/\s]+)/([^/\s?#]+)",
            url,
            flags=re.IGNORECASE,
        )
        if match:
            return f"{match.group(1)}/{match.group(2)}"[:60]
        meta = ingest.metadata or {}
        full_name = meta.get("full_name") or meta.get("repo_full_name")
        if isinstance(full_name, str) and "/" in full_name:
            return full_name[:60]
    return ""


def _apply_identifier_hints(raw: dict, ingest: IngestResult) -> dict:
    """Patch mini_title from deterministic ingest metadata before pydantic validation.

    Gemini often produces near-correct but schema-invalid labels (e.g. ``fastapi.repository``
    instead of ``fastapi/fastapi``; ``IndianStockMarket thread`` instead of
    ``r/IndianStockMarket ...``). The ingest layer already knows the canonical identifier
    for these sources, so we always prefer it over the model's guess. This does NOT touch
    prose fields (brief_summary, architecture_overview, detailed_summary) - only the
    schema-gated identifier.
    """
    if not isinstance(raw, dict):
        return raw
    st = ingest.source_type
    meta = ingest.metadata or {}
    if st == SourceType.GITHUB:
        # URL-first: evaluator checks emitted label against the queried URL,
        # and GitHub resolves redirects (e.g. tiangolo/typer → fastapi/typer)
        # in metadata.full_name which would otherwise cause a label mismatch.
        url = str(ingest.url or "")
        match = re.search(r"github\.com/([^/\s]+)/([^/\s?#]+)", url, flags=re.IGNORECASE)
        full_name: str | None = None
        if match:
            full_name = f"{match.group(1)}/{match.group(2)}"
        else:
            meta_name = meta.get("full_name") or meta.get("repo_full_name")
            if isinstance(meta_name, str) and "/" in meta_name:
                full_name = meta_name
        if isinstance(full_name, str) and "/" in full_name:
            raw["mini_title"] = full_name[:60]
    elif st == SourceType.REDDIT:
        subreddit = meta.get("subreddit") or meta.get("sub")
        if isinstance(subreddit, str) and subreddit:
            prefix = f"r/{subreddit.lstrip('r/').lstrip('/')}"
            current = str(raw.get("mini_title") or "")
            if not current.startswith(prefix):
                suffix_source = current or str(meta.get("title") or "").strip()
                suffix = suffix_source[: max(0, 60 - len(prefix) - 1)].strip()
                raw["mini_title"] = f"{prefix} {suffix}".strip()[:60]
        _REDDIT_PLACEHOLDERS = {
            "op", "the op", "original poster", "the original poster",
            "the author", "author", "user", "the user", "commenter",
            "the commenter", "poster", "the poster",
        }
        detailed = raw.get("detailed_summary")
        if isinstance(detailed, dict):
            op = detailed.get("op_intent")
            if isinstance(op, str) and op.strip().lower() in _REDDIT_PLACEHOLDERS:
                title_hint = str(meta.get("title") or "").strip()
                if title_hint:
                    detailed["op_intent"] = f"sought discussion about {title_hint}"[:200]
    elif st == SourceType.NEWSLETTER:
        detailed = raw.get("detailed_summary")
        if isinstance(detailed, dict):
            pub = detailed.get("publication_identity")
            if not isinstance(pub, str) or pub.strip().lower() in {
                "", "source", "the source", "newsletter", "the newsletter",
                "unknown", "n/a", "none",
            }:
                host = meta.get("site_name") or meta.get("publication") or meta.get("author")
                if isinstance(host, str) and host.strip():
                    detailed["publication_identity"] = host.strip()[:120]
    elif st == SourceType.YOUTUBE:
        _YT_PLACEHOLDERS = {
            "narrator", "host", "speaker", "analyst", "commentator",
            "voiceover", "voice over", "author of the source",
            "the host", "the speaker", "the narrator", "author",
            "presenter", "the presenter", "source", "the source",
            "channel", "the channel", "uploader", "the uploader",
            "creator", "the creator", "interviewer", "the interviewer",
            "interviewee", "the interviewee", "participant", "the participant",
            "guest", "the guest", "youtuber", "the youtuber",
        }
        channel = (
            meta.get("channel")
            or meta.get("uploader")
            or meta.get("author")
            or meta.get("channel_name")
        )
        speakers = raw.get("speakers")
        if isinstance(speakers, list):
            filtered = [
                s.strip() for s in speakers
                if isinstance(s, str) and s.strip()
                and s.strip().lower() not in _YT_PLACEHOLDERS
            ]
            if not filtered and isinstance(channel, str) and channel.strip():
                filtered = [channel.strip()]
            if filtered:
                raw["speakers"] = filtered
        elif isinstance(channel, str) and channel.strip():
            raw["speakers"] = [channel.strip()]
    return raw


def _fallback_payload(
    ingest: IngestResult, summary_text: str, config: EngineConfig
) -> StructuredSummaryPayload:
    """Graceful schema-fallback with a minimum-viable Overview.

    Downstream evaluators still detect the `_schema_fallback_` tag as a
    routing-bug signal, but the payload itself is structurally valid so the
    composite score floor doesn't collapse past the hallucination cap and
    hide other regressions.
    """
    meta = ingest.metadata or {}
    title = meta.get("title") or meta.get("full_name") or "Captured source"
    channel = (
        meta.get("channel")
        or meta.get("uploader")
        or meta.get("author")
        or meta.get("channel_name")
        or ""
    )
    brief_max = config.structured_extract.brief_summary_max_chars
    brief = _smart_truncate(summary_text, brief_max) or "No summary text was available."

    normalized_text = re.sub(r"\s+", " ", summary_text or "").strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized_text) if s.strip()]
    overview_bullets = [sentences[0]] if sentences else [brief]
    subs: dict[str, list[str]] = {}
    if channel:
        subs["Source"] = [f"Channel: {channel}"]
    if len(sentences) >= 2:
        subs["Additional context"] = sentences[1:4]

    sections = [
        DetailedSummarySection(
            heading="Overview",
            bullets=overview_bullets,
            sub_sections=subs,
        )
    ]

    return StructuredSummaryPayload(
        mini_title=str(title)[: config.structured_extract.mini_title_max_chars],
        brief_summary=brief,
        tags=["_schema_fallback_"],
        detailed_summary=sections,
    )


_BOILERPLATE_TAGS = frozenset(
    {"zettelkasten", "summary", "capture", "research", "source", "notes", "ai", "knowledge"}
)

# Internal sentinel tags (e.g. ``_schema_fallback_``) flow through the structured
# payload so evaluators can detect routing failures, but they must never reach
# user-facing surfaces (Obsidian notes, KG nodes). The pattern matches
# single-leading-underscore + single-trailing-underscore identifiers like
# ``_schema_fallback_`` while leaving real tags (``python``, ``__double__``,
# ``trailing_``) untouched.
_SENTINEL_TAG_RE = re.compile(r"^_[a-z][a-z0-9_]*_$")


def _strip_sentinel_tags(tags: list[str]) -> list[str]:
    """Return ``tags`` with internal sentinel markers removed.

    Order is preserved and the input list is not mutated. Non-string entries are
    coerced to ``str`` for the regex check so callers don't need to pre-clean.
    """
    return [tag for tag in tags if not _SENTINEL_TAG_RE.match(str(tag))]


def _normalize_tags(
    tags: list[str],
    tags_min: int,
    tags_max: int,
    *,
    allow_boilerplate_pad: bool = False,
    source_type_value: str | None = None,
    reserved: list[str] | None = None,
    tag_cleaner=None,
) -> list[str]:
    """Normalize tags. Does NOT pad with boilerplate unless explicitly allowed
    (schema-fallback path only). Boilerplate tags ('zettelkasten', 'summary',
    'capture', ...) are masking real routing bugs when they appear on a
    supposedly-successful summary.

    When ``reserved`` is provided, those tags are cleaned with the same rule,
    placed FIRST in the output (in the order given, deduped), and topical tags
    follow up to the cap. This guarantees source-identifying tags (subreddit,
    channel, owner/repo, brand) survive truncation regardless of how many
    topical tags the LLM emits.

    ``tag_cleaner`` lets a caller override the default ``str.lower + spaces ->
    dashes`` rule (used by Reddit, which strips arbitrary punctuation via
    regex). It must be a callable ``(str) -> str`` returning the canonical
    form. When ``None`` the default cleaner is applied.

    Default behavior (no ``reserved=`` and no ``tag_cleaner=``) is byte-
    identical to the prior implementation.
    """
    if tag_cleaner is None:
        def _default_clean(t: object) -> str:
            return str(t).strip().lower().replace(" ", "-")
        tag_cleaner = _default_clean

    normalized: list[str] = []
    for tag in tags:
        cleaned = tag_cleaner(tag)
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    # Strip internal sentinels (e.g. ``_schema_fallback_``) AFTER normalization
    # so they never leak into user-facing tag lists, but BEFORE the boilerplate
    # pad loop so a fallback payload still triggers padding to ``tags_min``.
    normalized = _strip_sentinel_tags(normalized)

    if reserved:
        cleaned_reserved: list[str] = []
        for r in reserved:
            cr = tag_cleaner(r)
            if cr and cr not in cleaned_reserved:
                cleaned_reserved.append(cr)
        # Topical = normalized tags excluding any that match a reserved entry.
        topical = [t for t in normalized if t not in cleaned_reserved]
        # Truncate reserved at cap so it never alone exceeds tags_max.
        cleaned_reserved = cleaned_reserved[:tags_max]
        room = max(0, tags_max - len(cleaned_reserved))
        merged = cleaned_reserved + topical[:room]
        if allow_boilerplate_pad and source_type_value:
            for fallback in (source_type_value, *_BOILERPLATE_TAGS):
                if len(merged) >= tags_min:
                    break
                if fallback not in merged:
                    merged.append(fallback)
        return merged[:tags_max]

    if allow_boilerplate_pad and source_type_value:
        for fallback in (source_type_value, *_BOILERPLATE_TAGS):
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
