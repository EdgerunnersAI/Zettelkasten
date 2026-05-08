"""Canonical persistence helpers for summarize → write-everywhere fanout.

This module is the **single source of truth** for persisting a summarize
result into the knowledge graph. Every ingest path (Telegram bot, website
``/api/summarize``, eval register scripts, future callers) should call
:func:`persist_summarized_result`.

Historically this code lived at
``website.experimental_features.nexus.service.persist``; a compat shim at
that path re-exports the public symbols so existing imports keep working.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from website.core.graph_store import _SOURCE_PREFIX, add_node, get_graph
from website.core.db_version import use_supabase_v2
from website.core.settings import get_settings
from website.core.supabase_kg import KGNodeCreate, KGRepository, is_supabase_configured
from website.core.supabase_v2.models import CanonicalChunkCreate, CanonicalZettelCreate, WorkspaceZettelCreate
from website.core.supabase_v2.repositories.content_repository import ContentRepository as V2ContentRepository
from website.core.supabase_v2.repositories.core_repository import CoreRepository as V2CoreRepository
from website.core.text_polish import polish, rewrite_tags, strip_caveats

logger = logging.getLogger("website.core.persist")

_supabase_repo: KGRepository | None = None
_supabase_user_id: str | None = None
_v2_core_repo: V2CoreRepository | None = None
_v2_content_repo: V2ContentRepository | None = None

_EXISTING_TYPES_CACHE: dict[str, tuple[float, list[str]]] = {}
_EXISTING_TYPES_TTL = 60.0


@dataclass(slots=True)
class PersistenceOutcome:
    """Result of writing a summarized artifact into the knowledge graph."""

    result: dict[str, Any]
    file_node_id: str | None = None
    supabase_node_id: str | None = None
    supabase_saved: bool = False
    supabase_duplicate: bool = False
    kg_user_id: str | None = None


def get_supabase_scope(user_id_override: str | None = None) -> tuple[KGRepository, str] | None:
    """Return ``(repo, kg_user_id)`` when Supabase is configured."""

    global _supabase_repo, _supabase_user_id

    if not is_supabase_configured():
        return None

    if _supabase_repo is None:
        try:
            _supabase_repo = KGRepository()
        except Exception as exc:
            logger.warning("Supabase init failed, falling back to file store: %s", exc)
            return None

    if user_id_override:
        try:
            existing = _supabase_repo.get_user_by_render_id(user_id_override)
            if existing:
                stats = _supabase_repo.get_stats(existing.id)
                if stats["node_count"] == 0:
                    legacy = _supabase_repo.get_user_by_render_id("naruto")
                    if legacy and legacy.id != existing.id:
                        legacy_stats = _supabase_repo.get_stats(legacy.id)
                        if legacy_stats["node_count"] > 0:
                            _supabase_repo.transfer_data(legacy.id, existing.id)
                            _supabase_user_id = None
                            logger.info(
                                "Transferred %d nodes from naruto to %s",
                                legacy_stats["node_count"],
                                user_id_override,
                            )
                return _supabase_repo, str(existing.id)

            legacy = _supabase_repo.get_user_by_render_id("naruto")
            if legacy:
                claimed = _supabase_repo.claim_user("naruto", user_id_override)
                if claimed:
                    _supabase_user_id = None
                    return _supabase_repo, str(claimed.id)

            user = _supabase_repo.get_or_create_user(user_id_override, display_name="Web User")
            return _supabase_repo, str(user.id)
        except Exception as exc:
            logger.warning("Supabase user lookup failed: %s", exc)
            return None

    if _supabase_user_id is None:
        try:
            user = _supabase_repo.get_or_create_user("naruto", display_name="Naruto")
            _supabase_user_id = str(user.id)
        except Exception as exc:
            logger.warning("Supabase default user init failed: %s", exc)
            return None

    return _supabase_repo, _supabase_user_id


def get_supabase_v2_scope(user_sub: str | None = None) -> tuple[V2ContentRepository, UUID, UUID] | None:
    """Return ``(content_repo, profile_id, workspace_id)`` for DB v2.

    DB v2 is workspace-first and requires a Supabase Auth UUID. Anonymous or
    legacy render-style IDs intentionally fall back to the existing file/v1
    path until the auth migration is complete.
    """
    global _v2_core_repo, _v2_content_repo

    if not use_supabase_v2() or not user_sub:
        return None
    try:
        profile_id = UUID(str(user_sub))
    except (TypeError, ValueError):
        logger.info("DB v2 requires UUID auth subject; falling back for user_sub=%r", user_sub)
        return None

    try:
        _v2_core_repo = _v2_core_repo or V2CoreRepository()
        _v2_content_repo = _v2_content_repo or V2ContentRepository()
        workspace_id = _v2_core_repo.get_default_workspace_id(profile_id)
        if workspace_id is None:
            return None
        return _v2_content_repo, profile_id, workspace_id
    except Exception as exc:
        logger.warning("Supabase v2 scope lookup failed, falling back: %s", exc)
        return None


# Internal sentinel tokens that must never leak to persisted surfaces.
# Mirrors summarization.common.structured._SENTINEL_TAG_RE but also covers
# bracketed/angle forms the LLM sometimes emits mid-text (e.g. ``[RESERVED]``,
# ``<SENTINEL:foo>``) that pollute Obsidian notes and KG node summaries.
_SENTINEL_TEXT_RE = re.compile(
    r"(\[(?:RESERVED|SENTINEL)[^\]]*\])|(<SENTINEL[^>]*>)|(\b_[a-z][a-z0-9_]*_\b)",
    re.IGNORECASE,
)
# Mid-sentence truncation: a sentence ending abruptly mid-word before terminal
# punctuation. When the last non-empty line has visible text but no terminal
# punctuation, the LLM output is truncated and the downstream surface will show
# a half-written sentence. We drop the dangling fragment rather than render it.
_TERMINAL_PUNCT = (".", "!", "?", ":", ";", '"', "'", ")", "]")


def _strip_sentinel_text(text: str) -> str:
    """Remove sentinel tokens from a rendered summary body.

    Returns the original text with sentinel markers deleted and any resulting
    double-spaces collapsed. Leaves newlines intact so markdown structure is
    preserved.
    """
    if not text:
        return text
    cleaned = _SENTINEL_TEXT_RE.sub("", text)
    # Collapse 2+ spaces (but not newlines) introduced by the excision.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def _drop_unterminated_tail(text: str) -> str:
    """Strip a trailing unterminated sentence fragment from a multi-line body.

    Walks lines from the end; while the last non-empty line doesn't end in
    terminal punctuation AND isn't a markdown heading or list marker, drop it.
    Stops as soon as a terminated sentence is found. Empty inputs pass through
    untouched. Single-line inputs are returned as-is (callers decide whether
    to reject them).
    """
    if not text or "\n" not in text:
        return text
    lines = text.split("\n")
    while lines:
        last = lines[-1].rstrip()
        if not last:
            lines.pop()
            continue
        # Preserve markdown headings and list markers even without terminal punct.
        if last.lstrip().startswith(("#", "- ", "* ", "1.", "2.", "3.")):
            break
        if last.endswith(_TERMINAL_PUNCT):
            break
        lines.pop()
    return "\n".join(lines).rstrip() + ("\n" if text.endswith("\n") else "")


def _coerce_detailed_to_markdown(value: Any) -> str:
    """Convert a structured detailed_summary (list-of-dicts or pydantic
    section models) into markdown that ``renderMarkdownLite`` on the
    frontend can parse.

    Returns an empty string for non-list/dict inputs. String inputs pass
    through untouched. This is the single point where a Python ``list`` /
    ``dict`` (shape emitted by the summarization engine and some eval
    register scripts) is rendered to a stable textual surface. Without
    this, callers that forward the raw Python object fall back to
    ``str(list_of_dicts)`` which produces a Python repr with single quotes
    — the exact bug we are fixing.
    """
    if value is None or isinstance(value, str):
        return value or ""
    sections: list[Any]
    if isinstance(value, list):
        sections = value
    elif isinstance(value, dict):
        sections = [value]
    else:
        return ""

    lines: list[str] = []
    for section in sections:
        if hasattr(section, "model_dump"):
            try:
                section = section.model_dump()
            except Exception:
                section = None
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        if lines:
            lines.append("")
        if heading:
            lines.append(f"## {heading}")
        bullets = section.get("bullets") or []
        if isinstance(bullets, list):
            for bullet in bullets:
                text = str(bullet).strip()
                if text:
                    lines.append(f"- {text}")
        sub_sections = section.get("sub_sections") or section.get("subSections") or {}
        if isinstance(sub_sections, dict):
            for sub_heading, sub_bullets in sub_sections.items():
                if not isinstance(sub_bullets, list) or not sub_bullets:
                    continue
                lines.append("")
                lines.append(f"### {str(sub_heading).strip()}")
                for bullet in sub_bullets:
                    text = str(bullet).strip()
                    if text:
                        lines.append(f"- {text}")
    return "\n".join(lines).strip()


def _normalize_summary_text(value: Any) -> str:
    """Normalize + sanitize a summary body for persistence.

    Beyond whitespace/escape normalization, this strips internal sentinel
    tokens (``[RESERVED]``, ``<SENTINEL...>``, ``_schema_fallback_``) that
    must never reach user-facing surfaces, and collapses trailing mid-
    sentence truncation so persisted summaries always end on a complete
    sentence or a structural marker.

    Non-string inputs (list-of-dict section payloads from the engine's
    structured summaries, or a single-dict section) are coerced to
    markdown via :func:`_coerce_detailed_to_markdown` rather than running
    through ``str()`` — which would emit a Python repr with single quotes
    (the iter-23 github regression).
    """
    if value is None:
        raw_text = ""
    elif isinstance(value, (list, dict)):
        raw_text = _coerce_detailed_to_markdown(value)
    else:
        raw_text = str(value)
    raw = (
        raw_text
        .replace("\r\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .strip()
    )
    cleaned = _strip_sentinel_text(raw)
    cleaned = _drop_unterminated_tail(cleaned)
    return cleaned.strip()


def _extract_summary_field_by_regex(text: str, field_name: str) -> str:
    pattern = re.compile(
        rf'"{re.escape(field_name)}"\s*:\s*"((?:\\.|[^"\\])*)"',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return _normalize_summary_text(match.group(1))


def _try_parse_summary_object(raw_text: str | None) -> dict[str, Any] | None:
    cleaned = str(raw_text or "").strip()
    if not cleaned:
        return None

    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE).strip()

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, str):
                nested = json.loads(parsed)
                if isinstance(nested, dict):
                    return nested
        except Exception:
            continue

    regex_brief = _extract_summary_field_by_regex(cleaned, "brief_summary")
    regex_detailed = _extract_summary_field_by_regex(cleaned, "detailed_summary")
    if regex_brief or regex_detailed:
        return {
            "brief_summary": regex_brief,
            "detailed_summary": regex_detailed,
        }

    return None


def extract_summary_parts(raw_summary: str | None, fallback_brief: str | None = None) -> tuple[str, str]:
    """Normalize a summarizer payload into brief + detailed summary strings."""

    fallback_brief_text = _normalize_summary_text(fallback_brief)
    parsed = _try_parse_summary_object(raw_summary)
    if parsed:
        brief = _normalize_summary_text(
            parsed.get("brief_summary")
            or parsed.get("briefSummary")
            or parsed.get("one_line_summary")
            or parsed.get("summary")
        )
        detailed = _normalize_summary_text(
            parsed.get("detailed_summary")
            or parsed.get("detailedSummary")
            or parsed.get("summary")
        )
        if brief or detailed:
            resolved_brief = brief or detailed or fallback_brief_text
            resolved_detailed = detailed or brief or fallback_brief_text
            return (
                resolved_brief or "No summary available for this zettel.",
                resolved_detailed or resolved_brief or "No summary available for this zettel.",
            )

    fallback = fallback_brief_text or _normalize_summary_text(raw_summary) or "No summary available for this zettel."
    return fallback, fallback


def _build_supabase_node_id(source_type: str, title: str) -> str:
    prefix = _SOURCE_PREFIX.get((source_type or "").strip().lower(), "web")
    slug = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:24].rstrip("-")
    slug = slug or "untitled"
    return f"{prefix}-{slug}"


def _file_graph_contains_url(source_url: str) -> bool:
    graph = get_graph()
    normalized_url = str(source_url or "").strip()
    if not normalized_url:
        return False
    return any(str(node.get("url") or "").strip() == normalized_url for node in graph.get("nodes", []))


def _get_cached_existing_types(repo: KGRepository, user_id: str) -> list[str]:
    now = time.monotonic()
    cached = _EXISTING_TYPES_CACHE.get(user_id)
    if cached and cached[0] > now:
        return cached[1]

    try:
        types_list = repo.get_distinct_entity_types(UUID(user_id))
    except Exception:
        types_list = []

    _EXISTING_TYPES_CACHE[user_id] = (now + _EXISTING_TYPES_TTL, types_list)
    return types_list


def _schedule_entity_extraction(
    *,
    repo: KGRepository,
    user_id: str,
    node_id: str,
    title: str,
    detailed_summary: str,
    brief_summary: str,
) -> None:
    try:
        from website.features.kg_features.entity_extractor import EntityExtractor
    except Exception as exc:
        logger.warning("Entity extraction import failed for %s: %s", node_id, exc)
        return

    async def _extract_entities() -> None:
        try:
            logger.info("Entity extraction started for %s", node_id)
            existing_types = _get_cached_existing_types(repo, user_id)
            extractor = EntityExtractor()
            extraction = await asyncio.wait_for(
                extractor.extract(
                    summary=(brief_summary or detailed_summary)[:500],
                    title=title,
                    existing_types=existing_types,
                ),
                timeout=40.0,
            )
            if not extraction.entities:
                logger.info("Entity extraction found 0 entities for %s", node_id)
                return

            current_meta = repo.get_node_metadata(user_id, node_id)
            merged = {
                **current_meta,
                "entities": [entity.model_dump() for entity in extraction.entities],
            }
            repo.update_node_metadata(user_id, node_id, merged)
            logger.info("Extracted %d entities for %s", len(extraction.entities), node_id)
        except asyncio.TimeoutError:
            logger.warning("Entity extraction timed out for %s", node_id)
        except Exception as exc:
            logger.warning("Entity extraction failed for %s: %s", node_id, exc)

    try:
        task = asyncio.create_task(_extract_entities(), name=f"entity-extract-{node_id}")
    except RuntimeError:
        logger.debug("No running event loop for entity extraction on %s", node_id)
        return

    task.add_done_callback(lambda task_ref: task_ref.exception() if not task_ref.cancelled() else None)


async def persist_summarized_result(
    result: dict[str, Any],
    *,
    user_sub: str | None = None,
    captured_on: date | None = None,
) -> PersistenceOutcome:
    """Persist a summarize result using the same KG behavior as ``/api/summarize``."""

    payload = dict(result)
    captured_on = captured_on or date.today()

    explicit_brief = _normalize_summary_text(payload.get("brief_summary"))
    explicit_detailed = _normalize_summary_text(payload.get("detailed_summary"))
    if explicit_brief and explicit_detailed:
        brief_summary = explicit_brief
        detailed_summary = explicit_detailed
    else:
        brief_summary, detailed_summary = extract_summary_parts(
            payload.get("summary"),
            payload.get("brief_summary"),
        )
    payload["brief_summary"] = brief_summary
    payload["detailed_summary"] = detailed_summary
    payload["summary"] = detailed_summary
    payload["captured_at"] = captured_on.isoformat()

    supabase_node_id: str | None = None
    supabase_saved = False
    supabase_duplicate = False
    kg_user_id: str | None = None
    source_url = str(payload["source_url"])
    file_duplicate = False

    v2_scope = get_supabase_v2_scope(user_sub)
    sb = None if v2_scope else get_supabase_scope(user_sub)
    file_duplicate = _file_graph_contains_url(source_url)

    if v2_scope:
        repo_v2, profile_id, workspace_id = v2_scope
        kg_user_id = str(profile_id)
        try:
            supabase_node_id, supabase_saved, supabase_duplicate = await _persist_supabase_v2_zettel(
                payload=payload,
                repo=repo_v2,
                workspace_id=workspace_id,
                captured_on=captured_on,
                detailed_summary=detailed_summary,
            )
        except Exception as exc:
            logger.warning("Failed to add zettel to Supabase v2: %s", exc)

    if sb and not v2_scope:
        repo, kg_user_id = sb
        try:
            supabase_node_id, supabase_saved, supabase_duplicate = await _persist_supabase_node(
                payload=payload,
                repo=repo,
                kg_user_id=kg_user_id,
                captured_on=captured_on,
                brief_summary=brief_summary,
                detailed_summary=detailed_summary,
            )
        except Exception as exc:
            logger.warning("Failed to add node to Supabase: %s", exc)

    file_node_id = _persist_file_node(payload, skip_duplicate=file_duplicate or supabase_duplicate)
    if file_node_id:
        payload["node_id"] = file_node_id
    payload.pop("raw_text", None)
    payload.pop("raw_metadata", None)

    return PersistenceOutcome(
        result=payload,
        file_node_id=file_node_id,
        supabase_node_id=supabase_node_id,
        supabase_saved=supabase_saved,
        supabase_duplicate=supabase_duplicate,
        kg_user_id=kg_user_id,
    )


async def _persist_supabase_v2_zettel(
    *,
    payload: dict[str, Any],
    repo: V2ContentRepository,
    workspace_id: UUID,
    captured_on: date,
    detailed_summary: str,
) -> tuple[str, bool, bool]:
    normalized_url = str(payload["source_url"])
    body_md = str(payload.get("raw_text") or detailed_summary or payload.get("summary") or "")
    content_hash = hashlib.sha256(body_md.encode("utf-8")).digest()

    zettel = CanonicalZettelCreate(
        normalized_url=normalized_url,
        content_hash=content_hash,
        source_type=str(payload.get("source_type") or "web"),
        title=polish(str(payload["title"])),
        body_md=body_md,
        publication_date=captured_on.isoformat(),
        source_metadata={
            "source_url": normalized_url,
            "metadata": payload.get("metadata") or {},
        },
    )
    workspace = WorkspaceZettelCreate(
        workspace_id=workspace_id,
        ai_summary=_encode_summary_payload(payload),
        ai_summary_engine_version=str(payload.get("engine_version") or ""),
        user_tags=list(rewrite_tags(payload.get("tags", []) or [])),
        added_via="website",
    )
    chunk_text = detailed_summary or body_md
    chunks = [
        CanonicalChunkCreate(
            chunk_idx=0,
            content=chunk_text,
            content_hash=hashlib.sha256(chunk_text.encode("utf-8")).digest(),
            chunk_type="semantic",
            token_count=max(1, len(chunk_text.split())),
        )
    ] if chunk_text else []
    result = await asyncio.to_thread(
        repo.upsert_canonical_zettel,
        zettel,
        workspace=workspace,
        chunks=chunks,
    )
    return str(result.canonical_zettel_id), result.was_new, not result.was_new


def _encode_summary_payload(payload: dict[str, Any]) -> str:
    """Serialize brief + detailed summaries as JSON so both survive persistence.

    Applies the deterministic polish + caveat-strip stack at the WRITE
    boundary so every persisted row is born clean. Idempotent — re-encoding
    an already-polished payload is a no-op. This complements the read-time
    polish in ``summary_normalizer.normalize_summary_for_wire`` so non-API
    consumers (Telegram bot, Obsidian export, GitHub writer) see the same
    cleaned text.
    """
    brief = _normalize_summary_text(payload.get("brief_summary"))
    detailed = _normalize_summary_text(payload.get("detailed_summary") or payload.get("summary"))
    if not brief and not detailed:
        return ""
    cleaned_brief = polish(strip_caveats(brief))
    detailed_value = detailed or brief
    cleaned_detailed = polish(strip_caveats(detailed_value)) if detailed_value else cleaned_brief
    return json.dumps(
        {"brief_summary": cleaned_brief, "detailed_summary": cleaned_detailed},
        ensure_ascii=False,
    )


def _persist_file_node(payload: dict[str, Any], *, skip_duplicate: bool) -> str | None:
    if skip_duplicate:
        return None
    try:
        return add_node(
            title=polish(str(payload["title"])),
            source_type=str(payload["source_type"]),
            source_url=str(payload["source_url"]),
            summary=_encode_summary_payload(payload),
            tags=list(rewrite_tags(payload.get("tags", []) or [])),
        )
    except Exception as exc:
        logger.warning("Failed to add node to file KG: %s", exc)
        return None


async def _persist_supabase_node(
    *,
    payload: dict[str, Any],
    repo: KGRepository,
    kg_user_id: str,
    captured_on: date,
    brief_summary: str,
    detailed_summary: str,
) -> tuple[str, bool, bool]:
    user_uuid = UUID(kg_user_id)
    node_id = _build_supabase_node_id(
        str(payload.get("source_type", "")),
        str(payload.get("title", "")),
    )
    if repo.node_exists(user_uuid, str(payload["source_url"])):
        return node_id, False, True

    node_create = _build_supabase_node_payload(
        payload=payload,
        node_id=node_id,
        captured_on=captured_on,
        embedding=None,
    )
    node_id = node_create.id
    repo.add_node(user_uuid, node_create)

    _schedule_embedding_and_links(
        repo=repo,
        kg_user_id=kg_user_id,
        user_uuid=user_uuid,
        node_id=node_create.id,
        payload=payload,
    )

    if get_settings().rag_chunks_enabled:
        _schedule_rag_chunks(
            payload=payload,
            user_uuid=user_uuid,
            node_id=node_create.id,
        )

    _schedule_entity_extraction(
        repo=repo,
        user_id=kg_user_id,
        node_id=node_create.id,
        title=str(payload["title"]),
        detailed_summary=detailed_summary,
        brief_summary=brief_summary,
    )
    return node_id, True, False


def _schedule_embedding_and_links(
    *,
    repo: KGRepository,
    kg_user_id: str,
    user_uuid: UUID,
    node_id: str,
    payload: dict[str, Any],
) -> None:
    """Generate embedding, persist it on the node, and create semantic links — off critical path."""

    async def _run() -> None:
        try:
            embedding = await asyncio.to_thread(_generate_node_embedding, payload)
            if not embedding:
                return
            await asyncio.to_thread(
                repo.update_node_embedding, user_uuid, node_id, embedding
            )
            await asyncio.to_thread(
                _create_semantic_links,
                repo=repo,
                kg_user_id=kg_user_id,
                user_uuid=user_uuid,
                node_id=node_id,
                embedding=embedding,
            )
        except Exception as exc:
            logger.warning("Background embedding/link failed for %s: %s", node_id, exc)

    try:
        task = asyncio.create_task(_run(), name=f"embed-link-{node_id}")
    except RuntimeError:
        logger.debug("No running event loop for embedding on %s", node_id)
        return
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _schedule_rag_chunks(
    *,
    payload: dict[str, Any],
    user_uuid: UUID,
    node_id: str,
) -> None:
    """Ingest RAG chunks off critical path so /api/summarize returns faster."""

    async def _run() -> None:
        try:
            from website.features.rag_pipeline.ingest.hook import ingest_node_chunks

            await ingest_node_chunks(
                payload=payload,
                user_uuid=user_uuid,
                node_id=node_id,
            )
        except Exception as exc:
            logger.warning("Background RAG chunk ingest failed for %s: %s", node_id, exc)

    try:
        task = asyncio.create_task(_run(), name=f"rag-chunks-{node_id}")
    except RuntimeError:
        logger.debug("No running event loop for RAG chunks on %s", node_id)
        return
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _generate_node_embedding(payload: dict[str, Any]) -> list[float] | None:
    from website.features.kg_features.embeddings import generate_embedding

    try:
        embed_input = (
            f"{payload.get('title', '')}\n\n"
            f"{payload.get('summary') or payload.get('brief_summary') or ''}"
        )
        return generate_embedding(embed_input.strip()[:2000]) or None
    except Exception as exc:
        logger.warning("Embedding generation failed: %s", exc)
        return None


def _build_supabase_node_payload(
    *,
    payload: dict[str, Any],
    node_id: str,
    captured_on: date,
    embedding: list[float] | None,
) -> KGNodeCreate:
    node_metadata: dict[str, Any] = {}
    if embedding:
        node_metadata["embedding_model"] = "gemini-embedding-001"
    caller_metadata = payload.get("metadata") or {}
    if isinstance(caller_metadata, dict) and caller_metadata:
        # Caller-supplied metadata (e.g. eval_iter, eval_source, composite,
        # eval_branch from register scripts) must land in kg_nodes.metadata,
        # but engine-generated keys (embedding_model, etc.) win on conflict.
        node_metadata = {**caller_metadata, **node_metadata}
    return KGNodeCreate(
        id=node_id,
        name=polish(str(payload["title"])),
        source_type=str(payload["source_type"]),
        tags=list(rewrite_tags(payload.get("tags", []) or [])),
        url=str(payload["source_url"]),
        summary=_encode_summary_payload(payload),
        node_date=captured_on,
        embedding=embedding,
        metadata=node_metadata,
    )


def _create_semantic_links(
    *,
    repo: KGRepository,
    kg_user_id: str,
    user_uuid: UUID,
    node_id: str,
    embedding: list[float],
) -> None:
    try:
        similar = repo.match_similar_nodes(
            kg_user_id,
            embedding,
            threshold=0.75,
            limit=5,
        )
        for hit in similar:
            hit_id = hit.get("node_id") or hit.get("id")
            hit_similarity = float(hit.get("similarity") or 0.0)
            if hit_id and hit_id != node_id and hit_similarity >= 0.75:
                repo.add_semantic_link(
                    user_id=user_uuid,
                    source_id=node_id,
                    target_id=hit_id,
                    similarity=hit_similarity,
                )
    except Exception as exc:
        logger.warning("Semantic auto-linking failed: %s", exc, exc_info=True)
