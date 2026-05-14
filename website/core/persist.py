"""Canonical persistence helpers for summarize → write-everywhere fanout.

This module is the **single source of truth** for persisting a summarize
result into the knowledge graph. Every ingest path should call
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
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from website.core.graph_store import _SOURCE_PREFIX, add_node, get_graph
from website.core.db_version import use_supabase_v2
from website.core.settings import get_settings
from website.core.supabase_v2.models import CanonicalChunkCreate, CanonicalZettelCreate, WorkspaceZettelCreate
from website.core.supabase_v2.repositories.content_repository import ContentRepository as V2ContentRepository
from website.core.supabase_v2.repositories.core_repository import CoreRepository as V2CoreRepository
from website.core.supabase_v2.client import get_v2_client as _get_v2_client
from website.core.text_polish import polish, rewrite_tags, strip_caveats

# Keep a forward reference to supabase Client only for typing; importing at
# module top would force the supabase package even when v2 is disabled.
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from supabase import Client  # noqa: F401

logger = logging.getLogger("website.core.persist")

_v2_core_repo: V2CoreRepository | None = None
_v2_content_repo: V2ContentRepository | None = None


@dataclass(slots=True)
class PersistenceOutcome:
    """Result of writing a summarized artifact into the knowledge graph."""

    result: dict[str, Any]
    file_node_id: str | None = None
    supabase_node_id: str | None = None
    file_saved: bool = False  # True if file-backed graph.json was written
    supabase_saved: bool = False
    supabase_duplicate: bool = False
    kg_user_id: str | None = None


def get_supabase_v2_scope_for_read(
    user_sub: str | None = None,
) -> tuple[V2ContentRepository, UUID, list[UUID]] | None:
    """Return ``(content_repo, profile_id, workspace_ids)`` for read paths.

    Mirrors :func:`get_supabase_v2_scope` but enumerates *every* workspace the
    profile is a member of (default-workspace-only is too narrow for the graph
    read path, which fans across personal + shared workspaces). Returns
    ``None`` when v2 is not in use, the JWT subject is not a UUID, or the
    profile has no workspace memberships.
    """
    global _v2_core_repo, _v2_content_repo

    if not use_supabase_v2() or not user_sub:
        return None
    try:
        profile_id = UUID(str(user_sub))
    except (TypeError, ValueError):
        logger.info(
            "DB v2 read scope requires UUID auth subject; falling back for user_sub=%r",
            user_sub,
        )
        return None

    try:
        _v2_core_repo = _v2_core_repo or V2CoreRepository()
        _v2_content_repo = _v2_content_repo or V2ContentRepository()
        # Enumerate all workspaces the profile is a member of via the same
        # core.workspace_members table CoreRepository.get_default_workspace_id
        # already uses; service-role client bypasses RLS for read fan-out.
        response = (
            _v2_core_repo._client.schema("core")
            .table("workspace_members")
            .select("workspace_id")
            .eq("profile_id", str(profile_id))
            .order("added_at")
            .execute()
        )
        workspace_ids = [
            UUID(str(row["workspace_id"])) for row in (response.data or []) if row.get("workspace_id")
        ]
        if not workspace_ids:
            return None
        return _v2_content_repo, profile_id, workspace_ids
    except Exception as exc:
        logger.warning("Supabase v2 read scope lookup failed, falling back: %s", exc)
        return None


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


def get_billing_scope(user_sub: str | UUID) -> tuple["Client", UUID]:
    """Return ``(v2 client, profile_id)`` for billing.* operations.

    Hard-fails on non-UUID ``user_sub``: per operator decision (2026-05-10,
    Phase 8.0 v2 purge), legacy non-UUID render_user_ids are not supported in
    the v2 billing path. Both production users are UUID-authed; v1 fallback
    branches in ``user_pricing/repository.py`` are dead code and have been
    removed (closes H2 + H3). See
    ``docs/db-v2/phase-9-pricing-enforcement-plan.md`` for the broader
    pricing-enforcement plan that replaces v1's request-counter model.
    """
    try:
        profile_id = user_sub if isinstance(user_sub, UUID) else UUID(str(user_sub))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"v2 billing requires a Supabase auth UUID; got {user_sub!r}"
        ) from exc
    return _get_v2_client(), profile_id


# Internal sentinel tokens that must never leak to persisted surfaces.
# Mirrors summarization.common.structured._SENTINEL_TAG_RE but also covers
# bracketed/angle forms the LLM sometimes emits mid-text (e.g. ``[RESERVED]``,
# ``<SENTINEL:foo>``) that pollute persisted KG node summaries.
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


# Phase 8.0.3 B+: removed v1-only helpers _get_cached_existing_types and
# _schedule_entity_extraction. They wrote to ``public.kg_nodes`` (dropped in
# Phase 6) via KGRepository; v2 entity extraction will be re-introduced as a
# pipeline against ``content.workspace_zettels`` in a later iter.


async def persist_summarized_result(
    result: dict[str, Any],
    *,
    user_sub: str | None = None,
    captured_on: date | None = None,
) -> PersistenceOutcome:
    """Persist a summarized result using the canonical Add Zettel KG behavior."""

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

    # Phase 8.0.3 B+: v1 fallback branch (KGRepository.add_node + semantic
    # auto-link) was removed — v1 ``kg_nodes`` / ``kg_users`` tables were
    # dropped in Phase 6, so the call would 500 against the live DB.

    file_node_id = _persist_file_node(payload, skip_duplicate=file_duplicate or supabase_duplicate)
    if file_node_id:
        payload["node_id"] = file_node_id
    payload.pop("raw_text", None)
    payload.pop("raw_metadata", None)

    return PersistenceOutcome(
        result=payload,
        file_node_id=file_node_id,
        supabase_node_id=supabase_node_id,
        file_saved=file_node_id is not None,
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
    persisted_id = result.workspace_zettel_id or result.canonical_zettel_id
    return str(persisted_id), result.workspace_zettel_id is not None, not result.was_new


def _encode_summary_payload(payload: dict[str, Any]) -> str:
    """Serialize brief + detailed summaries as JSON so both survive persistence.

    Applies the deterministic polish + caveat-strip stack at the WRITE
    boundary so every persisted row is born clean. Idempotent — re-encoding
    an already-polished payload is a no-op. Complements the read-time polish
    in ``summary_normalizer.normalize_summary_for_wire``.
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


# Phase 8.0.3 B+: removed v1-only helpers _persist_supabase_node and
# _schedule_embedding_and_links. They both took a v1 ``KGRepository`` and
# wrote to ``public.kg_nodes`` / ``public.kg_node_links`` (dropped in Phase 6).
# v2 zettel persist runs through ``_persist_supabase_v2_zettel`` above.


def _schedule_rag_chunks(
    *,
    payload: dict[str, Any],
    user_uuid: UUID,
    node_id: str,
) -> None:
    """Ingest RAG chunks off critical path so Add Zettel returns faster."""

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


# Phase 8.0.3 B+: removed v1-only helpers _build_supabase_node_payload (built
# a ``KGNodeCreate`` for ``public.kg_nodes``) and _create_semantic_links
# (called ``KGRepository.match_similar_nodes`` + ``add_semantic_link`` against
# ``public.kg_node_links``). Both v1 tables were dropped in Phase 6; v2
# canonical chunks + semantic edges are produced by the rag_pipeline ingest
# hook against ``content.canonical_chunks`` / ``rag.zettel_links_v2``.
