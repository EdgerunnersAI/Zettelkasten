# Purge Telegram, Obsidian, and Syncthing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every Telegram-bot, Obsidian-writer, and Syncthing reference from the codebase. Final state: a website-only system with Supabase backend, deployed via DigitalOcean blue/green. No code path, env var, setting, doc section, or test asserts a Telegram/Obsidian/Syncthing concept.

**Architecture:** Seven sequential phases. Each phase is independently committable and leaves the test suite green. The phases are ordered to minimise temporary breakage: settings/env first (drives most other changes), then writers, then entrypoints, then ops, then memory-guard, then docs, then full-suite verification + smoke deploy.

**Tech Stack:** Python 3.12 + FastAPI + Pydantic Settings + pytest (no new deps).

**HARD GATE — production discipline:** Per `CLAUDE.md` Critical Infra Decision Guardrails, do **not** silently change `GUNICORN_WORKERS`, `--preload`, `GUNICORN_TIMEOUT`, the rerank semaphore, the SSE heartbeat, blue/green Caddy `read_timeout`, `transport http` block, the schema-drift gate, or the `kg_users` allowlist gate. This plan touches none of those — if a step appears to require touching one, STOP and surface to operator.

---

## Pre-flight: protected env vars (do not touch)

| Variable | Reason kept |
|---|---|
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay payment webhooks — different system from the Telegram webhook |
| `SUPABASE_*` | Supabase backend |
| `GEMINI_API_KEY*` | summarisation |
| `REDDIT_CLIENT_*` | Reddit OAuth ingestion (still in use) |
| `RAG_*`, `GUNICORN_*`, `PROC_STATS_*` | infra knobs codified in CLAUDE.md guardrails |
| `SLACK_WEBHOOK_URL`, `SLACK_WEBHOOK_APP_ERRORS` | observability |

Variables this plan **deletes**:

| Variable | From |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Pydantic settings, `.env.example`, `config.yaml` |
| `ALLOWED_CHAT_ID` | Pydantic settings, `.env.example`, `config.yaml` |
| `WEBHOOK_MODE` / `WEBHOOK_URL` / `WEBHOOK_PORT` / `WEBHOOK_SECRET` | Pydantic settings, `.env.example`, `config.yaml` |
| `KG_DIRECTORY` | Pydantic settings, `.env.example`, `config.yaml` |
| `GITHUB_TOKEN` / `GITHUB_REPO` / `GITHUB_BRANCH` | Pydantic settings, `.env.example`, `config.yaml` |
| `REDDIT_OPTIONAL` | settings (gate logic redefined in Task 2.2) |

---

# Phase 1 — Settings, env, and config

## Task 1.1: Strip telegram + webhook + obsidian + github fields from `Settings`

**Files:**
- Modify: `website/core/settings.py`

The current `Settings` class has telegram/webhook/github/kg_directory fields. Replace with a website-only field set. The dev-mode bind port is renamed `webhook_port` → `server_port` (semantic). `validate_reddit_credentials` is rewritten in Task 1.2 below.

- [ ] **Step 1: Replace `website/core/settings.py` body**

```python
"""Website application settings.

Pydantic BaseSettings layering (env > .env > ops/config.yaml) for the FastAPI
app.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Tuple, Type

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

logger = logging.getLogger(__name__)

# Module-level latch so the Reddit OAuth warning fires exactly once per process.
_reddit_warning_emitted = False

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_CONFIG_YAML = _PROJECT_ROOT / "ops" / "config.yaml"
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Website configuration loaded from env, .env, and YAML."""

    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    gemini_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "ZettelkastenWeb/1.0"

    @property
    def reddit_oauth_configured(self) -> bool:
        """True iff both Reddit OAuth credentials are non-empty.

        When False, the Reddit ingestor degrades to the public JSON endpoint
        plus HTML scraping, which often returns thin content behind Reddit's
        anti-bot wall.
        """
        return bool(self.reddit_client_id.strip() and self.reddit_client_secret.strip())

    reddit_comment_depth: int = 10

    data_dir: str = "./data"

    server_port: int = 10000
    """Port the dev-mode uvicorn binds to. Production overrides via env PORT."""

    model_name: str = "gemini-2.5-flash"
    rag_chunks_enabled: bool = True

    log_level: str = "INFO"

    newsletter_domains: list[str] = [
        "substack.com",
        "buttondown.email",
        "beehiiv.com",
        "mailchimp.com",
        "medium.com",
        "stackoverflow.com",
        "stackexchange.com",
        "news.ycombinator.com",
        "dev.to",
        "hackernoon.com",
    ]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        yaml_settings = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_DEFAULT_CONFIG_YAML,
        )
        return (init_settings, env_settings, dotenv_settings, yaml_settings)


def _is_production() -> bool:
    """Production is signalled by ENV=production. Anything else is dev-like."""
    return os.environ.get("ENV", "").strip().lower() == "production"


def validate_reddit_credentials(settings: Settings) -> None:
    """Validate Reddit OAuth credentials.

    Behaviour:
      - production AND creds missing → ``RuntimeError`` (hard fail-fast).
      - non-production AND creds missing → one-shot warning.
      - creds present → no-op.

    The warning fires at most once per process via a module-level latch.
    """
    global _reddit_warning_emitted

    if settings.reddit_oauth_configured:
        return

    if _is_production():
        raise RuntimeError(
            "Reddit OAuth credentials are required in production. "
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET, or unset ENV=production."
        )

    if _reddit_warning_emitted:
        return
    logger.warning(
        "Reddit OAuth credentials missing (REDDIT_CLIENT_ID and/or "
        "REDDIT_CLIENT_SECRET are unset). Reddit ingestion will use public "
        "JSON fallback; set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET for "
        "full-quality extraction."
    )
    _reddit_warning_emitted = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    validate_reddit_credentials(settings)
    return settings
```

