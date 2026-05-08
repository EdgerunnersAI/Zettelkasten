"""Database-version routing helpers."""

from __future__ import annotations

import os

from website.core.supabase_v2.client import is_v2_configured


def get_db_schema_version() -> str:
    return os.environ.get("DB_SCHEMA_VERSION", "v1").strip().lower() or "v1"


def use_supabase_v2() -> bool:
    return get_db_schema_version() == "v2" and is_v2_configured()

