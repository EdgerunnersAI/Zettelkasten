"""Supabase DB v2 clients.

This module deliberately reads only SUPABASE_V2_* variables so offline
development cannot accidentally point DB v2 writes at the current production
project. Credentials can be added later without code changes.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_V2_ENV = _PROJECT_ROOT / ".env.v2"


def _bootstrap_env() -> None:
    load_dotenv(_V2_ENV, override=False)
    load_dotenv(_PROJECT_ROOT / ".env", override=False)


_bootstrap_env()


@dataclass(frozen=True)
class V2SupabaseConfig:
    url: str
    anon_key: str
    service_role_key: str
    database_url: str
    environment: str = "dev"
    listen_database_url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.anon_key and self.service_role_key)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_v2_config() -> V2SupabaseConfig:
    database_url = _env("SUPABASE_V2_DATABASE_URL")
    return V2SupabaseConfig(
        url=_env("SUPABASE_V2_URL").rstrip("/"),
        anon_key=_env("SUPABASE_V2_ANON_KEY"),
        service_role_key=_env("SUPABASE_V2_SERVICE_ROLE_KEY"),
        database_url=database_url,
        listen_database_url=_env("SUPABASE_V2_LISTEN_DATABASE_URL", database_url),
        environment=_env("SUPABASE_V2_ENVIRONMENT", "dev") or "dev",
    )


def is_v2_configured() -> bool:
    return get_v2_config().configured


def _client_options(
    *,
    schema: str = "public",
    authorization: str | None = None,
) -> SyncClientOptions:
    headers: dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
    return SyncClientOptions(
        schema=schema,
        headers=headers,
        postgrest_client_timeout=float(_env("SUPABASE_V2_HTTP_TIMEOUT", "120")),
        httpx_client=httpx.Client(
            timeout=float(_env("SUPABASE_V2_HTTP_TIMEOUT", "120")),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
        ),
    )


@lru_cache(maxsize=1)
def get_v2_client() -> Client:
    cfg = get_v2_config()
    if not cfg.url or not cfg.service_role_key:
        raise RuntimeError(
            "SUPABASE_V2_URL and SUPABASE_V2_SERVICE_ROLE_KEY must be set. "
            "Add them to .env.v2 after the v2-dev project is created."
        )
    return create_client(cfg.url, cfg.service_role_key, options=_client_options())


@lru_cache(maxsize=1)
def get_v2_anon_client() -> Client:
    cfg = get_v2_config()
    if not cfg.url or not cfg.anon_key:
        raise RuntimeError(
            "SUPABASE_V2_URL and SUPABASE_V2_ANON_KEY must be set. "
            "Add them to .env.v2 after the v2-dev project is created."
        )
    return create_client(cfg.url, cfg.anon_key, options=_client_options())


def get_v2_user_client(user_jwt: str) -> Client:
    cfg = get_v2_config()
    if not cfg.url or not cfg.anon_key:
        raise RuntimeError(
            "SUPABASE_V2_URL and SUPABASE_V2_ANON_KEY must be set for user-scoped RLS tests."
        )
    return create_client(
        cfg.url,
        cfg.anon_key,
        options=_client_options(authorization=f"Bearer {user_jwt}"),
    )


def get_v2_database_url(*, listen: bool = False) -> str:
    cfg = get_v2_config()
    dsn = cfg.listen_database_url if listen else cfg.database_url
    if not dsn:
        var = "SUPABASE_V2_LISTEN_DATABASE_URL" if listen else "SUPABASE_V2_DATABASE_URL"
        raise RuntimeError(f"{var} must be set in .env.v2.")
    return dsn


def parse_jwt_workspace_ids(jwt_token: str) -> list[str]:
    """Return app_metadata.workspace_ids from a JWT without verifying it.

    Verification belongs at the API boundary. This helper exists for tests,
    diagnostics, and shaping repository calls after auth has already happened.
    """
    parts = jwt_token.split(".")
    if len(parts) < 2:
        return []
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data: dict[str, Any] = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return []
    raw = data.get("app_metadata", {}).get("workspace_ids", [])
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item]

