"""Nexus API routes for provider connections and bulk imports."""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from website.api.auth import get_current_user, get_optional_user
from website.core.supabase_v2.client import is_v2_configured as is_supabase_configured
from website.experimental_features.nexus.service.bulk_import import (
    disconnect_provider_account,
    get_provider_account,
    list_import_runs,
    list_provider_accounts,
    provider_handler_available,
    run_all_imports,
    run_provider_import,
    upsert_provider_account,
)
from website.core.persist import get_supabase_scope
from website.experimental_features.nexus.source_ingest.common.models import (
    ImportRequest,
    NexusProvider,
    OAuthStartResponse,
    OAuthStateRecord,
    ProviderDescriptor,
    ProviderTokenSet,
    StoredProviderAccount,
)

logger = logging.getLogger("website.api.nexus")

router = APIRouter(prefix="/api/nexus", tags=["nexus"])


class ConnectRequest(BaseModel):
    redirect_path: str = "/home/nexus"
    remember_connection: bool = True


_CONNECT_HANDLER_NAMES: tuple[str, ...] = (
    "start_oauth",
    "begin_oauth",
    "start_connect",
    "build_authorization_url",
)
_CALLBACK_HANDLER_NAMES: tuple[str, ...] = (
    "handle_callback",
    "oauth_callback",
    "complete_oauth",
)


def _parse_provider(provider: str) -> NexusProvider:
    try:
        return NexusProvider(provider.lower())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unsupported provider '{provider}'") from exc


def _oauth_module(provider: NexusProvider):
    module_path = f"website.experimental_features.nexus.source_ingest.{provider.value}.oauth"
    return importlib.import_module(module_path)


def _resolve_callable(module: Any, names: tuple[str, ...]) -> Any | None:
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    return None


def _call_with_supported_kwargs(func: Any, **candidate_kwargs: Any) -> Any:
    signature = inspect.signature(func)
    accepted_kwargs = {}
    has_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    for name, value in candidate_kwargs.items():
        if value is None:
            continue
        if has_var_keyword or name in signature.parameters:
            accepted_kwargs[name] = value
    return func(**accepted_kwargs)


def _build_callback_redirect(provider: NexusProvider, status: str, message: str | None = None) -> str:
    params = {"provider": provider.value, "status": status}
    if message:
        params["message"] = message
    return f"/home/nexus?{urlencode(params)}"


def _normalize_token_set(payload: Any) -> ProviderTokenSet | None:
    if payload is None:
        return None
    if isinstance(payload, ProviderTokenSet):
        return payload
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if isinstance(payload, dict) and payload.get("access_token"):
        return ProviderTokenSet.from_token_payload(payload)
    return None


def _resolve_kg_user_id(auth_user_sub: str | None) -> UUID:
    if not auth_user_sub:
        raise HTTPException(status_code=400, detail="OAuth callback did not identify a user")

    scope = get_supabase_scope(auth_user_sub)
    if not scope:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    _repo, kg_user_id = scope
    return UUID(kg_user_id)


