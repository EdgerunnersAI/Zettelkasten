"""Shared helpers for DB v2 backfill scripts."""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BackfillConfig:
    dsn: str
    dry_run: bool


def parse_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print planned work.")
    parser.add_argument("--continue", dest="continue_run", action="store_true", help="Proceed after the previous gate.")
    return parser.parse_args()


def load_config(*, dry_run: bool) -> BackfillConfig:
    dsn = os.environ.get("SUPABASE_V2_DATABASE_URL", "").strip()
    if not dsn and not dry_run:
        raise SystemExit("SUPABASE_V2_DATABASE_URL is required for DB v2 backfill execution")
    return BackfillConfig(dsn=dsn, dry_run=dry_run)


def require_continue(args: argparse.Namespace, step: str) -> None:
    if args.dry_run:
        return
    if not args.continue_run:
        raise SystemExit(f"{step} requires --continue after reviewing the previous verification gate")


async def _connect(dsn: str):
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise SystemExit("asyncpg is required for DB v2 backfill execution") from exc
    return await asyncpg.connect(dsn)


async def run_statements(config: BackfillConfig, sql: str) -> None:
    if config.dry_run:
        return
    conn = await _connect(config.dsn)
    try:
        async with conn.transaction():
            await conn.execute(sql)
    finally:
        await conn.close()


async def fetch_value(config: BackfillConfig, sql: str) -> Any:
    if config.dry_run:
        return None
    conn = await _connect(config.dsn)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


async def assert_zero(config: BackfillConfig, sql: str, message: str) -> None:
    count = await fetch_value(config, f"SELECT COUNT(*) FROM ({sql}) AS violations")
    if count:
        raise SystemExit(f"{message}: {count} violation(s)")


def run_async(coro) -> int:
    asyncio.run(coro)
    return 0
