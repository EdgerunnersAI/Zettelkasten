"""FastAPI routes for summarization engine v2."""
from __future__ import annotations

import os
from json import dumps
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from website.api.auth import get_optional_user
from website.features.api_key_switching.key_pool import (
    GeminiKeyPool,
    _load_keys_from_file,
    candidate_api_env_paths,
)
from website.features.summarization_engine.api.models import BatchV2Request, SummarizeV2Request, SummarizeV2Response
from website.features.summarization_engine.batch.processor import BatchProcessor, progress_stream
from website.features.summarization_engine.core.config import load_config
from website.features.summarization_engine.core.confidence import grade as grade_confidence
from website.features.summarization_engine.core.errors import UnsupportedVideoError
from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.orchestrator import summarize_url_bundle
from website.features.summarization_engine.writers.supabase import SupabaseWriter

router = APIRouter(prefix="/api/v2", tags=["summarization-engine-v2"])


@router.post("/summarize", response_model=SummarizeV2Response)
async def summarize_v2(
    request: SummarizeV2Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    user_id = _user_id(user)
    client = _gemini_client()
    try:
        bundle = await summarize_url_bundle(request.url, user_id=user_id, gemini_client=client)
    except UnsupportedVideoError as exc:
        # H4/T7: preflight hard-fail (private/removed/livestream/premiere/members-only).
        # Distinct from H2's post-chain 422 (metadata_only + <500 chars).
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_video_type",
                "reason": exc.reason,
                "confidence": "insufficient",
                "confidence_reason": f"Video type cannot be ingested: {exc.reason}",
                "quality_signals": {"input_chars": 0, "source_tier": "preflight_refused"},
            },
        )
    result = bundle.summary_result

    # H2/C4: two-tier hallucination prevention. Insufficient content + metadata-only
    # tier -> HTTP 422 refusal (mirrors OpenAI structured-outputs refusal pattern).
    ingest_meta = bundle.ingest_result.metadata or {}
    source_tier = str(ingest_meta.get("tier_used") or "")
    raw_text_len = len(bundle.ingest_result.raw_text or "")
    conf, reason = grade_confidence(raw_text_len=raw_text_len, source_tier=source_tier)
    quality_signals = {"input_chars": raw_text_len, "source_tier": source_tier}
    if conf == "insufficient":
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_context",
                "confidence": "insufficient",
                "confidence_reason": reason,
                "quality_signals": quality_signals,
            },
        )

    writers = []
    if request.write_to_supabase:
        writers.append(await SupabaseWriter().write(result, user_id=user_id))
    return SummarizeV2Response(
        summary=result.model_dump(mode="json"),
        writers=writers,
        confidence=conf,
        confidence_reason=reason or None,
        quality_signals=quality_signals,
    )


@router.post("/batch")
async def batch_v2(
    request: BatchV2Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    user_id = _user_id(user)
    writers = [SupabaseWriter()] if request.write_to_supabase else []
    processor = BatchProcessor(user_id=user_id, gemini_client=_gemini_client(), writers=writers)
    payload = {"urls": [{"url": url} for url in request.urls]}
    return await processor.run(input_bytes=dumps(payload).encode(), filename="request.json")


@router.post("/batch/upload")
async def batch_upload_v2(
    file: UploadFile,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    contents = await file.read()
    processor = BatchProcessor(user_id=_user_id(user), gemini_client=_gemini_client())
    return await processor.run(input_bytes=contents, filename=file.filename or "upload.csv")


@router.post("/batch/stream")
async def batch_stream_v2(
    request: BatchV2Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    result = await batch_v2(request, user)
    return EventSourceResponse(progress_stream(result))


def _user_id(user: dict | None) -> UUID:
    raw = (user or {}).get("sub") or "00000000-0000-0000-0000-000000000001"
    try:
        return UUID(str(raw))
    except ValueError:
        return UUID("00000000-0000-0000-0000-000000000001")


def _gemini_client() -> TieredGeminiClient:
    keys: list[str | tuple[str, str]] = []
    keys.extend(
        (os.environ[name], "free")
        for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2")
        if os.environ.get(name)
    )
    if os.environ.get("GEMINI_API_KEYS"):
        keys.extend(
            (key.strip(), "free")
            for key in os.environ["GEMINI_API_KEYS"].split(",")
            if key.strip()
        )
    if not keys:
        for path in candidate_api_env_paths():
            loaded = _load_keys_from_file(str(path))
            if loaded:
                keys.extend(loaded)
                break
    if not keys:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")
    return TieredGeminiClient(GeminiKeyPool(keys), load_config())
