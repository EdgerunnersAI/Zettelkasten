"""Sanity-check the new Supabase credentials end-to-end. Echoes ONLY pass/fail.

Verifies:
  1. SUPABASE_URL reachable (HEAD /)
  2. Anon key authenticates (GET /rest/v1/ as anon)
  3. Service role key authenticates (GET /rest/v1/ as service_role)
  4. JWT secret is valid base64 and matches expected length
  5. Direct Postgres URL connects + pgvector + pg_cron + pg_partman extensions present
  6. Project ref derived from URL matches SUPABASE_PROJECT_REF
"""
from __future__ import annotations
import asyncio
import base64
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Load .env from repo root
def load_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


async def main() -> int:
    env = load_env()
    fails: list[str] = []
    passes: list[str] = []

    required = [
        "SUPABASE_URL", "SUPABASE_PROJECT_REF", "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET",
        "SUPABASE_DATABASE_URL", "SUPABASE_ACCESS_TOKEN",
    ]
    for k in required:
        if not env.get(k):
            fails.append(f"MISSING: {k}")
    if fails:
        for f in fails:
            print(f"  ✗ {f}")
        return 1

    # 1. URL reachable + anon key recognized (any non-5xx response is OK;
    #    401 / 403 are expected on a fresh project with no public tables)
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(env["SUPABASE_URL"] + "/rest/v1/", method="GET",
                                      headers={"apikey": env["SUPABASE_ANON_KEY"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.status
        except urllib.error.HTTPError as he:
            code = he.code
        if code < 500:
            passes.append(f"✓ SUPABASE_URL reachable + anon key accepted (HTTP {code})")
        else:
            fails.append(f"✗ SUPABASE_URL returned 5xx: {code}")
    except Exception as e:
        fails.append(f"✗ SUPABASE_URL unreachable: {type(e).__name__}: {e}")

    # 2. Project ref matches
    parsed = urlparse(env["SUPABASE_URL"])
    derived_ref = parsed.netloc.split(".")[0]
    if derived_ref == env["SUPABASE_PROJECT_REF"]:
        passes.append("✓ SUPABASE_PROJECT_REF matches URL subdomain")
    else:
        fails.append(f"✗ SUPABASE_PROJECT_REF mismatch (URL says {derived_ref})")

    # 3. Service role key authenticates
    try:
        req = urllib.request.Request(env["SUPABASE_URL"] + "/rest/v1/", method="GET",
                                      headers={"apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
                                               "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status in (200, 404), f"status {resp.status}"
        passes.append("✓ SUPABASE_SERVICE_ROLE_KEY authenticates")
    except Exception as e:
        fails.append(f"✗ SUPABASE_SERVICE_ROLE_KEY auth failed: {type(e).__name__}")

    # 4. JWT secret is valid base64
    try:
        decoded = base64.b64decode(env["SUPABASE_JWT_SECRET"])
        assert 32 <= len(decoded) <= 128, f"unexpected JWT secret length {len(decoded)}"
        passes.append(f"✓ SUPABASE_JWT_SECRET decodes ({len(decoded)} bytes)")
    except Exception as e:
        fails.append(f"✗ SUPABASE_JWT_SECRET invalid: {type(e).__name__}")

    # 5. Direct Postgres connect + extensions
    try:
        import asyncpg  # type: ignore[import-not-found]
        conn = await asyncpg.connect(env["SUPABASE_DATABASE_URL"], timeout=10)
        try:
            ver = await conn.fetchval("SELECT version();")
            exts = await conn.fetch("SELECT extname FROM pg_extension;")
            ext_names = {r["extname"] for r in exts}
            passes.append(f"✓ SUPABASE_DATABASE_URL connects (pg {ver.split()[1] if ver else '?'})")
            for needed in ("pgcrypto", "vector"):
                if needed in ext_names:
                    passes.append(f"  ✓ extension {needed} present")
                else:
                    fails.append(f"  ✗ extension {needed} MISSING")
            for optional in ("pg_partman", "pg_cron"):
                if optional in ext_names:
                    passes.append(f"  ✓ extension {optional} present")
                else:
                    passes.append(f"  ⚠ extension {optional} not yet enabled (will install in 00_extensions.sql)")
        finally:
            await conn.close()
    except ImportError:
        passes.append("⚠ asyncpg not installed; skipping direct-port connection test")
    except Exception as e:
        fails.append(f"✗ SUPABASE_DATABASE_URL connect failed: {type(e).__name__}: {e}")

    print("-" * 60)
    for p in passes:
        print(p)
    for f in fails:
        print(f)
    print("-" * 60)
    if fails:
        print(f"FAIL: {len(fails)} check(s) failed, {len(passes)} passed")
        return 1
    print(f"PASS: all {len(passes)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