- [ ] **Step 2: Verify file no longer references the deleted concepts**

```bash
grep -nE "telegram|allowed_chat|webhook_(mode|url|port|secret)|kg_directory|github_(token|repo|branch|enabled)|REDDIT_OPTIONAL" website/core/settings.py
```
Expected: no matches.

---

## Task 1.2: Rewrite `tests/unit/website/test_settings.py`

**Files:**
- Modify: `tests/unit/website/test_settings.py`

- [ ] **Step 1: Replace file contents**

```python
from __future__ import annotations

import logging

import pytest

from website.core import settings as settings_module
from website.core.settings import Settings, get_settings, validate_reddit_credentials


def test_settings_exposes_website_fields() -> None:
    settings = Settings()

    assert "substack.com" in settings.newsletter_domains
    assert isinstance(settings.rag_chunks_enabled, bool)
    assert settings.server_port == 10000


def test_get_settings_returns_singleton() -> None:
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert isinstance(first, Settings)


# ─── Reddit OAuth credential validation ──────────────────────────────────────


@pytest.fixture(autouse=False)
def reset_reddit_warning_latch() -> None:
    settings_module._reddit_warning_emitted = False
    yield
    settings_module._reddit_warning_emitted = False


def test_reddit_oauth_configured_true_when_both_present() -> None:
    s = Settings(
        reddit_client_id="sample-id",
        reddit_client_secret="sample-secret",
    )
    assert s.reddit_oauth_configured is True


def test_reddit_oauth_configured_false_when_secret_missing() -> None:
    s = Settings(reddit_client_id="sample-id", reddit_client_secret="")
    assert s.reddit_oauth_configured is False


def test_reddit_oauth_configured_false_when_id_missing() -> None:
    s = Settings(reddit_client_id="", reddit_client_secret="sample-secret")
    assert s.reddit_oauth_configured is False


def test_reddit_oauth_configured_false_when_whitespace_only() -> None:
    s = Settings(reddit_client_id="   ", reddit_client_secret="   ")
    assert s.reddit_oauth_configured is False
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/website/test_settings.py -v
```
Expected: 6 passed.

---

## Task 1.3: Rewrite `tests/unit/website/test_reddit_creds_fail_fast.py`

**Files:**
- Modify: `tests/unit/website/test_reddit_creds_fail_fast.py`

The previous file gated on `webhook_secret`; the new gate is `ENV=production`.

- [ ] **Step 1: Replace file contents**

```python
"""Reddit OAuth credential gate.

In production (ENV=production) AND Reddit OAuth credentials missing, startup
must raise RuntimeError. In non-production, missing creds produce a one-shot
warning instead.
"""
from __future__ import annotations

import pytest

import website.core.settings as settings_module
from website.core.settings import Settings, validate_reddit_credentials


@pytest.fixture(autouse=True)
def _reset_warning_latch(monkeypatch):
    """Each test starts with the warning latch cleared and ENV unset."""
    settings_module._reddit_warning_emitted = False
    monkeypatch.delenv("ENV", raising=False)
    yield
    settings_module._reddit_warning_emitted = False


def _s(**overrides) -> Settings:
    base = dict(reddit_client_id="", reddit_client_secret="")
    base.update(overrides)
    return Settings(**base)


def test_production_with_missing_reddit_creds_raises(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="Reddit OAuth"):
        validate_reddit_credentials(_s())


def test_production_with_full_reddit_creds_no_raise(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    validate_reddit_credentials(
        _s(reddit_client_id="id", reddit_client_secret="secret")
    )


def test_non_production_with_missing_creds_warns_not_raises(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    validate_reddit_credentials(_s())
    assert any("Reddit OAuth" in r.getMessage() for r in caplog.records)


def test_non_production_warning_fires_only_once(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    validate_reddit_credentials(_s())
    validate_reddit_credentials(_s())
    validate_reddit_credentials(_s())
    reddit_warnings = [
        rec for rec in caplog.records
        if "Reddit OAuth" in rec.getMessage()
    ]
    assert len(reddit_warnings) == 1
```

- [ ] **Step 2: Run**

```bash
pytest tests/unit/website/test_reddit_creds_fail_fast.py -v
```
Expected: 4 passed.

---

## Task 1.4: Strip removed fields from `ops/config.yaml`

**Files:**
- Modify: `ops/config.yaml`

- [ ] **Step 1: Replace file contents**

```yaml
# Zettelkasten Website — default configuration
# ==============================================
# Non-secret defaults only. Secrets (API keys, OAuth creds) must be
# supplied via environment variables — never commit them here.
#
# Environment variable names match the field names in ALL_CAPS.

# ── Google Gemini ─────────────────────────────────────────────────────────────
# Leave empty; set GEMINI_API_KEY env var at runtime.
gemini_api_key: ""

# Model to use for summarisation (gemini-2.5-flash is fast and cheap).
model_name: "gemini-2.5-flash"
rag_chunks_enabled: true

# ── Reddit (Nexus ingestor) ───────────────────────────────────────────────────
# Leave empty; set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET env vars.
reddit_client_id: ""
reddit_client_secret: ""
reddit_user_agent: "ZettelkastenWeb/1.0"
reddit_comment_depth: 10

# ── Storage paths ─────────────────────────────────────────────────────────────
data_dir: "./data"

# ── Server ────────────────────────────────────────────────────────────────────
server_port: 10000

# ── Logging ───────────────────────────────────────────────────────────────────
log_level: "INFO"

# ── Newsletter source detection ───────────────────────────────────────────────
newsletter_domains:
  - substack.com
  - buttondown.email
  - beehiiv.com
  - mailchimp.com
  - medium.com
  - stackoverflow.com
  - stackexchange.com
  - news.ycombinator.com
  - dev.to
  - hackernoon.com
```