def _build_account_from_token_set(
    *,
    provider: NexusProvider,
    token_set: ProviderTokenSet,
    auth_user_sub: str | None,
    account_id: str | None = None,
    account_username: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StoredProviderAccount:
    kg_user_uuid = _resolve_kg_user_id(auth_user_sub)
    return StoredProviderAccount(
        user_id=kg_user_uuid,
        provider=provider,
        account_id=account_id,
        account_username=account_username,
        access_token=token_set.access_token,
        refresh_token=token_set.refresh_token,
        token_type=token_set.token_type,
        scopes=token_set.scopes,
        expires_at=token_set.expires_at,
        metadata=metadata or {},
    )


def _coerce_provider_account(
    *,
    provider: NexusProvider,
    callback_result: Any,
    current_user_sub: str | None,
) -> StoredProviderAccount | None:
    if isinstance(callback_result, StoredProviderAccount):
        return callback_result

    if hasattr(callback_result, "model_dump"):
        callback_result = callback_result.model_dump()

    if not isinstance(callback_result, dict):
        return None

    account_payload = callback_result.get("account")
    if isinstance(account_payload, StoredProviderAccount):
        return account_payload
    if hasattr(account_payload, "model_dump"):
        account_payload = account_payload.model_dump()

    if isinstance(account_payload, dict) and account_payload.get("access_token"):
        try:
            return StoredProviderAccount.model_validate(account_payload)
        except Exception:
            pass

    token_payload = (
        callback_result.get("token_set")
        or callback_result.get("tokens")
        or callback_result.get("credentials")
        or callback_result
    )
    token_set = _normalize_token_set(token_payload)
    if token_set is None:
        return None

    auth_user_sub = (
        callback_result.get("auth_user_sub")
        or callback_result.get("user_sub")
        or current_user_sub
    )
    account_info = account_payload if isinstance(account_payload, dict) else {}
    return _build_account_from_token_set(
        provider=provider,
        token_set=token_set,
        auth_user_sub=auth_user_sub,
        account_id=(
            account_info.get("account_id")
            or callback_result.get("account_id")
        ),
        account_username=(
            account_info.get("account_username")
            or account_info.get("username")
            or callback_result.get("account_username")
            or callback_result.get("username")
        ),
        metadata=callback_result.get("metadata") or {},
    )


def _coerce_exchange_callback_account(
    *,
    provider: NexusProvider,
    exchange_result: Any,
    current_user_sub: str | None,
) -> StoredProviderAccount:
    state_record: OAuthStateRecord | None = None
    token_set: ProviderTokenSet | None = None

    if isinstance(exchange_result, tuple) and len(exchange_result) == 2:
        state_candidate, token_candidate = exchange_result
        if isinstance(state_candidate, OAuthStateRecord):
            state_record = state_candidate
        elif hasattr(state_candidate, "model_dump"):
            state_record = OAuthStateRecord.model_validate(state_candidate.model_dump())
        elif isinstance(state_candidate, dict):
            state_record = OAuthStateRecord.model_validate(state_candidate)
        token_set = _normalize_token_set(token_candidate)
    elif isinstance(exchange_result, dict):
        state_candidate = exchange_result.get("state_record") or exchange_result.get("state")
        token_candidate = (
            exchange_result.get("token_set")
            or exchange_result.get("tokens")
            or exchange_result.get("credentials")
        )
        if isinstance(state_candidate, OAuthStateRecord):
            state_record = state_candidate
        elif hasattr(state_candidate, "model_dump"):
            state_record = OAuthStateRecord.model_validate(state_candidate.model_dump())
        elif isinstance(state_candidate, dict):
            state_record = OAuthStateRecord.model_validate(state_candidate)
        token_set = _normalize_token_set(token_candidate)
    else:
        token_set = _normalize_token_set(exchange_result)

    if token_set is None:
        raise HTTPException(status_code=400, detail="OAuth callback did not return provider tokens")

    auth_user_sub = (
        state_record.auth_user_sub if state_record is not None else None
    ) or current_user_sub
    metadata = {}
    if state_record is not None and isinstance(state_record.metadata, dict):
        metadata.update(state_record.metadata)
    return _build_account_from_token_set(
        provider=provider,
        token_set=token_set,
        auth_user_sub=auth_user_sub,
        metadata=metadata,
    )


@router.get("/providers")
async def providers(user: Annotated[dict, Depends(get_current_user)]) -> dict[str, list[dict[str, Any]]]:
    if not is_supabase_configured():
        raise HTTPException(status_code=503, detail="Supabase not configured")

    scope = get_supabase_scope(user["sub"])
    if not scope:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    _repo, kg_user_id = scope
    try:
        connected_accounts = list_provider_accounts(kg_user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    descriptors = []
    for provider in NexusProvider:
        account = connected_accounts.get(provider)
        descriptors.append(
            ProviderDescriptor(
                provider=provider,
                label=provider.value.replace("_", " ").title() if provider.value != "twitter" else "Twitter/X",
                connected=account is not None,
                available=provider_handler_available(provider, "oauth") or provider_handler_available(provider, "ingest"),
                can_refresh=bool(account and account.refresh_token),
                scopes=account.scopes if account else [],
                account_username=account.account_username if account else None,
                last_imported_at=account.last_imported_at if account else None,
            ).model_dump()
        )

    return {"providers": descriptors}


@router.post("/connect/{provider}")
async def connect_provider(
    provider: str,
    body: ConnectRequest = Body(default_factory=ConnectRequest),
    user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    parsed_provider = _parse_provider(provider)

    try:
        module = _oauth_module(parsed_provider)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"OAuth is not available for provider '{parsed_provider.value}'",
        ) from exc

    handler = _resolve_callable(module, _CONNECT_HANDLER_NAMES)
    if handler is None:
        raise HTTPException(
            status_code=503,
            detail=f"OAuth is not available for provider '{parsed_provider.value}'",
        )

    try:
        result = _call_with_supported_kwargs(
            handler,
            auth_user_sub=user["sub"],
            redirect_path=body.redirect_path,
            metadata={
                "remember_connection": bool(body.remember_connection),
                "connection_mode": "persistent" if body.remember_connection else "session",
            },
            provider=parsed_provider,
        )
        if inspect.isawaitable(result):
            result = await result
    except RuntimeError as exc:
        logger.warning("Invalid %s OAuth configuration: %s", parsed_provider.value, exc)
        raise HTTPException(
            status_code=503,
            detail=f"{parsed_provider.value} OAuth config error: {exc}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to start %s OAuth: %s", parsed_provider.value, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start {parsed_provider.value} OAuth") from exc

    if isinstance(result, OAuthStartResponse):
        payload = result.model_dump()
        payload.setdefault("redirect_url", payload.get("authorization_url"))
        payload["remember_connection"] = bool(body.remember_connection)
        return payload
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
        if isinstance(payload, dict):
            payload.setdefault("redirect_url", payload.get("authorization_url"))
            payload["remember_connection"] = bool(body.remember_connection)
        return payload
    if isinstance(result, dict):
        result.setdefault("redirect_url", result.get("authorization_url"))
        result["remember_connection"] = bool(body.remember_connection)
        return result
    raise HTTPException(status_code=500, detail="OAuth handler returned an unsupported response")


@router.get("/callback/{provider}")
async def callback_provider(
    provider: str,
    request: Request,
    user: Annotated[dict | None, Depends(get_optional_user)] = None,
):
    parsed_provider = _parse_provider(provider)

    try:
        module = _oauth_module(parsed_provider)
    except Exception:
        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "error", "OAuth handler unavailable"),
            status_code=302,
        )

    callback_handler = _resolve_callable(module, _CALLBACK_HANDLER_NAMES)
    exchange_handler = _resolve_callable(module, ("exchange_code_for_tokens",))
    if callback_handler is None and exchange_handler is None:
        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "error", "OAuth handler unavailable"),
            status_code=302,
        )

    query_params = dict(request.query_params)
    if query_params.get("error"):
        message = query_params.get("error_description") or query_params.get("error") or "OAuth callback failed"
        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "error", message),
            status_code=302,
        )

    try:
        account: StoredProviderAccount | None = None

        if callback_handler is not None:
            result = _call_with_supported_kwargs(
                callback_handler,
                request=request,
                query_params=query_params,
                provider=parsed_provider,
                current_user=user,
                auth_user_sub=user["sub"] if user else None,
            )
            if inspect.isawaitable(result):
                result = await result
            account = _coerce_provider_account(
                provider=parsed_provider,
                callback_result=result,
                current_user_sub=user["sub"] if user else None,
            )
        else:
            code = query_params.get("code")
            state_token = query_params.get("state") or query_params.get("state_token")
            if not code or not state_token:
                raise HTTPException(status_code=400, detail="OAuth callback is missing code or state")

            exchange_result = _call_with_supported_kwargs(
                exchange_handler,
                code=code,
                state_token=state_token,
                request=request,
                query_params=query_params,
                provider=parsed_provider,
                auth_user_sub=user["sub"] if user else None,
            )
            if inspect.isawaitable(exchange_result):
                exchange_result = await exchange_result
            account = _coerce_exchange_callback_account(
                provider=parsed_provider,
                exchange_result=exchange_result,
                current_user_sub=user["sub"] if user else None,
            )

        if account is None:
            raise HTTPException(status_code=400, detail="OAuth callback did not return a provider account")
        upsert_provider_account(account)

        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "connected"),
            status_code=302,
        )
    except HTTPException as exc:
        message = str(exc.detail) if exc.detail else "OAuth callback failed"
        logger.warning("OAuth callback failed for %s: %s", parsed_provider.value, message)
        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "error", message),
            status_code=302,
        )
    except Exception as exc:
        logger.warning("OAuth callback failed for %s: %s", parsed_provider.value, exc)
        return RedirectResponse(
            url=_build_callback_redirect(parsed_provider, "error", str(exc)),
            status_code=302,
        )


