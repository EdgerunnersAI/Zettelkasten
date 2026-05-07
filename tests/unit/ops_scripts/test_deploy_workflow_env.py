"""iter-12 Task 23: deploy workflow env-drift sanity tests."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-droplet.yml"
ENV_EXAMPLE = REPO_ROOT / "ops" / ".env.example"
COMPOSE_BLUE = REPO_ROOT / "ops" / "docker-compose.blue.yml"
COMPOSE_GREEN = REPO_ROOT / "ops" / "docker-compose.green.yml"


def _static_body_knobs() -> set[str]:
    text = WORKFLOW.read_text()
    return set(re.findall(r'"([A-Z_][A-Z0-9_]+)=', text))


def _example_knobs() -> set[str]:
    out = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z_][A-Z0-9_]+)=", line)
        if m and m.group(1).startswith(("RAG_", "GUNICORN_", "GEMINI_COOLDOWN_")):
            out.add(m.group(1))
    return out


def test_iter12_class_p_knobs_in_static_body():
    knobs = _static_body_knobs()
    for k in [
        "RAG_EXECUTOR_MAX_WORKERS", "RAG_RPC_GLOBAL_SEMAPHORE",
        "RAG_HTTPX_MAX_CONNECTIONS", "RAG_HTTPX_MAX_KEEPALIVE",
        "RAG_ENTITY_GATHER_SEMAPHORE",
    ]:
        assert k in knobs, f"{k} missing from STATIC_BODY"


def test_iter11_carryover_anchor_flags_explicit():
    knobs = _static_body_knobs()
    assert "RAG_ANCHOR_BOOST_ENABLED" in knobs
    assert "RAG_ANCHOR_SEED_INJECTION_ENABLED" in knobs


def test_no_drift_between_example_and_static_body():
    drift = _example_knobs() - _static_body_knobs()
    assert not drift, f"Knobs in .env.example missing from STATIC_BODY: {sorted(drift)}"


def test_compose_blue_has_env_local_overlay():
    text = COMPOSE_BLUE.read_text()
    assert ".env.local" in text, "blue compose missing .env.local overlay"


def test_compose_green_has_env_local_overlay():
    text = COMPOSE_GREEN.read_text()
    assert ".env.local" in text, "green compose missing .env.local overlay"


def test_env_example_documents_operator_override_pattern():
    text = ENV_EXAMPLE.read_text()
    assert ".env.local" in text
    assert "OPERATOR OVERRIDE PATTERN" in text


def test_short_thematic_threshold_removed():
    text = ENV_EXAMPLE.read_text()
    assert "RAG_SHORT_THEMATIC_THRESHOLD" not in text, \
        "RAG_SHORT_THEMATIC_THRESHOLD must be removed (Class D-out)"