- [ ] **Step 2: Verify**

```bash
grep -nE "telegram|webhook_(mode|url|port|secret)|kg_directory|github" ops/config.yaml
```
Expected: no matches.

---

## Task 1.5: Strip removed env vars from `ops/.env.example`

**Files:**
- Modify: `ops/.env.example`

- [ ] **Step 1: Open the file and delete the offending blocks**

Delete these lines / blocks (locate by anchor, then delete the contiguous block):

| Anchor (current text) | What to delete |
|---|---|
| `# Zettelkasten Capture Bot — environment variable template` | Replace heading with `# Zettelkasten Website — environment variable template` |
| `TELEGRAM_BOT_TOKEN=...` | The whole `# ── Required ──` block (header line through `ALLOWED_CHAT_ID=...`) |
| `# ── Webhook (optional — leave unset for polling / development mode) ──` | The whole webhook block (4 lines after the header) |
| `# KG_DIRECTORY=./kg_output` | This single line (and its `# ── Storage` block IF the only remaining line is `DATA_DIR=` — keep `DATA_DIR=` line and the header) |
| `# ── GitHub Note Storage (optional — for cloud deployment) ──` | The whole GitHub Note Storage block (4 lines: header + 3 setting lines) |

The first two lines after the new top-of-file comment header should look like:

```bash
# Zettelkasten Website — environment variable template
# Copy this file to .env and fill in the required values.
#   cp .env.example .env
#
# Secrets must NEVER be committed to version control.
# Non-secret defaults can also be set in ops/config.yaml.
#
# OPERATOR OVERRIDE PATTERN (post-iter-12 Task 23):
#   1. Long-term knob changes go in .github/workflows/deploy-droplet.yml STATIC_BODY (committed).
#   2. Emergency / experimental overrides go in /opt/zettelkasten/compose/.env.local on the droplet
#      (SSH only; survives master pushes; Docker Compose later-wins).
#   3. Never `echo >> /opt/zettelkasten/compose/.env` — that file is rewritten on every push.

# ── Google Gemini (required for AI summarisation) ──────────────────────────────
```

(i.e. the `# ── Required ──` Telegram block is gone — file jumps straight from the operator-override comment to the Gemini block.)

The "Storage" section becomes:

```bash
# ── Storage (optional — defaults shown) ───────────────────────────────────────
# DATA_DIR=./data
```

- [ ] **Step 2: Verify**

```bash
grep -nEi "telegram|allowed_chat_id|webhook_(mode|url|port|secret)\b|^kg_directory|^# KG_DIRECTORY|github_token|github_repo|github_branch|obsidian-kg" ops/.env.example
```
Expected: no matches.

- [ ] **Step 3: Commit Phase 1**

```bash
git add website/core/settings.py tests/unit/website/test_settings.py tests/unit/website/test_reddit_creds_fail_fast.py ops/config.yaml ops/.env.example
git commit -m "refactor: strip telegram, webhook, obsidian, github fields from settings"
```

---

# Phase 2 — Writers

## Task 2.1: Delete Obsidian + GithubRepo writers, drop their tests

**Files:**
- Delete: `website/features/summarization_engine/writers/obsidian.py`
- Delete: `website/features/summarization_engine/writers/github_repo.py`
- Modify: `website/features/summarization_engine/writers/__init__.py`
- Modify: `website/features/summarization_engine/tests/unit/test_batch_and_writers.py`
- Modify: `tests/unit/website/test_persist_polish.py`

- [ ] **Step 1: Delete the writer source files**

```bash
git rm website/features/summarization_engine/writers/obsidian.py
git rm website/features/summarization_engine/writers/github_repo.py
```

- [ ] **Step 2: Replace `writers/__init__.py`**

```python
from website.features.summarization_engine.writers.base import BaseWriter
from website.features.summarization_engine.writers.markdown import render_markdown
from website.features.summarization_engine.writers.supabase import SupabaseWriter

__all__ = [
    "BaseWriter",
    "SupabaseWriter",
    "render_markdown",
]
```

- [ ] **Step 3: Edit `website/features/summarization_engine/tests/unit/test_batch_and_writers.py`**

Delete these imports at top:
```python
from website.features.summarization_engine.writers.obsidian import ObsidianWriter
from website.features.summarization_engine.writers.github_repo import GithubRepoWriter
```

Delete these test functions in their entirety:
- `test_obsidian_writer_writes_markdown`
- `test_github_writer_updates_existing_file`

Keep `test_render_markdown_contains_frontmatter` (the `render_markdown` helper stays per scope decision A+B).

- [ ] **Step 4: Verify the test file is still importable**

```bash
python -c "import website.features.summarization_engine.tests.unit.test_batch_and_writers"
```
Expected: no output (clean import).

- [ ] **Step 5: Run the test file**

```bash
pytest website/features/summarization_engine/tests/unit/test_batch_and_writers.py -v
```
Expected: 6 passed (was 8: 2 deleted, 6 remain — `test_load_batch_input_csv`, `test_load_batch_input_json`, `test_load_batch_input_rejects_oversized_payload`, `test_batch_request_rejects_too_many_urls`, `test_batch_request_rejects_invalid_urls`, `test_batch_processor_stress_uses_bounded_workers`, `test_render_markdown_contains_frontmatter` — recount, 7).

