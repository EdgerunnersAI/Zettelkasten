"""UP-21: forbid secret-shaped tokens in ``purchase_launcher.js``.

The launcher runs in the browser. Any string matching a Razorpay key-secret
shape (``RAZORPAY_KEY_SECRET``, ``KEY_SECRET``, ``WEBHOOK_SECRET``) or a
Stripe-shaped live/test key (``sk_live_*``, ``sk_test_*`` with ≥20 chars) is
treated as a credential leak. The launcher is allowed to call
``/api/payments/orders`` to obtain a checkout payload that contains the
**public** ``key_id`` — but it must never embed a secret.

CI gate: ``.github/workflows/launcher_secret_scan.yml`` runs the same regex
against the file on every push/PR. This pytest unit is the local mirror so
the violation surfaces before CI.
"""
from __future__ import annotations

import pathlib
import re

import pytest


LAUNCHER = pathlib.Path("website/features/user_pricing/js/purchase_launcher.js")

# Patterns kept in sync with .github/workflows/launcher_secret_scan.yml.
# Each pattern must be a substring or shape that should NEVER appear in a
# browser-shipped file.
FORBIDDEN = re.compile(
    r"(KEY_SECRET|RAZORPAY_KEY_SECRET|WEBHOOK_SECRET|sk_live|sk_test_[A-Za-z0-9]{20,})"
)


def test_launcher_file_exists():
    assert LAUNCHER.exists(), f"{LAUNCHER} missing — UP-21 cannot scan a phantom"


def test_launcher_has_no_secret_shaped_tokens():
    body = LAUNCHER.read_text(encoding="utf-8")
    matches = FORBIDDEN.findall(body)
    assert matches == [], (
        f"Forbidden secret-shaped tokens found in {LAUNCHER}: {matches!r}. "
        "Browser-shipped JS must never contain Razorpay key secrets, webhook "
        "secrets, or Stripe-shaped keys. Only the PUBLIC key_id is allowed "
        "and it arrives via the /api/payments/orders response, not the source."
    )


def test_launcher_does_not_reference_env_secret_names():
    """Defence-in-depth: also forbid literal env var names that operators
    might paste in by mistake (e.g. when copying from `.env.example`)."""
    body = LAUNCHER.read_text(encoding="utf-8")
    leaks = []
    for needle in ("RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        if needle in body:
            leaks.append(needle)
    assert leaks == [], f"Env-var-secret name leaked in launcher: {leaks}"
