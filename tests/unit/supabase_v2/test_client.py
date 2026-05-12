from __future__ import annotations

import base64
import json

import pytest

from website.core.supabase_v2 import client


def _jwt(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"header.{body}.sig"


def test_v2_config_uses_only_v2_env(monkeypatch) -> None:
    """New contract (c418e5b): SUPABASE_V2_* absent → falls back to canonical SUPABASE_*."""
    monkeypatch.setenv("SUPABASE_URL", "https://prod.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "prod-key")
    monkeypatch.delenv("SUPABASE_V2_URL", raising=False)
    monkeypatch.delenv("SUPABASE_V2_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_V2_ANON_KEY", raising=False)

    cfg = client.get_v2_config()
    assert cfg.url == "https://prod.supabase.co"
    assert cfg.service_role_key == "prod-key"
    assert cfg.anon_key == "anon-key"
    assert client.is_v2_configured()


def test_v2_config_empty_when_neither_set(monkeypatch) -> None:
    """Neither V2_* nor canonical set → url empty, is_v2_configured False."""
    for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
              "SUPABASE_V2_URL", "SUPABASE_V2_ANON_KEY", "SUPABASE_V2_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(k, raising=False)

    cfg = client.get_v2_config()
    assert cfg.url == ""
    assert cfg.service_role_key == ""
    assert not client.is_v2_configured()


def test_parse_jwt_workspace_ids() -> None:
    token = _jwt(
        {
            "sub": "user",
            "app_metadata": {
                "workspace_ids": [
                    "00000000-0000-0000-0000-000000000001",
                    "00000000-0000-0000-0000-000000000002",
                ]
            },
        }
    )
    assert client.parse_jwt_workspace_ids(token) == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]


def test_get_v2_database_url_requires_explicit_v2_url(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_V2_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_V2_DATABASE_URL"):
        client.get_v2_database_url()