@router.post("/disconnect/{provider}")
async def disconnect_provider(
    provider: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    parsed_provider = _parse_provider(provider)

    scope = get_supabase_scope(user["sub"])
    if not scope:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    _repo, kg_user_id = scope
    try:
        disconnected = disconnect_provider_account(kg_user_id, parsed_provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "provider": parsed_provider.value,
        "disconnected": disconnected,
    }


@router.post("/import/{provider}")
async def import_provider(
    provider: str,
    body: ImportRequest = Body(default_factory=ImportRequest),
    user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    parsed_provider = _parse_provider(provider)

    try:
        result = await run_provider_import(
            auth_user_sub=user["sub"],
            provider=parsed_provider,
            request=body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "provider": result.provider.value,
        "run": result.run.model_dump() if result.run else None,
        "total_artifacts": result.total_artifacts,
        "imported_count": result.imported_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "results": result.results,
        "remember_connection": body.remember_connection,
        "credentials_forgotten": result.credentials_forgotten,
    }


@router.post("/import/all")
async def import_all(
    body: ImportRequest = Body(default_factory=ImportRequest),
    user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, Any]:
    try:
        results = await run_all_imports(
            auth_user_sub=user["sub"],
            request=body,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "runs": [
            {
                "provider": result.provider.value,
                "run": result.run.model_dump() if result.run else None,
                "total_artifacts": result.total_artifacts,
                "imported_count": result.imported_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "results": result.results,
                "remember_connection": body.remember_connection,
                "credentials_forgotten": result.credentials_forgotten,
            }
            for result in results
        ]
    }


@router.get("/runs")
async def runs(
    limit: int = 20,
    user: Annotated[dict, Depends(get_current_user)] = None,
) -> dict[str, list[dict[str, Any]]]:
    scope = get_supabase_scope(user["sub"])
    if not scope:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    _repo, kg_user_id = scope
    items = list_import_runs(kg_user_id, limit=limit)
    return {"runs": [item.model_dump() for item in items]}
