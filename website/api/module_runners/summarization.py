"""Runner for the website Add Zettel summarization pipeline.

This module is intentionally importable from both FastAPI routes and CLI tools.
It is the API-facing runner that boots the summarization engine, normalizes the
engine output into the website DTO, and then calls canonical persistence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from website.core.summary_rendering import render_detailed_summary

_SUMMARIZE_SEMAPHORE = asyncio.Semaphore(2)


class SummaryDTO(BaseModel):
    title: str
    summary: str
    brief_summary: str
    detailed_summary: str
    tags: list[str]
    source_type: str
    source_url: str
    one_line_summary: str
    tokens_used: int
    latency_ms: int
    metadata: dict[str, Any]


class PersistenceDTO(BaseModel):
    requested: bool
    persisted: bool
    file_store: bool
    supabase: bool
    duplicate: bool


class QualityDTO(BaseModel):
    confidence: str
    confidence_reason: str | None = None
    quality_signals: dict[str, Any] = Field(default_factory=dict)


class AddZettelPipelineOutput(BaseModel):
    status: Literal["succeeded", "accepted", "failed"]
    operation_id: str
    summary: SummaryDTO | None = None
    persistence: PersistenceDTO
    quality: QualityDTO
    node_id: str | None = None
    workspace_zettel_id: str | None = None
    status_url: str | None = None


GeminiClientFactory = Callable[[], Any]


def default_gemini_client() -> Any:
    from website.features.summarization_engine.core.client_factory import (
        build_tiered_gemini_client,
    )

    return build_tiered_gemini_client()


async def require_entitlement(*args: Any, **kwargs: Any) -> Any:
    from website.features.user_pricing.entitlements import require_entitlement as _impl

    return await _impl(*args, **kwargs)


async def consume_entitlement(*args: Any, **kwargs: Any) -> Any:
    from website.features.user_pricing.entitlements import consume_entitlement as _impl

    return await _impl(*args, **kwargs)


async def summarize_url_bundle(*args: Any, **kwargs: Any) -> Any:
    from website.features.summarization_engine.core.orchestrator import (
        summarize_url_bundle as _impl,
    )

    return await _impl(*args, **kwargs)


async def persist_summarized_result(*args: Any, **kwargs: Any) -> Any:
    from website.core.persist import persist_summarized_result as _impl

    return await _impl(*args, **kwargs)


async def resolve_redirects(*args: Any, **kwargs: Any) -> Any:
    from website.core.url_utils import resolve_redirects as _impl

    return await _impl(*args, **kwargs)


def normalize_url(*args: Any, **kwargs: Any) -> Any:
    from website.core.url_utils import normalize_url as _impl

    return _impl(*args, **kwargs)


async def run_add_zettel_pipeline(
    *,
    url: str,
    client_action_id: str,
    persist: bool,
    user: dict | None,
    effective_user_id: UUID,
    gemini_client_factory: GeminiClientFactory = default_gemini_client,
) -> dict[str, Any]:
    """Run Add Zettel end-to-end for API and CLI callers."""

    from website.features.user_pricing.models import Meter

    user_sub = str(effective_user_id)
    await require_entitlement(Meter.ZETTEL, user, action_id=client_action_id)

    resolved = await resolve_redirects(url)
    normalized = normalize_url(resolved)

    async with _SUMMARIZE_SEMAPHORE:
        bundle = await summarize_url_bundle(
            normalized,
            user_id=effective_user_id,
            gemini_client=gemini_client_factory(),
        )

    summary = summary_dto(bundle)
    quality = quality_dto(bundle)
    outcome: PersistenceOutcome | None = None
    if persist:
        outcome = await persist_summarized_result(
            summary.model_dump(mode="json"),
            user_sub=user_sub,
        )
        await consume_entitlement(Meter.ZETTEL, user, action_id=client_action_id)

    return AddZettelPipelineOutput(
        status="succeeded",
        operation_id=client_action_id,
        summary=summary,
        persistence=persistence_dto(persist, outcome),
        quality=quality,
        node_id=outcome.file_node_id if outcome else None,
        workspace_zettel_id=outcome.supabase_node_id if outcome else None,
    ).model_dump(mode="json")


def summary_dto(bundle: Any) -> SummaryDTO:
    result = bundle.summary_result
    ingest = bundle.ingest_result
    metadata = result.metadata.model_dump(mode="json", exclude_none=True)
    detailed = render_detailed_summary(result.detailed_summary) or result.brief_summary
    summary = SummaryDTO(
        title=result.mini_title,
        summary=detailed,
        brief_summary=result.brief_summary,
        detailed_summary=detailed,
        tags=list(result.tags or []),
        source_type=result.metadata.source_type.value,
        source_url=result.metadata.url,
        one_line_summary=result.brief_summary,
        tokens_used=result.metadata.total_tokens_used,
        latency_ms=result.metadata.total_latency_ms,
        metadata=metadata,
    )
    if ingest is not None:
        summary.metadata.setdefault("raw_metadata", dict(ingest.metadata or {}))
    return summary


def quality_dto(bundle: Any) -> QualityDTO:
    from website.features.summarization_engine.core.confidence import grade as grade_confidence

    ingest = bundle.ingest_result
    source_tier = str((ingest.metadata or {}).get("tier_used") or "")
    raw_text_len = len(ingest.raw_text or "")
    confidence, reason = grade_confidence(
        raw_text_len=raw_text_len,
        source_tier=source_tier,
    )
    return QualityDTO(
        confidence=confidence,
        confidence_reason=reason,
        quality_signals={"input_chars": raw_text_len, "source_tier": source_tier},
    )


def persistence_dto(requested: bool, outcome: Any | None) -> PersistenceDTO:
    if not requested or outcome is None:
        return PersistenceDTO(
            requested=requested,
            persisted=False,
            file_store=False,
            supabase=False,
            duplicate=False,
        )
    return PersistenceDTO(
        requested=True,
        persisted=outcome.file_saved or outcome.supabase_saved or outcome.supabase_duplicate,
        file_store=outcome.file_saved,
        supabase=outcome.supabase_saved,
        duplicate=outcome.supabase_duplicate,
    )


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if key.strip() and key.strip() not in {"", "#"}:
            import os

            os.environ.setdefault(key.strip(), value)


def _load_local_env() -> None:
    root = Path.cwd()
    for candidate in (
        root / ".env",
        root / ".env.v2",
        root / "supabase" / ".env",
    ):
        _load_env_file(candidate)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Add Zettel summarization engine facade from CLI.",
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--user-id", required=True, help="Supabase Auth UUID to write under")
    parser.add_argument("--client-action-id", default="cli-add-zettel")
    parser.add_argument("--persist", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--load-env", action="store_true", help="Load .env/.env.v2/supabase/.env first")
    return parser.parse_args()


async def _cli() -> int:
    args = _parse_args()
    if args.load_env:
        _load_local_env()
    result = await run_add_zettel_pipeline(
        url=args.url,
        client_action_id=args.client_action_id,
        persist=args.persist,
        user={"sub": args.user_id},
        effective_user_id=UUID(str(args.user_id)),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