- [ ] **Step 6: Update `tests/unit/website/test_persist_polish.py` line 5 docstring**

Find the docstring fragment:
```
``graph.json``) must also be born clean so non-API consumers (Telegram bot,
```
Replace the parenthesised list with:
```
``graph.json``) must also be born clean so persisted text is never re-rendered
```

Re-read the docstring after edit; if it now reads naturally, leave the rest. The `test_render_markdown_strips_caveats_and_rewrites_reddit_tag` test stays — `render_markdown` still exists.

- [ ] **Step 7: Run**

```bash
pytest tests/unit/website/test_persist_polish.py -v
```
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add website/features/summarization_engine/writers/__init__.py \
        website/features/summarization_engine/tests/unit/test_batch_and_writers.py \
        tests/unit/website/test_persist_polish.py
git commit -m "refactor: delete obsidian and github_repo writers"
```

---

# Phase 3 — App entrypoint, run.py, persist.py

## Task 3.1: Update `website/main.py` to use `server_port`

**Files:**
- Modify: `website/main.py`

- [ ] **Step 1: Edit line 96**

Find:
```python
    port = settings.webhook_port or 10000
```
Replace with:
```python
    port = settings.server_port
```

- [ ] **Step 2: Edit line 97 logging string**

Find:
```python
    logger.info("Starting Zettelkasten website on 0.0.0.0:%d (uvicorn dev mode)", port)
```
(unchanged — keep as-is; it doesn't reference deleted concepts.)

- [ ] **Step 3: Verify**

```bash
grep -n "webhook_port\|telegram\|obsidian\|syncthing" website/main.py
```
Expected: no matches.

---

## Task 3.2: Update `website/app.py` docstring

**Files:**
- Modify: `website/app.py`

- [ ] **Step 1: Replace the top-of-file docstring**

Find lines 1-5:
```python
"""FastAPI application factory for the web frontend.

Serves the static web UI and the /api routes.  In webhook mode, also
handles Telegram webhook forwarding so both services share a single port.
"""
```
Replace with:
```python
"""FastAPI application factory for the web frontend.

Serves the static web UI and the /api routes.
"""
```

- [ ] **Step 2: Decide whether to drop the `lifespan` parameter**

Run:
```bash
grep -nE "create_app\(.*lifespan" .
```
Expected callers: `website/main.py:87` (`app = create_app(lifespan=_lifespan)`) and possibly tests.

If `website/main.py` is the only caller passing `lifespan`, the parameter stays — it's a real, in-use feature for the proc-stats logger task. Do not remove it.

- [ ] **Step 3: Verify**

```bash
grep -nEi "telegram|webhook" website/app.py
```
Expected: no matches.

---

## Task 3.3: Update `run.py` to drop `WEBHOOK_PORT` fallback

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Edit line 37 of run.py**

Find:
```python
        "--bind", f"0.0.0.0:{os.environ.get('PORT', os.environ.get('WEBHOOK_PORT', '10000'))}",
```
Replace with:
```python
        "--bind", f"0.0.0.0:{os.environ.get('PORT', '10000')}",
```

- [ ] **Step 2: Verify**

```bash
grep -nE "WEBHOOK_PORT|telegram|obsidian" run.py
```
Expected: no matches.

---

## Task 3.4: Clean `website/core/persist.py` doc-comment references

**Files:**
- Modify: `website/core/persist.py`

- [ ] **Step 1: Edit module docstring (lines 3-6)**

Find:
```
result into the knowledge graph. Every ingest path (Telegram bot, website
``/api/summarize``, eval register scripts, future callers) should call
:func:`persist_summarized_result`.
```
Replace with:
```
result into the knowledge graph. Every ingest path (website ``/api/summarize``,
eval register scripts, future callers) should call
:func:`persist_summarized_result`.
```

- [ ] **Step 2: Edit comment near line 112**

Find:
```python
# ``<SENTINEL:foo>``) that pollute Obsidian notes and KG node summaries.
```
Replace with:
```python
# ``<SENTINEL:foo>``) that pollute persisted KG node summaries.
```

- [ ] **Step 3: Edit `_encode_summary_payload` docstring (around line 494)**

Find:
```python
    """Serialize brief + detailed summaries as JSON so both survive persistence.

    Applies the deterministic polish + caveat-strip stack at the WRITE
    boundary so every persisted row is born clean. Idempotent — re-encoding
    an already-polished payload is a no-op. This complements the read-time
    polish in ``summary_normalizer.normalize_summary_for_wire`` so non-API
    consumers (Telegram bot, Obsidian export, GitHub writer) see the same
    cleaned text.
    """
```
Replace with:
```python
    """Serialize brief + detailed summaries as JSON so both survive persistence.

    Applies the deterministic polish + caveat-strip stack at the WRITE
    boundary so every persisted row is born clean. Idempotent — re-encoding
    an already-polished payload is a no-op. Complements the read-time polish
    in ``summary_normalizer.normalize_summary_for_wire``.
    """
```

- [ ] **Step 4: Verify**

```bash
grep -nEi "telegram|obsidian|github\s*(writer|export)" website/core/persist.py
```
Expected: no matches.

- [ ] **Step 5: Commit Phase 3**

```bash
git add website/main.py website/app.py run.py website/core/persist.py
git commit -m "refactor: rename webhook_port to server_port and clean doc comments"
```

---

# Phase 4 — Caddy + ops files

## Task 4.1: Strip Telegram log-skip from Caddyfile

**Files:**
- Modify: `ops/caddy/Caddyfile`

- [ ] **Step 1: Delete lines 46-49 (telegram log_skip block)**

Find:
```caddyfile
    # Skip access logging for the Telegram webhook.
    @telegram path /telegram/webhook
    log_skip @telegram

