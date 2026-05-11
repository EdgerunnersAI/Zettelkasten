"""UP-25: SHA-256 file-hash gate on ``billing.pricing_consume_entitlement`` body.

This pins the *file* ``supabase/website/_v2/12_revert_unauthorized_pricing.sql``
which is the canonical anti-tamper anchor for the RPC body
(Phase-8 decision: that file is "Restore the original
pricing_consume_entitlement body (verbatim copy of 06_billing_schema.sql lines
217-263)" — see Phase 0.5 discovery 2026-05-11).

Any drift in the SQL file fails CI. Intentional edits require:
  1. Explicit operator approval (CLAUDE.md "pricing module authority" rule).
  2. Re-running ``python -c "import hashlib,pathlib; ..."`` to compute the
     new digest.
  3. Updating ``GOLDEN_SHA256`` below in the same commit as the SQL change.

Layer-1 ``file-hash`` gate only — Layer-2 ``pg_proc.prosrc`` live-DB smoke
check is deferred per the 2026-05-11 plan amendment.

NOTE: SHA-256 (not md5). The plan body referenced md5 historically; the
amendment locked SHA-256 because md5 collision attacks are public for years
and operator policy is to use SHA-256 for new anti-tamper gates.
"""
from __future__ import annotations

import hashlib
import pathlib


# Computed 2026-05-11 against the committed file (size 3240 bytes).
RPC_FILE = pathlib.Path("supabase/website/_v2/12_revert_unauthorized_pricing.sql")
GOLDEN_SHA256 = "9d9b8b071acdd753397099f663110848f630f056d6b8c8835b2d2dbb297461a6"


def test_rpc_file_exists():
    assert RPC_FILE.exists(), (
        f"Expected {RPC_FILE} to exist — the canonical pricing_consume_entitlement "
        f"anchor file was moved or deleted. Restore it or update the gate."
    )


def test_consume_entitlement_body_unchanged():
    body = RPC_FILE.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    assert digest == GOLDEN_SHA256, (
        f"\n  RPC body drifted.\n"
        f"  File:     {RPC_FILE}\n"
        f"  Expected: {GOLDEN_SHA256}\n"
        f"  Actual:   {digest}\n"
        f"  Size:     {len(body)} bytes\n\n"
        f"If this drift is intentional, update GOLDEN_SHA256 in the same commit "
        f"AND obtain explicit operator approval per CLAUDE.md 'pricing module "
        f"authority' rule (NEVER alter the consume_entitlement body without "
        f"operator sign-off — the RPC body is golden-protected and any change "
        f"can corrupt billing invariants in production)."
    )
