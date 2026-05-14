"""Website Add Zettel API facade."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from website.api.auth import get_optional_user
from website.api.module_runners.summarization import (
    AddZettelPipelineOutput as AddZettelResponse,
    PersistenceDTO,
    QualityDTO,
    default_gemini_client,
    persistence_dto,
    run_add_zettel_pipeline,
)
from website.core.url_utils import validate_url
from website.features.summarization_engine.core.errors import (
    ExtractionConfidenceError,
    RoutingError,
    UnsupportedVideoError,
)

logger = logging.getLogger("website.api.zettels")
router = APIRouter(prefix="/api")

_EXPECTED_USERS_PATH = Path(__file__).resolve().parents[2] / "ops" / "deploy" / "expected_users.json"
_SENTINEL_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
_ZORO_USER_ID: UUID | None = None

_RATE_STORE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 10
_RATE_WINDOW_SECONDS = 60
_AUTO_ACCEPT_AFTER_SECONDS = 8.0
_OPERATION_TTL_SECONDS = 15 * 60
_MAX_OPERATION_RECORDS = 128
_MAX_IDEMPOTENCY_RECORDS = 128

_IDEMPOTENCY_CACHE: "OrderedDict[tuple[str, str], tuple[float, str, dict[str, Any]]]" = OrderedDict()
_OPERATIONS: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
_OPERATION_TASKS: dict[str, asyncio.Task] = {}
_IN_FLIGHT: dict[tuple[str, str], tuple[str, str]] = {}


class AddZettelRequest(BaseModel):
    url: str
    client_action_id: str = Field(min_length=1, max_length=160)
    persist: bool = True
    surface: Literal["landing", "home", "zettels"]
    mode: Literal["sync", "auto"] = "sync"

    @field_validator("url")
    @classmethod
    def validate_url_field(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL is required")
        if len(value) > 2048:
            raise ValueError("URL too long (max 2048 characters)")
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if not validate_url(value):
            raise ValueError("URL is invalid or blocked")
        return value


def _problem(
    *,
    status_code: int,
    title: str,
    detail: Any,
    operation_id: str | None = None,
    type_slug: str = "add-zettel-failed",
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://zettelkasten.in/problems/errors/{type_slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": f"/api/zettels/add/{operation_id}" if operation_id else "/api/zettels/add",
    }
    if operation_id:
        body["operation_id"] = operation_id
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status_code, media_type="application/problem+json")


def _check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    _RATE_STORE[ip] = [t for t in _RATE_STORE[ip] if now - t < _RATE_WINDOW_SECONDS]
    if len(_RATE_STORE[ip]) >= _RATE_LIMIT:
        return False
    _RATE_STORE[ip].append(now)
    return True


def _zoro_user_id() -> UUID:
    global _ZORO_USER_ID
    if _ZORO_USER_ID is not None:
        return _ZORO_USER_ID
    try:
        raw = json.loads(_EXPECTED_USERS_PATH.read_text(encoding="utf-8"))
        _ZORO_USER_ID = UUID(str(raw["_canonical_zoro"]))
    except Exception:
        logger.exception("Failed to load Zoro auth UUID; falling back to sentinel user")
        _ZORO_USER_ID = _SENTINEL_USER_ID
    return _ZORO_USER_ID


def _effective_user_id(user: dict | None) -> UUID:
    raw = (user or {}).get("sub")
    if raw:
        try:
            return UUID(str(raw))
        except ValueError:
            logger.warning("Ignoring non-UUID auth sub for Add Zettel")
    return _zoro_user_id()


_gemini_client = default_gemini_client


def _request_hash(body: AddZettelRequest) -> str:
    import hashlib

    fingerprint = body.model_dump(mode="json")
    encoded = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _idempotency_conflict(operation_id: str) -> JSONResponse:
    return _problem(
        status_code=409,
        title="Idempotency key reused with a different request",
        detail="Generate a new client_action_id when changing the URL or Add Zettel options.",
        operation_id=operation_id,
        type_slug="idempotency-conflict",
    )


def _cache_get(key: tuple[str, str], request_hash: str) -> dict[str, Any] | JSONResponse | None:
    record = _IDEMPOTENCY_CACHE.get(key)
    if not record:
        return None
    ts, cached_hash, value = record
    if time.monotonic() - ts > _OPERATION_TTL_SECONDS:
        _IDEMPOTENCY_CACHE.pop(key, None)
        return None
    if cached_hash != request_hash:
        return _idempotency_conflict(key[1])
    _IDEMPOTENCY_CACHE.move_to_end(key)
    return value


def _cache_put(key: tuple[str, str], request_hash: str, value: dict[str, Any]) -> None:
    _IDEMPOTENCY_CACHE[key] = (time.monotonic(), request_hash, value)
    _IDEMPOTENCY_CACHE.move_to_end(key)
    while len(_IDEMPOTENCY_CACHE) > _MAX_IDEMPOTENCY_RECORDS:
        _IDEMPOTENCY_CACHE.popitem(last=False)


def _operation_put(operation_id: str, value: dict[str, Any]) -> None:
    _OPERATIONS[operation_id] = (time.monotonic(), value)
    _OPERATIONS.move_to_end(operation_id)
    while len(_OPERATIONS) > _MAX_OPERATION_RECORDS:
        old_id, _ = _OPERATIONS.popitem(last=False)
        old_task = _OPERATION_TASKS.pop(old_id, None)
        if old_task and not old_task.done():
            old_task.cancel()


def _operation_get(operation_id: str) -> dict[str, Any] | None:
    record = _OPERATIONS.get(operation_id)
    if not record:
        return None
    ts, value = record
    if time.monotonic() - ts > _OPERATION_TTL_SECONDS:
        _OPERATIONS.pop(operation_id, None)
        _OPERATION_TASKS.pop(operation_id, None)
        return None
    _OPERATIONS.move_to_end(operation_id)
    return value


def _store_operation_result(
    task: asyncio.Task,
    *,
    operation_id: str,
    cache_key: tuple[str, str],
    request_hash: str,
    persist_requested: bool,
) -> None:
    try:
        result = task.result()
    except Exception as exc:
        logger.exception("Background Add Zettel operation failed")
        result = AddZettelResponse(
            status="failed",
            operation_id=operation_id,
            persistence=persistence_dto(persist_requested, None),
            quality=QualityDTO(
                confidence="failed",
                confidence_reason=str(exc),
            ),
        ).model_dump(mode="json")
    else:
        _cache_put(cache_key, request_hash, result)
    _operation_put(operation_id, result)
    _OPERATION_TASKS.pop(operation_id, None)
    _IN_FLIGHT.pop(cache_key, None)


def _invalidate_graph(user_sub: str | None, persisted: bool) -> None:
    if not persisted:
        return
    try:
        from website.api import routes as routes_mod

        routes_mod.invalidate_user_graph(user_sub)
        routes_mod._graph_cache_global = None
        routes_mod._graph_cache_global_ts = 0
    except Exception:
        logger.exception("Failed to invalidate graph cache after Add Zettel")


async def _run_add_zettel(
    body: AddZettelRequest,
    *,
    user: dict | None,
    effective_user_id: UUID,
) -> dict[str, Any]:
    user_sub = str(effective_user_id)
    result = await run_add_zettel_pipeline(
        url=body.url,
        client_action_id=body.client_action_id,
        persist=body.persist,
        user=user,
        effective_user_id=effective_user_id,
        gemini_client_factory=_gemini_client,
    )
    persistence = PersistenceDTO.model_validate(result["persistence"])
    _invalidate_graph(user_sub if user else None, persistence.persisted)
    return result


@router.post("/zettels/add", response_model=AddZettelResponse)
async def add_zettel(
    body: AddZettelRequest,
    request: Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return _problem(
            status_code=429,
            title="Too many Add Zettel requests",
            detail="Please wait a minute before trying again.",
            operation_id=body.client_action_id,
            type_slug="rate-limited",
        )

    effective_user_id = _effective_user_id(user)
    cache_key = (str(effective_user_id), body.client_action_id)
    request_hash = _request_hash(body)
    cached = _cache_get(cache_key, request_hash)
    if cached is not None:
        return cached
    in_flight = _IN_FLIGHT.get(cache_key)
    if in_flight is not None:
        running_hash, operation_id = in_flight
        if running_hash != request_hash:
            return _idempotency_conflict(body.client_action_id)
        existing = _operation_get(operation_id)
        if existing is None:
            existing = AddZettelResponse(
                status="accepted",
                operation_id=operation_id,
                persistence=persistence_dto(body.persist, None),
                quality=QualityDTO(confidence="pending"),
                status_url=f"/api/operations/{operation_id}",
            ).model_dump(mode="json")
        return JSONResponse(
            existing,
            status_code=202 if existing.get("status") == "accepted" else 200,
            headers={
                "Location": f"/api/operations/{operation_id}",
                "Retry-After": "3",
            },
        )

    try:
        work = asyncio.create_task(
            _run_add_zettel(body, user=user, effective_user_id=effective_user_id)
        )
        _IN_FLIGHT[cache_key] = (request_hash, body.client_action_id)
        if body.mode == "auto":
            try:
                result = await asyncio.wait_for(asyncio.shield(work), timeout=_AUTO_ACCEPT_AFTER_SECONDS)
            except TimeoutError:
                accepted = AddZettelResponse(
                    status="accepted",
                    operation_id=body.client_action_id,
                    persistence=persistence_dto(body.persist, None),
                    quality=QualityDTO(confidence="pending"),
                    status_url=f"/api/operations/{body.client_action_id}",
                ).model_dump(mode="json")
                _operation_put(body.client_action_id, accepted)
                _OPERATION_TASKS[body.client_action_id] = work
                work.add_done_callback(
                    lambda task: _store_operation_result(
                        task,
                        operation_id=body.client_action_id,
                        cache_key=cache_key,
                        request_hash=request_hash,
                        persist_requested=body.persist,
                    )
                )
                return JSONResponse(
                    accepted,
                    status_code=202,
                    headers={
                        "Location": f"/api/operations/{body.client_action_id}",
                        "Retry-After": "3",
                    },
                )
        else:
            result = await work
        _cache_put(cache_key, request_hash, result)
        _operation_put(body.client_action_id, result)
        _IN_FLIGHT.pop(cache_key, None)
        return result
    except HTTPException as exc:
        _IN_FLIGHT.pop(cache_key, None)
        detail = exc.detail
        problem_title = "Add Zettel request rejected"
        type_slug = "request-rejected"
        if isinstance(detail, dict):
            problem_title = str(detail.get("message") or detail.get("error") or problem_title)
            if detail.get("code") == "quota_exhausted":
                type_slug = "quota-exhausted"
        return _problem(
            status_code=exc.status_code,
            title=problem_title,
            detail=detail,
            operation_id=body.client_action_id,
            type_slug=type_slug,
        )
    except UnsupportedVideoError as exc:
        _IN_FLIGHT.pop(cache_key, None)
        return _problem(
            status_code=422,
            title="Unsupported video",
            detail=f"Video type cannot be ingested: {exc.reason}",
            operation_id=body.client_action_id,
            type_slug="unsupported-video",
        )
    except ExtractionConfidenceError as exc:
        _IN_FLIGHT.pop(cache_key, None)
        return _problem(
            status_code=422,
            title="Insufficient content",
            detail="Could not extract enough content from this URL to produce a reliable summary.",
            operation_id=body.client_action_id,
            type_slug="insufficient-content",
            extra={"reason": exc.reason, "tier_results": exc.tier_results},
        )
    except (RoutingError, ValueError) as exc:
        _IN_FLIGHT.pop(cache_key, None)
        return _problem(
            status_code=422,
            title="Invalid Add Zettel request",
            detail=str(exc),
            operation_id=body.client_action_id,
            type_slug="invalid-url",
        )
    except Exception as exc:
        _IN_FLIGHT.pop(cache_key, None)
        logger.exception("Add Zettel failed for %s", body.url)
        return _problem(
            status_code=500,
            title="Add Zettel failed",
            detail=f"Failed to process URL: {exc}",
            operation_id=body.client_action_id,
        )


@router.get("/operations/{operation_id}", response_model=AddZettelResponse)
async def operation_status(operation_id: str):
    result = _operation_get(operation_id)
    if result is None:
        return _problem(
            status_code=404,
            title="Operation not found",
            detail="The operation is unknown or expired.",
            operation_id=operation_id,
            type_slug="operation-not-found",
        )
    return JSONResponse(result, status_code=202 if result.get("status") == "accepted" else 200)
