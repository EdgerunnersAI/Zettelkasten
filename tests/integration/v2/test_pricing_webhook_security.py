"""UP-01 / UP-02: webhook signature validation + constant-time compare regression locks.

These tests pin two production invariants for the Razorpay webhook:
  UP-01: HMAC-SHA256(body, RAZORPAY_WEBHOOK_SECRET) is verified before any
         JSON parse or handler dispatch. Tampered bodies are rejected.
  UP-02: The compare uses ``hmac.compare_digest`` (constant-time) — never
         ``==``. CWE-208 (Observable Timing Discrepancy) regression lock.

Discovery 2026-05-11 confirmed both invariants hold today
(razorpay_client.py:58-88). These tests stay as green regression locks; if
either flips to ``==`` in the future the suite blocks the merge.

No live DB calls — secret is supplied directly to ``verify_webhook_signature``,
so the suite runs in unit-time without a ``RAZORPAY_WEBHOOK_SECRET`` env var.
"""
from __future__ import annotations

import hashlib
import hmac
import inspect

from website.features.user_pricing import razorpay_client
from website.features.user_pricing.razorpay_client import verify_webhook_signature


TEST_SECRET = "wave-a-test-webhook-secret"


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ─────────────────────────── UP-01: signature gates ───────────────────────────


def test_valid_signature_accepted():
    body = b'{"event":"payment.captured","id":"evt_1"}'
    sig = _sign(body, TEST_SECRET)
    assert verify_webhook_signature(body=body, signature=sig, secret=TEST_SECRET) is True


def test_signature_mismatch_rejected():
    body = b'{"event":"payment.captured","id":"evt_1"}'
    bad = _sign(body, "wrong-secret")
    assert verify_webhook_signature(body=body, signature=bad, secret=TEST_SECRET) is False


def test_body_mutated_after_sign_rejected():
    """Body altered between signing and verify (replay-with-mutation) must fail."""
    body = b'{"event":"payment.captured","id":"evt_1"}'
    sig = _sign(body, TEST_SECRET)
    mutated = body.replace(b"captured", b"failed")
    assert verify_webhook_signature(body=mutated, signature=sig, secret=TEST_SECRET) is False


def test_missing_signature_rejected():
    body = b"{}"
    assert verify_webhook_signature(body=body, signature="", secret=TEST_SECRET) is False


def test_empty_secret_rejects_even_valid_sig():
    """Defence-in-depth: an empty resolved secret must short-circuit to False."""
    body = b'{"event":"payment.captured"}'
    # If secret is "" the helper hashes against b"" — never accept.
    assert verify_webhook_signature(body=body, signature="anything", secret="") is False


# ─────────────────────────── UP-02: constant-time compare ──────────────────────


_TRIPLE_DQ = chr(34) * 3  # avoid embedding the token in this file's source
_TRIPLE_SQ = chr(39) * 3


def _code_lines(src: str) -> list[str]:
    # Strip docstrings (single-line and multi-line) and comments so the `==`
    # guard only inspects executable code. Handles both single-line
    # triple-quoted strings on one line AND multi-line spans.
    import re as _re
    src = _re.sub(r'(?s)' + _TRIPLE_DQ + r'.*?' + _TRIPLE_DQ, '', src)
    src = _re.sub(r"(?s)" + _TRIPLE_SQ + r".*?" + _TRIPLE_SQ, '', src)
    out: list[str] = []
    for raw in src.splitlines():
        line = raw
        if "#" in line:
            line = line.split("#", 1)[0]
        out.append(line)
    return out


def _no_eq_signature(src: str) -> bool:
    """Detect `==` against `signature` while ignoring `!=` and `===` edges.

    The regex uses negative-look behind/ahead so that `!= signature` and
    `signature !=` do NOT trigger (those are correct rejection paths), and
    `=== signature` / `signature ===` (triple-equals — JS-style typos that
    Python wouldn't compile but defensive anyway) also do not trigger.
    """
    import re as _re
    pattern = _re.compile(r"(?<![!=])==\s*signature\b|\bsignature\s*==(?!=)")
    for line in _code_lines(src):
        if pattern.search(line):
            return False
    return True


def test_verify_webhook_signature_uses_compare_digest():
    """CWE-208 regression: webhook verify MUST use hmac.compare_digest, not ==."""
    src = inspect.getsource(razorpay_client.verify_webhook_signature)
    assert "compare_digest" in src, (
        "verify_webhook_signature must use hmac.compare_digest "
        "(constant-time). Found source:\n" + src
    )
    assert _no_eq_signature(src), (
        "verify_webhook_signature must not use `==` against the signature."
    )


def test_verify_payment_signature_uses_compare_digest():
    src = inspect.getsource(razorpay_client.verify_payment_signature)
    assert "compare_digest" in src, (
        "verify_payment_signature must use hmac.compare_digest"
    )
    assert _no_eq_signature(src), (
        "verify_payment_signature must not use `==` against the signature."
    )


def test_verify_subscription_signature_uses_compare_digest():
    src = inspect.getsource(razorpay_client.verify_subscription_signature)
    assert "compare_digest" in src, (
        "verify_subscription_signature must use hmac.compare_digest"
    )
    assert _no_eq_signature(src), (
        "verify_subscription_signature must not use `==` against the signature."
    )
