"""Supabase client singleton for knowledge graph operations."""

from __future__ import annotations

import logging
import atexit
import os
from functools import lru_cache
from pathlib import Path

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SUPABASE_ENV = _PROJECT_ROOT / "supabase" / ".env"


def _load_key_value_secret_file(path: Path) -> int:
    """Load KEY=VALUE lines from a secret file into os.environ.

    This parser intentionally ignores plain lines without ``=`` so it can
    safely read files such as ``api_env`` that may contain non-env payloads
    (for example one Gemini key per line).
    """
    if not path.exists():
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key or key in os.environ:
            continue

        value = value.strip().strip("'").strip('"')
        os.environ[key] = value
        loaded += 1
    return loaded


def _bootstrap_env() -> None:
    """Load env vars from local files and from the droplet secret-file mounts.

    The `/etc/secrets/*` paths below are mounted into the production
    DigitalOcean droplet container. The path convention was originally
    adopted from Render's Secret Files feature and carried over to the
    droplet for continuity; Render itself is no longer used.

    Precedence remains: explicit environment variables > file values.
    """
    load_dotenv(_SUPABASE_ENV, override=False)
    load_dotenv(_PROJECT_ROOT / ".env", override=False)

    secret_candidates = (
        Path("/etc/secrets/nexus_env"),
        Path("/etc/secrets/api_env"),
        _PROJECT_ROOT / "supabase" / "website" / "nexus" / "nexus_env",
        _PROJECT_ROOT / "website" / "experimental_features" / "nexus" / "nexus_env.txt",
    )
    for secret_path in secret_candidates:
        loaded = _load_key_value_secret_file(secret_path)
        if loaded:
            logger.info("Loaded %s env vars from %s", loaded, secret_path)


_bootstrap_env()


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a lazily-initialized Supabase client.

    Credentials are loaded from ``supabase/.env``.  Uses the service-role
    key so the Python backend bypasses RLS and can manage data for any user.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set. "
            "Add them to supabase/.env — see that file for instructions."
        )

    timeout = _read_http_timeout()
    verify = _read_http_verify()
    proxy = (os.environ.get("SUPABASE_HTTP_PROXY") or "").strip() or None

    httpx_kwargs: dict[str, object] = {
        "timeout": timeout,
        "verify": verify,
        # iter-03 §B (2026-04-29): bound the connection pool. iter-12 Class P:
        # env-driven limits so the pool scales with RAG_EXECUTOR_MAX_WORKERS
        # without a code deploy. Never raise max_connections above 16 — RAM
        # and httpx pool ceiling documented in iter-12 RESEARCH.md.
        "limits": httpx.Limits(
            max_keepalive_connections=int(os.environ.get("RAG_HTTPX_MAX_KEEPALIVE", "8")),
            max_connections=int(os.environ.get("RAG_HTTPX_MAX_CONNECTIONS", "16")),
            keepalive_expiry=30.0,
        ),
    }
    if proxy:
        # httpx 0.27+ accepts a single proxy URL via ``proxy=``.
        httpx_kwargs["proxy"] = proxy

    shared_http_client = httpx.Client(**httpx_kwargs)
    atexit.register(shared_http_client.close)
    options = SyncClientOptions(httpx_client=shared_http_client)

    logger.info("Initializing Supabase client for %s", url)
    return create_client(url, key, options=options)


def get_supabase_env() -> dict[str, str]:
    """Return all three Supabase env vars as a dict (for MCP/tooling)."""
    return {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
        "SUPABASE_ANON_KEY": os.environ.get("SUPABASE_ANON_KEY", ""),
        "SUPABASE_SERVICE_ROLE_KEY": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    }


def is_supabase_configured() -> bool:
    """Check whether Supabase env vars are present (without initializing)."""
    return bool(
        os.environ.get("SUPABASE_URL")
        and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )


def _read_http_timeout() -> float:
    raw = (os.environ.get("SUPABASE_HTTP_TIMEOUT") or "").strip()
    if not raw:
        return 20.0
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return max(1.0, value)


def _read_http_verify() -> bool:
    raw = (os.environ.get("SUPABASE_HTTP_VERIFY") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True
