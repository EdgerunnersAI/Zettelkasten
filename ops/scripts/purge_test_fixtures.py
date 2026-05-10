"""Phase 7.3c: nightly cleanup of leftover ``e2e-*@test.com`` test fixtures.

The pytest_sessionfinish hook in ``tests/integration/v2/conftest.py`` is the
first line of defence; this script is the safety net for crashes that skip
teardown entirely (KeyboardInterrupt, OOM, runner SIGKILL) and for fixture
leaks from one-off operator runs.

Idempotent: matches the canonical mint pattern ``e2e-{8 hex}@test.com``
(allow 6-12 hex for forward-compat). Only deletes users older than
``--age-hours`` (default 24h) so an in-flight test run is never disrupted.

Requires the service-role key (``SUPABASE_V2_SERVICE_ROLE_KEY`` or
``SUPABASE_SERVICE_ROLE_KEY``) — admin.list_users / admin.delete_user are
service-role-only RPCs.

Usage::

    python ops/scripts/purge_test_fixtures.py --age-hours 24
    python ops/scripts/purge_test_fixtures.py --age-hours 0 --dry-run

Exit codes: 0 success, 1 runtime error, 2 config error.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv(ROOT / "supabase" / ".env")
    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)-22s %(message)s",
)
log = logging.getLogger("purge_test_fixtures")

# Mint pattern: ``e2e-{uuid.uuid4().hex[:8]}@test.com``. Allow 6-12 hex for
# forward-compat if the prefix length is ever tuned.
E2E_EMAIL_PATTERN = re.compile(r"^e2e-[0-9a-f]{6,12}@test\.com$")


def _parse_supabase_timestamp(value: str) -> datetime | None:
    """Parse a Supabase ISO-8601 timestamp into a tz-aware UTC datetime."""
    if not value:
        return None
    try:
        # Supabase emits e.g. "2026-05-10T06:42:01.123456+00:00".
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _user_created_at(user) -> datetime | None:
    raw = getattr(user, "created_at", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return _parse_supabase_timestamp(str(raw))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--age-hours",
        type=float,
        default=24.0,
        help="Only delete users older than this many hours (default: 24).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matches without deleting.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="auth.admin.list_users page size (default: 200).",
    )
    args = parser.parse_args(argv)

    try:
        from website.core.supabase_v2.client import get_v2_client
    except Exception as exc:  # noqa: BLE001
        log.error("could not import v2 client: %s: %s", type(exc).__name__, exc)
        return 2

    try:
        client = get_v2_client()
    except Exception as exc:  # noqa: BLE001
        log.error("get_v2_client failed: %s: %s", type(exc).__name__, exc)
        return 2

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=args.age_hours)
    log.info("cutoff = %s (age >= %.1fh)", cutoff.isoformat(), args.age_hours)

    candidates = []
    page = 1
    scanned = 0
    while True:
        try:
            resp = client.auth.admin.list_users(page=page, per_page=args.page_size)
        except Exception as exc:  # noqa: BLE001
            log.error("list_users page=%d failed: %s: %s", page, type(exc).__name__, exc)
            return 1
        users = resp if isinstance(resp, list) else getattr(resp, "users", [])
        if not users:
            break
        scanned += len(users)
        for u in users:
            email = getattr(u, "email", None) or ""
            if not email or not E2E_EMAIL_PATTERN.match(email):
                continue
            created = _user_created_at(u)
            if created is None:
                log.warning("skip %s: no parseable created_at", email)
                continue
            if created > cutoff:
                continue
            candidates.append((u, email, created))
        if len(users) < args.page_size:
            break
        page += 1
        if page > 50:
            log.warning("hit page-cap of 50 (10k users); stopping scan")
            break

    log.info(
        "scanned=%d matched=%d (older than cutoff)",
        scanned,
        len(candidates),
    )

    if not candidates:
        return 0

    if args.dry_run:
        for _u, email, created in candidates[:50]:
            log.info("would delete %s (created=%s)", email, created.isoformat())
        if len(candidates) > 50:
            log.info("... and %d more (dry-run)", len(candidates) - 50)
        return 0

    deleted, failed = 0, 0
    for u, email, _created in candidates:
        try:
            client.auth.admin.delete_user(u.id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("delete %s failed: %s: %s", email, type(exc).__name__, exc)

    log.info("done: deleted=%d failed=%d", deleted, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