```
Delete the whole 4-line block (including the trailing blank line so the file remains tidy). The next block (`# Application reverse proxy — upstream is hot-swappable`) should now follow the previous block (`# 30s cache for the public KG endpoint`) directly.

- [ ] **Step 2: Verify**

```bash
grep -nEi "telegram|obsidian|syncthing" ops/caddy/Caddyfile
```
Expected: no matches.

---

## Task 4.2: Update `ops/deploy/deploy.sh` comment

**Files:**
- Modify: `ops/deploy/deploy.sh`

- [ ] **Step 1: Edit comment around line 36-39**

Find:
```bash
# iter-03 §1C.4: extract ONLY the DEPLOY_* audit metadata from the container
# .env file (which the GH Actions workflow writes via the already-NOPASSWD-
# allowed sudo /usr/bin/tee path). Avoids full-sourcing the file so the rest
# of the .env (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, SUPABASE_*, etc.) stays
# scoped to the docker --env-file path and never leaks into deploy.sh's
# shell.
```
Replace `TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, SUPABASE_*` with `GEMINI_API_KEY, SUPABASE_*` so the comment reads:
```bash
# of the .env (GEMINI_API_KEY, SUPABASE_*, etc.) stays
```

- [ ] **Step 2: Verify (no other telegram refs in deploy/* scripts)**

```bash
grep -nri "telegram" ops/deploy/
```
Expected: no matches.

- [ ] **Step 3: Commit Phase 4**

```bash
git add ops/caddy/Caddyfile ops/deploy/deploy.sh
git commit -m "ops: drop telegram log-skip and stale env comment"
```

---

# Phase 5 — Memory guard

## Task 5.1: Drop `/telegram/webhook` from memory guard exempt list (if present)

**Files:**
- Modify: `website/api/_memory_guard.py` (only if it contains a `/telegram/webhook` literal)
- Modify: `tests/unit/api/test_memory_guard_exempts_health.py`

- [ ] **Step 1: Search for the literal in `_memory_guard.py`**

```bash
grep -nE "/telegram/webhook" website/api/_memory_guard.py
```

- [ ] **Step 2: If found**, locate the exempt-prefix tuple/list and remove the `/telegram/webhook` entry. Do not touch `/api/health` or `/api/admin/` exemptions.

If not found, skip to Step 3 — the test was just exercising the safety property "non-existent paths still aren't 503-ed", which is a generic invariant, not a telegram-specific one.

- [ ] **Step 3: Edit `tests/unit/api/test_memory_guard_exempts_health.py`**

Delete the entire `test_telegram_webhook_path_passes_through_under_pressure` test function (~7 lines, including the function header).

Update the module docstring (lines 1-4):

Find:
```python
"""Iter-03 mem-bounded §2.9: middleware MUST NOT shed exempt paths even when
VmRSS is over the threshold. Exempt prefixes: /api/health, /api/admin/,
/telegram/webhook, /favicon.ico, /favicon.svg.
"""
```
Replace with:
```python
"""Iter-03 mem-bounded §2.9: middleware MUST NOT shed exempt paths even when
VmRSS is over the threshold. Exempt prefixes: /api/health, /api/admin/,
/favicon.ico, /favicon.svg.
"""
```

- [ ] **Step 4: Run**

```bash
pytest tests/unit/api/test_memory_guard_exempts_health.py -v
```
Expected: 4 passed (was 5; one telegram test deleted).

- [ ] **Step 5: Commit Phase 5**

```bash
git add website/api/_memory_guard.py tests/unit/api/test_memory_guard_exempts_health.py
git commit -m "refactor: drop telegram webhook exemption from memory guard"
```

---

# Phase 6 — Documentation purge

## Task 6.1: Delete Syncthing/VPS docs

**Files:**
- Delete: `docs/SYNCTHING-ALTERNATIVES.md`
- Delete: `docs/VPS-RECOMMENDATIONS.md`

These two docs are entirely about the Telegram-bot + Obsidian + Syncthing topology and have no website-relevant content.

- [ ] **Step 1: Delete files**

```bash
git rm docs/SYNCTHING-ALTERNATIVES.md docs/VPS-RECOMMENDATIONS.md
```

- [ ] **Step 2: Search for inbound links**

```bash
grep -rni "SYNCTHING-ALTERNATIVES\|VPS-RECOMMENDATIONS" --include="*.md"
```
If any other doc links to these, delete those links inline (do not rewrite the surrounding paragraph unless the deletion makes it ungrammatical).

---

## Task 6.2: Rewrite `website/features/summarization_engine/About.md`

**Files:**
- Modify: `website/features/summarization_engine/About.md`

- [ ] **Step 1: Replace contents**

```markdown
# Summarization Engine v2

Pure-library summarization engine that ingests URLs from 9 content sources and produces structured Zettelkasten summaries via tiered Gemini 2.5 Pro + Flash.

## Public API
- `summarize_url(url, user_id)` - single URL, real-time
- `BatchProcessor(user_id).run(input_path | input_bytes)` - CSV/JSON batch
- Single writer: `SupabaseWriter` (writes the structured summary into `kg_nodes.summary`).

## Integration
- `/api/v2/summarize` and `/api/v2/batch*` endpoints alongside existing `/api/summarize`
- Reuses `website/features/api_key_switching/key_pool.py`

See `docs/superpowers/specs/2026-04-10-summarization-engine-v2-design.md` for full design.
```

---

## Task 6.3: Surgical `CLAUDE.md` rewrite

**Files:**
- Modify: `CLAUDE.md`

This is the largest single edit. Goal: drop telegram/obsidian/syncthing as live concepts while preserving production-discipline + RAG iteration history + infra guardrails. Use sed-like surgical edits, NOT a wholesale rewrite.

- [ ] **Step 1: Replace the `## What This Is` section**

Find:
```markdown
## What This Is

Zettelkasten Capture Bot — a Telegram bot that captures URLs (Reddit, YouTube, GitHub, newsletters, generic web) and writes AI-summarised Obsidian notes to a local knowledge graph. Python 3, async, uses python-telegram-bot v21+.

**Status**: Production-ready. ~530 tests passing (CI). DigitalOcean blue/green deploy stack merged: 2026-04-10.
**Repo**: https://github.com/chintanmehta21/Zettelkasten_KG
**Obsidian KG**: `C:\Users\LENOVO\Documents\Syncthing\Obsidian\KG`
**Verified sources**: YouTube, GitHub, Newsletter (Substack), Generic (HN/web)

Two interfaces: Telegram bot (primary) and a FastAPI web UI (`website/`) with REST API at `/api/summarize` and an interactive 3D knowledge graph at `/knowledge-graph`.
```
Replace with:
```markdown
## What This Is

Zettelkasten Website — a FastAPI web app that captures URLs (Reddit, YouTube, GitHub, newsletters, generic web) and produces AI-summarised entries in a Supabase-backed knowledge graph. Python 3, async, deployed on DigitalOcean blue/green.

**Status**: Production-ready. DigitalOcean blue/green deploy stack merged: 2026-04-10.
**Repo**: https://github.com/chintanmehta21/Zettelkasten_KG
**Verified sources**: YouTube, GitHub, Newsletter (Substack), Generic (HN/web)

Single interface: a FastAPI web UI (`website/`) with REST API at `/api/summarize` and an interactive 3D knowledge graph at `/knowledge-graph`.
```

- [ ] **Step 2: Replace the `## Commands` section**

Find:
```markdown
## Commands

\`\`\`bash
# Run the bot (polling/dev mode)
python run.py
# or
python -m telegram_bot

# Run all tests
```

(through the rest of that fenced block)

Replace with:
```markdown
## Commands

\`\`\`bash
# Run the website (dev mode)
ENV=dev python run.py

# Run the website (production mode — gunicorn + uvicorn)
python run.py

# Run all tests
pytest

# Run a single test file
pytest tests/unit/website/test_settings.py -v

# Run unit tests only (skip network-dependent tests)
pytest tests/ -m "not live"

# Run live integration tests (requires real API creds in .env)
pytest --live

# Coverage
pytest --cov=website --cov-report=term-missing

# Install runtime dependencies only
pip install -r ops/requirements.txt

# Install dev/test dependencies
pip install -r ops/requirements-dev.txt
\`\`\`
```

- [ ] **Step 3: Delete the entire `### Note Storage` subsection**

Find:
```markdown
### Note Storage
- **Local mode:** Notes written to `KG_DIRECTORY` (default `./kg_output`). In production droplet deploys this is backed by a host volume.
- **Cloud mode:** When `GITHUB_TOKEN` and `GITHUB_REPO` are set, notes are pushed via GitHub Contents API (base64-encoded PUT). `settings.github_enabled` returns True when both are set. `GITHUB_BRANCH` defaults to `main`.
```
Delete the heading and both bullets.

- [ ] **Step 4: Delete the `### Bot Layer` section**

Find the section beginning with:
```markdown
### Bot Layer

`main.py` wires everything: builds the PTB Application, registers CommandHandlers ...
```
Delete the heading and the entire prose block plus the two sub-bullets (`- **Polling mode** ...` and `- **Webhook mode** ...`) plus the trailing single-paragraph bot/handlers description. Stop deleting when you hit the next heading (`### Web UI`).

- [ ] **Step 5: Delete or shrink the `### Source Extractors (plugin pattern)` section**

The current section talks about `telegram_bot/sources/` auto-discovery — that module no longer exists.

Find the section starting:
```markdown
### Source Extractors (plugin pattern)

Extractors live in `telegram_bot/sources/`. **Auto-discovery**: ...
```
Delete the entire section through to the next `### ` heading.

If `website/` does have a parallel auto-discovery in some other path, leave a one-paragraph stub at the spot:

```markdown
### Source Extractors

Each source (Reddit, YouTube, GitHub, Newsletter, generic web) is encapsulated in `website/features/summarization_engine/summarization/<source>/`. The summarization engine dispatches via the `SourceType` enum in the engine's models module.
```

- [ ] **Step 6: Edit the `## Configuration` section**

Find the paragraph:
```markdown
Settings are loaded by `telegram_bot/config/settings.py` (Pydantic BaseSettings) from three sources in priority order: env vars > `.env` file > `ops/config.yaml`. Secrets (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, REDDIT_CLIENT_*) must be in env vars or `.env`, never in config.yaml.
```
Replace with:
```markdown
Settings are loaded by `website/core/settings.py` (Pydantic BaseSettings) from three sources in priority order: env vars > `.env` file > `ops/config.yaml`. Secrets (GEMINI_API_KEY, REDDIT_CLIENT_*, SUPABASE_*) must be in env vars or `.env`, never in config.yaml.
```

The `Settings` singleton is accessed everywhere via `get_settings()` (lru_cache). Keep that sentence as-is.

- [ ] **Step 7: Edit the `## Architecture` section**

Find anywhere `telegram_bot.` is referenced (e.g. `telegram_bot/pipeline/orchestrator.py`, `telegram_bot/sources/`) — replace with the website-equivalent path. Where the section describes the bot's polling/webhook bifurcation, delete that bifurcation; the system is single-mode (FastAPI + Supabase).

If a sub-section (`#### API Key Pool & Model Fallback`) mentions `/etc/secrets/api_env` (Render-era path), keep it — that path is still mounted on the droplet container and is in active use.

If the architecture section header mentions Telegram, drop the mention. Section names should describe what's there now.

- [ ] **Step 8: Drop the `**Obsidian KG**` user-fact line at the very top of the file**

Find:
```markdown
**Obsidian KG**: `C:\Users\LENOVO\Documents\Syncthing\Obsidian\KG`
```
Delete it entirely.

- [ ] **Step 9: Verify CLAUDE.md final state**

```bash
grep -nEi "telegram[_-]?bot|obsidian[_-]?(writer|export|note)|syncthing|polling/dev mode|allowed_chat_id|webhook_secret|webhook_mode" CLAUDE.md
```
Expected: zero matches in body content. Acceptable surviving matches: lines that reference `obsidian_export/` as a path under `docs/supabase_data/` (the migration capture folder), or historical references like "The legacy Obsidian writer was removed in iter-13" if you choose to add a one-line history note.

- [ ] **Step 10: Sync `AGENTS.md`**

```bash
# AGENTS.md is auto-synced from CLAUDE.md by ops/git-hooks/pre-commit.
# A staged-only commit triggers it. We just need to ensure the hook runs.
git add CLAUDE.md
git commit -m "docs: rewrite CLAUDE.md to reflect website-only architecture"
# Verify AGENTS.md was regenerated
git diff HEAD~1 AGENTS.md | head -20
```

If `AGENTS.md` did not regenerate, run the regeneration step manually (see `ops/git-hooks/pre-commit` for the exact command), commit again.

---

## Task 6.4: Surgical `README.md` rewrite

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Open `README.md` and apply the same removal pattern as CLAUDE.md**

Targets to remove:
- Top-of-readme description framing the project as a Telegram bot.
- Any "Obsidian", "Syncthing", "polling mode", "telegram_bot", "webhook secret" mentions.
- Any commands of the form `python -m telegram_bot` or `python run.py --webhook ...`.
- The "Note Storage" section if present.

Targets to keep / rewrite:
- Project overview as a website + Supabase-backed KG.
- Setup steps: install requirements, set env vars (Gemini, Supabase, Reddit), run `python run.py`.
- Architecture overview pointing at `website/` + `supabase/` + `ops/`.
- Deployment via DigitalOcean blue/green.

The detailed wording is at the executor's discretion, subject to:
- No env-var tables (per user feedback memory: "No Env Vars in README" — keep project structure with major folders only).
- No purple, no Obsidian/Telegram/Syncthing.

- [ ] **Step 2: Verify**

```bash
grep -nEi "telegram|obsidian|syncthing|webhook_secret|webhook_mode|allowed_chat" README.md
```
Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README to reflect website-only architecture"
```

---

# Phase 7 — Verification

## Task 7.1: Live-pipeline integration test triage

**Files:**
- Modify or delete: `tests/integration_tests/test_live_pipeline.py`

- [ ] **Step 1: Read the file**

```bash
cat tests/integration_tests/test_live_pipeline.py
```

- [ ] **Step 2: Decide branch**

| Condition | Action |
|---|---|
| File only orchestrates the website pipeline (URL → summary → Supabase) and the `TELEGRAM_BOT_TOKEN` reference is purely a vestigial cred-presence check or docstring | Delete the telegram-related cred check and update the docstring; keep the test file. |
| File invokes any actual telegram code path (PTB, `telegram_bot.*` import, `Bot(...)` instantiation) | Delete the entire file: `git rm tests/integration_tests/test_live_pipeline.py` |

- [ ] **Step 3: Verify the live-pipeline test file no longer references telegram**

```bash
grep -nEi "telegram" tests/integration_tests/test_live_pipeline.py 2>/dev/null || echo "(file deleted)"
```

- [ ] **Step 4: Commit**

```bash
git add -A tests/integration_tests/
git commit -m "test: drop telegram references from live-pipeline integration test"
```

---

## Task 7.2: Final grep sweep

**Files:** none (verification only)

- [ ] **Step 1: Code grep**

```bash
grep -rniE "telegram|obsidian[_-]?(writer|export|note)|syncthing" \
  --include="*.py" --include="*.yaml" --include="*.yml" --include="*.json" --include="*.sh" --include="*.toml" \
  --exclude-dir=docs --exclude-dir=.claude-mem-queue --exclude-dir=.claude --exclude-dir=models
```
Expected: zero matches outside `docs/supabase_data/` (which legitimately has `obsidian_export/`).

If there are residual matches, classify each:
- Stale comment / docstring → delete the offending words inline.
- Active code reference → STOP, surface to operator.

- [ ] **Step 2: Doc grep (excluding plans + supabase_data)**

```bash
grep -rniE "telegram[_-]?bot|polling/dev mode|allowed_chat_id|webhook_(mode|secret)|kg_directory" \
  docs/ \
  --exclude-dir=docs/superpowers/plans \
  --exclude-dir=docs/superpowers/specs \
  --exclude-dir=docs/supabase_data
```
Expected: zero matches.

(`docs/superpowers/plans` and `docs/superpowers/specs` are historical and explicitly out of scope per scope decision #7B/C.)

- [ ] **Step 3: Settings grep**

```bash
grep -rniE "TELEGRAM_BOT_TOKEN|ALLOWED_CHAT_ID|WEBHOOK_(MODE|URL|PORT|SECRET)\b|KG_DIRECTORY|GITHUB_(TOKEN|REPO|BRANCH)" \
  ops/ website/ tests/ run.py pyproject.toml
```
Expected: zero matches.

---

## Task 7.3: Full pytest run

- [ ] **Step 1: Run unit tests (no live)**

```bash
pytest tests/ website/ -m "not live" --tb=short -q
```
Expected: every test passes.

- [ ] **Step 2: If failures, classify**

| Failure | Action |
|---|---|
| Test imports a deleted symbol (`ObsidianWriter`, `GithubRepoWriter`, `webhook_secret`) | Update the test, or delete it if obsolete. |
| Test asserts on `validate_reddit_credentials` old behavior | Already covered by Task 1.3 rewrite — re-run that test file. |
| Test imports `telegram_bot.*` | Delete the test (the bot module is already gone; the test is dead). |
| Test fails for a non-related reason | STOP and surface — that's a regression unrelated to this plan. |

- [ ] **Step 3: Commit any test follow-up fixes**

```bash
git add -A tests/ website/
git commit -m "test: align suite with website-only settings shape"
```

---

## Task 7.4: Smoke deploy + production verification (per scope decision #17=B)

This task ships the changes through the existing CI/CD blue/green pipeline and verifies the production droplet is healthy.

**HARD GATE:** Before merging anything to `master`, confirm the operator is available to triage if the deploy fails. Per CLAUDE.md, blind mitigations during a 5xx storm are forbidden — if the smoke fails, surface the failure with logs, do not blanket-revert.

- [ ] **Step 1: Push the branch**

```bash
git push origin <current-branch>
```

- [ ] **Step 2: Open PR and let CI run**

The deploy workflow `.github/workflows/deploy-droplet.yml` runs `pytest` against the new code with stubbed env vars. Expect:
- Unit tests: pass.
- Migration L2 gate: pass (no schema changes in this plan).
- Image build: succeeds on `ghcr.io/chintanmehta21/zettelkasten-kg-website:<sha>`.

If pytest fails in CI but passed locally, the most likely cause is an env-var coupling — check stubs in the workflow.

- [ ] **Step 3: Merge and watch the deploy**

Once CI is green, merge to `master`. The deploy job runs `/opt/zettelkasten/deploy/deploy.sh <sha>`. Expected log progression:
1. `[migration] OK — proceeding with blue/green flip.`
2. `[stage2-assert] <color> stage2 session OK`
3. `[rag-smoke] <color> smoke probe OK`
4. `[caddy-smoke] public probe via Caddy OK (HTTP 200)`
5. `DEPLOY SUCCEEDED`

- [ ] **Step 4: Manual smoke (operator)**

```bash
curl -s https://zettelkasten.in/api/health | jq .
curl -s -o /dev/null -w '%{http_code}\n' https://zettelkasten.in/
curl -s -o /dev/null -w '%{http_code}\n' https://zettelkasten.in/knowledge-graph
```
Expected: 200/200/200.

Optional (full-stack write path — only if the operator wants to spend a Gemini call):
```bash
# Replace <jwt> with a fresh Naruto JWT
curl -X POST https://zettelkasten.in/api/summarize \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "user_sub": "<naruto-sub>"}'
```
Expected: 200, body includes `"node_id"`.

- [ ] **Step 5: Verify the droplet env file no longer references deleted vars**

```bash
ssh deploy@<droplet-ip>
sudo grep -E '^(TELEGRAM_BOT_TOKEN|ALLOWED_CHAT_ID|WEBHOOK_(MODE|URL|PORT|SECRET)|KG_DIRECTORY|GITHUB_(TOKEN|REPO|BRANCH))=' /opt/zettelkasten/compose/.env || echo "(none)"
```
Expected: `(none)`. If matches found, the operator should remove them with a single `sudo sed -i '/^TELEGRAM_BOT_TOKEN=/d' /opt/zettelkasten/compose/.env` (and similar for each), then restart the active container (`docker restart zettelkasten-<active>`). The app already ignores them (Pydantic `extra="ignore"`), so this cleanup is hygiene rather than correctness-critical.

---

## Self-review notes (executor: skim before declaring done)

- ✅ All HARD GATES preserved: no change to `GUNICORN_WORKERS`, `--preload`, timeouts, semaphore, heartbeat, schema-drift gate, allowlist gate, kasten/KG colour rules.
- ✅ Phase order minimises temporary breakage: settings/env first, writers next, app last; tests updated alongside their referent code.
- ✅ Each phase ends in a committable green state.
- ✅ TDD where applicable (tests rewritten before / alongside code in Phase 1; Phase 2's deletions paired with test deletions).
- ✅ All paths absolute or repo-relative; commit subjects ≤10 words and prefixed correctly per CLAUDE.md commit discipline.
- ✅ No silent infra knob changes.
- ✅ Surgical doc edits (CLAUDE.md, README.md) preserve operational war-stories and infra guardrails.
- ✅ `RAZORPAY_WEBHOOK_SECRET` (different system) untouched.
- ✅ Verification gate is full pytest + grep sweep + smoke deploy (decision #17=B).

## Out of scope (do not expand)

- ❌ Touching `docs/superpowers/plans/` or `docs/superpowers/specs/` historical content (out per scope #7).
- ❌ Touching the user's local `~/.claude/.../memory/MEMORY.md` (personal memory).
- ❌ Capturing data from Supabase (separate plan: `2026-05-08-supabase-data-capture.md`).
- ❌ Provisioning the new Supabase project.
- ❌ The `dev/` folder (untracked).
- ❌ Removing `markdown.py` `render_markdown` helper (kept per scope decision A+B).
- ❌ Rewriting git history.
