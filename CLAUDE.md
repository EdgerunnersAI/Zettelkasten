# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Zettelkasten Website — a FastAPI web app that captures URLs (Reddit, YouTube, GitHub, newsletters, generic web) and produces AI-summarised entries in a Supabase-backed knowledge graph. Python 3, async, deployed on DigitalOcean blue/green.

**Status**: Production-ready. DigitalOcean blue/green deploy stack merged: 2026-04-10.
**Repo**: https://github.com/chintanmehta21/Zettelkasten_KG
**Verified sources**: YouTube, GitHub, Newsletter (Substack), Generic (HN/web)

Single interface: a FastAPI web UI (`website/`) with Add Zettel API at `/api/zettels/add` and an interactive 3D knowledge graph at `/knowledge-graph`.

## Production Change Discipline

This is a live production web application with active users across frontend, backend/API, and database layers. Every change has immediate real-world impact, and anything that cannot scale with user growth is unacceptable.

**Before any change:**
- Read and understand the full in-scope code path. Verify behavior from code, docs, tests, and existing patterns; do not assume.
- Identify every touched component, dependency, integration point, and side effect before editing.
- Reason through edge cases up front, including empty states, invalid inputs, concurrent requests, simultaneous writes, race conditions, auth failures, upstream/network timeouts, and data inconsistency risks.

**When implementing:**
- Ship complete, self-contained changes only. No TODOs, placeholders, stubs, or partial follow-ups.
- Preserve backward compatibility unless the user explicitly approves a breaking change.
- If a change has meaningful production risk, stop and ask for confirmation before proceeding. State the risk clearly and propose the safest path.
- Do not treat ambiguous behavior as settled. Resolve ambiguity explicitly from code or docs, or surface the uncertainty.

**Testing requirements:**
- Add or update tests for happy paths, edge cases, boundary/invalid inputs, and relevant scale/concurrency scenarios.
- For stateful or shared-resource paths, cover high concurrency, simultaneous writes, race conditions, and session collision risks where the code path can plausibly fail under load.
- Run the relevant test suite and verify existing tests still pass. If anything fails, diagnose and fix the regression before considering the task complete.

**Communication style:**
- Be concise and technical. State what changed, why, impact, and any residual risk.
- Use bullet points only for major tasks; keep smaller updates brief.
- Ask for confirmation before risky changes.
- State uncertainty explicitly. Never present assumptions as facts.
- Cite relevant documentation, specifications, or library behavior when correctness depends on those details.
- Flag the conditions under which the implementation could break.

**Never:**
- Leave the codebase in a broken, partial, or knowingly fragile state.
- Skip tests because a change appears simple.
- Ignore scalability, concurrency, or failure-mode analysis.
- Proceed through ambiguity without acknowledging it.
- Present assumptions as verified facts.

## When to ask vs when to keep moving

The "skip clarifying questions" rule from earlier user feedback was scoped too broadly. Corrected scope (2026-04-28):

**Skip questions** when:
- In dashboard-only mode (recurring progress-bar loops, autonomous execution loops).
- Inside a locked execution loop where the plan is already agreed and the steps are mechanical.

**Ask questions freely** when:
- Planning a new iteration, design, refactor, or eval scope.
- Brainstorming trade-offs or comparing approaches.
- About to touch any knob in the "Critical Infra Decision Guardrails" section below.
- Any change with production blast-radius, security impact, or that would revert a prior iteration's deliberate decision.
- Anything ambiguous that could be interpreted more than one way.

**Never (without explicit user approval in chat):**
- Make a major / irreversible / infra decision because triage is taking time.
- Push an "infra mitigation" while logs / evidence are still in flight.
- Revert a protected knob from a prior iteration as a reflex response to a 5xx storm.
- Treat your own hypothesis as fact without verification.

User: "Skip clarifying questions" applies only inside execution-task loops in dashboard-only mode. Otherwise — ask. *Never* make major important decisions without explicit approval, and stop repeating the same mistakes.

## Critical Infra Decision Guardrails (HARD RULES — never silently undo)

**These are infrastructure decisions baked into prior iterations with explicit rationale. They MUST NOT be reverted, downgraded, or "blindly mitigated" without (a) reproducing the failure with logs in hand and (b) the user's explicit authorization in the chat.** A failing health check or a 5xx storm is NOT authorization — it is the trigger to root-cause, not to revert.

Specifically forbidden as a reflex response to production errors:
- Reducing `GUNICORN_WORKERS` below 2 on the production droplet. The whole point of the iter-03 BGE int8 quantization (Phase 1A, ~110 MB RAM saving via COW + `--preload`) was to keep 2 workers viable on the 2 GB droplet so the system handles concurrent users at scale. Halving worker count silently undoes that work.
- Disabling `--preload` (workers each load their own model — re-explodes RAM).
- Switching the int8 cascade back to fp32 by setting `FP32_VERIFY_ENABLED=true` for everything (Phase 1A.5 made it a top-3 verifier only).
- Lowering `GUNICORN_TIMEOUT` below 180s (Phase 1B reasoned 180s minimum for Strong/Pro multi-hop synth).
- Disabling the rerank semaphore / bounded queue (Phase 1B.2). The 503 backpressure path is the burst-correctness mechanism.
- Removing the SSE heartbeat wrapper (Phase 1B.4). Cloudflare 502s on idle non-streaming responses are exactly what it prevents.
- Reverting blue/green Caddy timeouts to defaults (the explicit `transport http { read_timeout 240s ... }` block is the upstream-timeout fix for slow synth).
- Disabling the schema-drift gate (Phase 1C.5) or the `kg_users` allowlist gate (Phase 2D.2) without explicit operator approval per occurrence.
- Switching colors on the Kasten surface to anything other than teal, or putting amber outside `/knowledge-graph`.

When prod is failing and one of these knobs looks tempting:
1. **STOP.** Pull droplet logs (`gh workflow run read_recent_logs.yml`), Caddy access log, container `dmesg`, `free -h`. Read first.
2. State the hypothesis with evidence. Not "it might be OOM" — show the OOM line.
3. Propose the targeted fix that DOES NOT touch the protected knobs. (Examples: fix the actual exception, add the swapfile, bump a single timeout, retry policy, fix a leak.)
4. If the only viable fix touches a protected knob, **ask the user explicitly with the trade-off named** before pushing. e.g. "Logs show OOM at 1.8 GB during cold-load — temporarily setting GUNICORN_WORKERS=1 until we add the swapfile?" — then wait.

**Penalty pattern for the assistant:** if you're tempted to push a "blind mitigation" because triage is taking time, that's the exact moment to slow down. Production change discipline (CLAUDE.md §1) overrides perceived urgency. The user has consistently chosen "wait for logs + correct fix" over "fast-but-wrong revert".

## Research Discipline (read before every plan / brainstorm / design)

**The rule:** Every plan, design, recommendation, or question set must be grounded in completed, verified research. Never reason from assumptions, punch-list summaries, or partial agent output when more research is in flight.

**When you dispatch background subagents:**
1. Wait for **every** dispatched agent to complete before making recommendations or asking decisive questions. A partial picture will silently shape the question set with wrong defaults — and the user will catch you, costing more time than waiting did.
2. If you must communicate before all agents return, state explicitly which agents are still running and that you are NOT yet making recommendations.
3. After each agent returns, fully digest its output and check it against any prior recommendation you made — flag and revise anything contradicted.
4. Only after all agents return AND their outputs have been cross-checked, issue the question set or design.

**When the user asks "rapid-fire" or "be fast":**
- Speed up communication, not research. Batch questions, drop ceremony, skip preamble.
- Do NOT speed up by skipping research, by reasoning from punch-list claims, or by guessing at file locations / current behavior.
- Verify every code claim with `smart_search` / `Read` / `Grep` before stating it. Punch-list items often contain stale assumptions about file paths, threshold names, or query-class labels — treat them as hypotheses to verify, not facts.
- "Punch list says X exists at file:line" → check the file. Roughly half the time, the claim is wrong or out of date.

**Verification before recommendation:**
- For every clarifying question with a recommended default, the recommendation must cite a verified file or fact, not a punch-list summary or a memory.
- For every "fix at file:line" in a design, confirm the file and line exist now.
- For every infra constraint (RAM, workers, timeouts), measurement or research must justify the number — never a guess.

**When in doubt, dispatch a scout.** A 90-second architecture-scout agent is always cheaper than one wrong recommendation that the user has to correct, then a revised recommendation, then a re-revised plan.

## Commands

```bash
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
```

## Deployment Infrastructure (Canonical)

**This is the ONLY production environment.** Earlier iterations of this app ran on Render.com; that platform is **legacy / no longer used**. Any doc, comment, or plan that references Render, `*.onrender.com`, the Render dashboard, or "Render Secret Files" is historical context unless explicitly stated otherwise.

- **Provider:** DigitalOcean
- **Droplet:** Premium Intel — 2 GB RAM, 1 vCPU, 70 GB NVMe SSD
- **Networking:** DigitalOcean Reserved IP attached (stable public IP across droplet lifecycle events)
- **Stack:** Docker Compose blue/green (app containers bind `127.0.0.1:10000` and `127.0.0.1:10001`) fronted by a Caddy 2 container that terminates TLS (Let's Encrypt) and reverse-proxies to whichever color is live
- **CI/CD:** GitHub Actions builds the image to `ghcr.io/chintanmehta21/zettelkasten-kg-website:<git-sha>`, then SSHes into the droplet and runs `/opt/zettelkasten/deploy/deploy.sh <git-sha>` to flip colors with a graceful Caddy reload (zero dropped connections)
- **DNS:** Cloudflare (delegated from GoDaddy), apex `zettelkasten.in` -> Reserved IP

### GitHub Actions

`.github/workflows/deploy-droplet.yml` runs on pushes to `master`:
- Runs `pytest` with stubbed env vars (no real secrets required for unit tests).
- Builds and pushes `ghcr.io/chintanmehta21/zettelkasten-kg-website` using `ops/Dockerfile`.
- SSH deploys to the droplet and runs `/opt/zettelkasten/deploy/deploy.sh <git-sha>`.

### Blue/Green Compose

Compose files live in `ops/`:
- `docker-compose.blue.yml` binds `127.0.0.1:10000`.
- `docker-compose.green.yml` binds `127.0.0.1:10001`.
- Caddy terminates TLS and proxies to the active color (see `ops/caddy/`).

### Secrets / Env Vars

See `ops/.env.example` for the canonical list. Common ones:
- Required: `GEMINI_API_KEYS` (preferred) or `GEMINI_API_KEY`
- Optional: `SUPABASE_URL`, `SUPABASE_ANON_KEY` (Supabase KG), `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`

## Configuration

Settings are loaded by `website/core/settings.py` (Pydantic BaseSettings) from three sources in priority order: env vars > `.env` file > `ops/config.yaml`. Secrets (GEMINI_API_KEY, REDDIT_CLIENT_*, SUPABASE_*) must be in env vars or `.env`, never in config.yaml.

The `Settings` singleton is accessed everywhere via `get_settings()` (lru_cache). Tests that need settings without valid credentials should be careful — `get_settings()` calls `_validate_settings()` which does `SystemExit(1)` on missing required fields.

### Reddit credentials and RAG chunk density

`REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are **required for OAuth-backed Reddit extraction** (used by the website `RedditIngestor` and by `ops/scripts/backfill_chunks.py --refetch-source` when it encounters `/r/` URLs). Without them the ingestor degrades to the public JSON endpoint + HTML scraping, which often returns thin content for Reddit's anti-bot walls and caps RAG chunk density at ~1 chunk per post. On the production droplet, set both in the container env (via `--env-file`) or in the secret file mounted at `/etc/secrets/api_env`. See `ops/.env.example` for the template.

## Architecture

### Add Zettel Pipeline (the core flow)

`website/api/zettels_routes.py` is the website Add Zettel facade. It validates and normalizes URLs, resolves the authenticated user, maps anonymous captures to the canonical Zoro user, calls `website/features/summarization_engine/core/orchestrator.py::summarize_url_bundle`, converts the engine result into the website DTO, then persists through `website/core/persist.py::persist_summarized_result`.

#### API Key Pool & Model Fallback

A centralized `GeminiKeyPool` (`website/features/api_key_switching/`) manages up to 10 API keys with key-first traversal: `key1/gemini-2.5-flash` → `key2/gemini-2.5-flash` → ... → `key1/gemini-2.5-flash-lite` → `key2/gemini-2.5-flash-lite`. On a 429 rate-limit, it tries the next key (same model) before downgrading to the next model tier. Content-aware routing sends short/simple content to `flash-lite` first to preserve `flash` quota for complex content. Keys are loaded from an `api_env` file (one key per line) at project root or `/etc/secrets/api_env` (the secret-file path mounted into the droplet container; the path was originally adopted from Render conventions and carried over), with fallback to `GEMINI_API_KEY` for backward compatibility. If ALL keys/models fail, the engine surfaces a structured failure to the caller. For YouTube, it can bypass transcript extraction and send the video URL directly to Gemini's video understanding API.

### Source Extractors

Each source (Reddit, YouTube, GitHub, Newsletter, generic web) is encapsulated in `website/features/summarization_engine/summarization/<source>/`. The summarization engine dispatches via the `SourceType` enum in the engine's models module.

### Web UI (`website/`)

FastAPI app. Two main pages: a URL summarizer at `/` and a 3D knowledge graph visualizer at `/knowledge-graph`. Mobile browsers auto-redirect to `/m/` (detected via user-agent regex in `website/app.py`).

- `website/api/zettels_routes.py` — `POST /api/zettels/add` with typed request/response DTOs, idempotency, bounded concurrency, structured problem responses, and optional 202 status polling.
- `website/api/routes.py` — `GET /api/graph` returns KG data and `GET /api/health` serves container / load balancer health checks.
- `website/core/graph_store.py` — thread-safe in-memory store backed by `website/features/knowledge_graph/content/graph.json`. Auto-links new nodes to existing ones based on shared normalized tags. Node IDs use source-type prefixes (`yt-`, `gh-`, `rd-`, `ss-`, `md-`, `web-`) + slugified title.

#### Supabase Knowledge Graph (`website/core/supabase_kg/`)

Supabase v2 is the canonical user zettel store. Add Zettel persistence writes through `content.canonical_zettels`, `content.workspace_zettels`, and `content.canonical_chunks` via `content.upsert_canonical_zettel`; the file graph remains a public graph mirror/fallback surface.

- `client.py` — Supabase client init via `get_supabase_client()`, gated by `is_supabase_configured()`
- `models.py` — Pydantic models: `KGNode`, `KGLink`, `KGUser`, `KGGraph` (with Create variants)
- `repository.py` — `KGRepository` with CRUD: `get_or_create_user()`, `add_node()`, `node_exists()`, `get_graph()`
- Schema: `supabase/website/kg_public/schema.sql` (tables: `kg_users`, `kg_nodes`, `kg_links`)
- Migration: `python ops/scripts/migrate_graph_to_supabase.py` — migrates `graph.json` data to Supabase (requires `supabase/.env` with `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`)

### URL Utilities (`utils/url_utils.py`)

Security-conscious URL handling: `validate_url()` blocks private/reserved IPs (SSRF protection), `normalize_url()` strips tracking params (utm_*, fbclid, etc.) and sorts query params for dedup consistency, `resolve_redirects()` follows chains async with HEAD-first strategy, `is_shortener()` detects 16 known shortener domains.

### Data Models

`models/capture.py` defines the shared Pydantic models: `SourceType` (enum), `CaptureRequest`, `ExtractedContent`, `ProcessedNote`. All pipeline stages communicate through these.

## Code Navigation (smart-explore is the default)

For **any code file in this repo** (`*.py`, `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.go`, `*.rs`, `*.java`, `*.cs`, etc.), always prefer the claude-mem `smart-explore` skill over `Read` / `Grep` / `Glob`:

1. **Discover** with `smart_search(query=..., path="./website")` — replaces the Glob → Grep → Read cycle.
2. **Map a file** with `smart_outline(file_path=...)` — replaces reading a full file.
3. **Zoom to one symbol** with `smart_unfold(file_path=..., symbol_name=...)` — replaces reading a range.

**Fallback ladder** (use standard tools *only* when smart-explore can't handle the task):
- File is <100 lines, a non-code file (JSON/YAML/TOML/Markdown/config), or `.env*` → use `Read`.
- Need exact string / regex match (e.g., hunting `TODO`, a log string, a specific import path) → use `Grep`.
- Need filesystem path patterns (e.g., all test files, all Dockerfiles) → use `Glob`.
- Need cross-file narrative synthesis across 6+ files → dispatch the `Explore` agent.
- `smart_search` returns zero hits *and* the target is known to exist → fall back to `Grep` with the same query, then `Read` the single matching file.

Rule of thumb: **the question before every code-file read is "can I get a structural overview first?"** If yes, smart-explore.

## Multi-phase Work (make-plan → do)

Any task that spans **2+ phases, 3+ files, or requires verification gates** must be driven by the `make-plan` → `do` skill pair from claude-mem:

1. **`make-plan`** — Phase 0 is always Documentation Discovery (no implementation until docs/APIs are confirmed). Each subsequent phase has: what to implement, documentation references, verification checklist, anti-pattern guards.
2. **`do`** — executes the plan via subagents: one Implementation subagent per phase, then Verification, Anti-pattern, Code Quality, and Commit subagents. Never advance a phase until verification passes.

Single-file tweaks, typo fixes, and isolated bug patches do **not** need this pipeline — go direct.

## Secrets Handling (`<private>` tags)

claude-mem's hook layer strips `<private>...</private>` tags **before** observations reach the worker/DB. Wrap every secret value or file excerpt in these tags whenever it appears in assistant output, plan text, tool arguments, or commit descriptions.

**Always wrap content from:**
- `new_envs.txt` (project root — untracked)
- `.env` (project root)
- `supabase/.env`
- Any other `.env*` file in this repo except `ops/.env.example` (which is a template with no real values)
- Any file under `~/.ssh/`, droplet SSH private keys, or key material pasted into the chat

**Always wrap values of:**
- `GEMINI_API_KEY`, `GEMINI_API_KEYS`
- `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_URL` (project ref is sensitive)
- `GITHUB_TOKEN`
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
- Any droplet IP, hostname, or SSH key fingerprint

**Example:** `GEMINI_API_KEY=<private>AIzaSy...redacted</private>`.

**Never** echo full `.env` file contents into assistant output without wrapping. **Never** commit secrets — and if you read one into context, treat the memory record as contaminated and flag it.

## Memory Tagging (claude-mem observation types)

claude-mem captures every session under the `code` mode with 6 observation types: `bugfix`, `feature`, `refactor`, `change`, `discovery`, `decision`. Frame prompts and response openings so the observer tool tags them correctly — this makes future `search(obs_type=...)` queries sharp.

**Deliberate framing (state the type in the first sentence when ambiguous):**
- Bug fixes → "Fixing X which was broken because Y" → tagged `bugfix`
- New capability → "Adding X feature that now supports Y" → tagged `feature`
- Restructure with same behavior → "Refactoring X to separate Y from Z" → tagged `refactor`
- Docs/config/misc → "Updating X config" → tagged `change`
- Reading existing code to understand → "Discovering how X works" → tagged `discovery`
- **Architectural/design choice with rationale** → always prefix with **"This is a decision because..."** → tagged `decision`

**Non-negotiable: any of the following must be captured as a `decision` observation with explicit rationale**, even if the user frames it casually:
- GeminiKeyPool traversal order (key-first vs model-first), new model tier additions, content-aware routing thresholds
- Supabase vs file-store precedence, dual-write toggles, schema migrations
- Blue/green cutover trigger, rollback criteria, Caddy upstream changes
- Any new source extractor (plugin vs monkey-patch vs external service)
- Any change to the `orchestrator.process_url` sequence
- Auth/secret-handling model changes
- Dependency additions/removals or Python-version bumps
- Test strategy shifts (live vs stubbed, new marker, new fixture tier)

**Going forward, apply this rule dynamically: whenever you pick between two or more viable approaches for anything not in the list above, announce "this is a decision because..." and record the rationale inline.** The observer will tag it correctly and the narrative will be retrievable via `search(obs_type="decision")` in future sessions.

## Cross-Agent Memory (Claude Code CLI + Claude Code Desktop + Codex Desktop)

Three agents can edit this repo on this machine, plus the non-Claude-Code Claude Desktop chat app as a read-only memory consumer. The system is designed so all four see the same claude-mem brain with zero data duplication.

### Agent topology

| Agent | Binary / config root | Identifies itself via | Write path to claude-mem | Read path from claude-mem |
|---|---|---|---|---|
| **Claude Code CLI** | `~/.local/bin/claude.exe` + `~/.claude/` | `CLAUDECODE=1`, `CLAUDE_CODE_ENTRYPOINT=cli` | Native PostToolUse hook from `thedotmack/claude-mem` plugin (per-tool-use granularity) | claude-mem plugin in `~/.claude/plugins/cache/thedotmack/claude-mem/` |
| **Claude Code Desktop** (embedded in Claude Desktop) | `%APPDATA%\Claude\claude-code\<ver>\claude.exe` | `CLAUDECODE=1`, `CLAUDE_CODE_ENTRYPOINT=claude-desktop` | Same PostToolUse hook as CLI — Claude Desktop's embedded Claude Code shares `~/.claude/` plugins and settings | Same plugin cache as CLI |
| **Codex Desktop** (OpenAI, Appx-installed) | `C:\Program Files\WindowsApps\OpenAI.Codex_<ver>\app\Codex.exe` (Electron) + `app\resources\codex.exe` (engine) with `~/.codex/` as data root | Process-tree walk finds `WindowsApps\OpenAI.Codex` ancestor | Git post-commit hook → `.claude-mem-queue/` → drained by Claude Code at next session start | MCP server `claude_mem` registered in `~/.codex/config.toml` `[mcp_servers.claude_mem]` — same `mcp-server.cjs`, same DB |
| **Claude Desktop** (Electron chat, non-Claude-Code mode) | `%APPDATA%\Claude\` + `claude_desktop_config.json` | N/A (chat, doesn't commit) | None — Claude Desktop chat conversations aren't captured as code work | MCP server `claude_mem` registered in `claude_desktop_config.json` `mcpServers.claude_mem` |

### Five shared layers

1. **Code state** — shared git checkout. No config needed. All three agents edit the same working tree.
2. **Rules** — `CLAUDE.md` (this file) is canonical. `AGENTS.md` at the project root is an auto-synced mirror regenerated by `ops/git-hooks/pre-commit` on every commit that stages `CLAUDE.md` — never edit it by hand. Codex Desktop auto-loads `AGENTS.md` at every session start (project root + walks down from cwd). User-global Codex rules live at `~/.codex/AGENTS.md`. Claude Code (CLI and Desktop) auto-loads `CLAUDE.md` and `~/.claude/CLAUDE.md`.
3. **Folder timelines** — claude-mem writes per-folder `CLAUDE.md` activity files automatically (flag `CLAUDE_MEM_FOLDER_CLAUDEMD_ENABLED=true` in `~/.claude-mem/settings.json`). All three agents read these via their normal `Read` tool — no special integration needed.
4. **Historical memory (claude-mem DB)** — all four clients point at the same SQLite DB at `~/.claude-mem/claude-mem.db` (~109 MB) via the same `mcp-server.cjs` at `~/.claude/plugins/cache/thedotmack/claude-mem/10.5.6/scripts/`. Zero data duplication: Codex Desktop, Claude Desktop, and Claude Code all share one on-disk database.
5. **Write capture** — only Claude Code CLI / Claude Code Desktop have per-tool-use PostToolUse hooks that write observations directly to the DB. Codex Desktop captures commit-granularity via `ops/git-hooks/post-commit`, which writes queue files that Claude Code drains at next session start.

### Write path: the git post-commit queue

`ops/git-hooks/post-commit` fires on every `git commit` regardless of the tool. Its tool-detection chain is (first hit wins):

1. `CLAUDE_MEM_AUTHOR_TOOL` env var — manual override.
2. Tool-set env vars that propagate into git's environment (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CURSOR_TRACE_ID`, `TERM_PROGRAM=vscode`, `JETBRAINS_IDE`, `CODEX_HOME`/`OPENAI_CODEX`).
3. PowerShell single-shot process-tree walk (`Get-CimInstance Win32_Process`, 8 ancestors) — catches GUI agents that don't set env vars. Recognizes `claude-code (desktop)` via the `AppData\Roaming\Claude\claude-code` path, `codex-desktop` via the `WindowsApps\OpenAI.Codex` path, and `cursor` / `vscode` / `jetbrains` / `github-desktop` / `sourcetree` by process name.
4. Fallback: `unknown` — entry still lands in queue, tool can be re-tagged manually.

Activation (run once per clone): `git config core.hooksPath ops/git-hooks`. Already set on the current clone; verify with `git config --get core.hooksPath`.

### Queue-drain protocol (Claude Code CLI or Desktop, at every session start)

1. Check if `.claude-mem-queue/` has any `*.md` files.
2. For each file, read its frontmatter + body, then infer the observation type from the conventional-commit prefix (`feat:` → `feature`, `fix:` → `bugfix`, `refactor:` → `refactor`, `docs:`/`test:`/`chore:`/`style:` → `change`, anything else → `discovery`). Decisions are only tagged when the body contains `"This is a decision because"`.
3. Emit a short chat message per queued entry in the form *"Recording from &lt;tool&gt; commit &lt;sha&gt;: &lt;subject&gt;"* — this triggers the claude-mem observer via normal PostToolUse capture. Use the `tool:` frontmatter value so the observer knows the origin.
4. After the entry is processed, delete the queue file.
5. If a file is >7 days old, archive it to `.claude-mem-queue/archived/` instead of deleting, in case it wasn't ingested correctly.

This turns commits-from-anywhere — CLI, Desktop, Codex, GUI editors — into observations-in-the-DB with zero manual effort.

### Forward-looking: Codex Desktop plugin hooks

Codex Desktop supports a plugin hook system with the same schema as Claude Code plugins: `hooks/hooks.json` + `hooks/scripts/` inside a plugin with both `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` marker files. Hook commands can reference `${CLAUDE_PLUGIN_ROOT}`. At least the `Stop` event is supported; `PostToolUse` coverage is unverified. This means a future tighter capture path is possible: install a minimal claude-mem shim plugin into `~/.codex/plugins/` with a Stop hook that writes per-task entries to `.claude-mem-queue/`, giving sub-commit granularity for Codex Desktop. Not yet wired — the git post-commit path is sufficient for now.

## Git Commits

- **5–10 words max in the commit subject.** Accurately showcase the changes made. No body paragraphs, no bullet lists, no explanations.
- **No tool or author names.** Never mention `Claude`, `Codex`, `Copilot`, `ChatGPT`, or any AI tool / assistant / human author anywhere in the message.
- **No `Co-Authored-By` trailers.** Never append `Co-Authored-By:` lines.
- **Prefix tags** (always use one; the prefix counts toward the word budget):
  - `build:` — Releases only. **Never** use autonomously; only when the user explicitly asks. (e.g., `build: v0.0.1`)
  - `feat:` — Major feature builds. (e.g., `feat: migrate reranker`)
  - `fix:` — Minor fixes or loop deploys. (e.g., `fix: stabilize streaming token test for CI timing`)
  - `chore:` — Ad-hoc tasks; use as "others" when nothing else fits. (e.g., `chore: aggressive Docker prune to reclaim 8.5GB`)
  - `ci:` — Changes to CI/CD pipelines. (e.g., `ci: SHA-pin SCP action in deploy workflow`)
  - `ops:` — Infrastructure and deployment operational tasks. (e.g., `ops: sync deploy scripts to droplet`)
  - `refactor:` — Restructure with same behavior. (e.g., `refactor: extract key pool into module`)
  - `docs:` — Documentation only. (e.g., `docs: update CLAUDE.md commit tags`)
  - `test:` — Test additions or changes only. (e.g., `test: add KG node dedup coverage`)
- When a commit implements a prior-session decision, append the observation ID in parentheses: e.g., `feat(engine): key-first rotation (#S155)`. The `(#S...)` token does not count toward the word budget.

## Testing

- pytest with `asyncio_mode = auto` (`pyproject.toml`)
- Custom `--live` flag: tests marked `@pytest.mark.live` are skipped by default; pass `--live` to run them (they hit real APIs and need `.env` credentials)
- `conftest.py` provides sample URL fixtures (`sample_reddit_url`, `sample_youtube_url`, etc.)
- Integration tests in `tests/integration_tests/` make real network calls
- **Settings in tests**: Always mock `get_settings()` via `@patch` — calling it without valid env vars triggers `SystemExit(1)` from `_validate_settings()`

## UI Design

- **No purple.** Never use purple, violet, or lavender (`hsl(250–290)`, `#A78BFA`, etc.) anywhere in the UI. The Knowledge Graph accent is amber/gold (`#D4A024`); the main site accent is teal.

## Docker

Multi-stage build (`ops/Dockerfile`): Stage 1 installs `ops/requirements.txt` into `/opt/venv` and pre-compiles `.pyc` files for cold-start optimization. Stage 2 copies only the venv and compiled code. Base image: `python:3.12-slim`. Exposes port 10000 (production default). Entry point: `python run.py`. Build with `docker build -f ops/Dockerfile -t zettelkasten-bot .` from the repo root.

## DB v2 Purge — Phase 8 Closeout Complete (2026-05-11)

The DB v2 schema purge Phase 8 closeout is complete. All production code paths run on the v2 schemas (`core, content, kg, rag, pipelines, billing`); legacy `public.kg_*`, `public.rag_*`, `public.chat_*`, `public.summary_batch_*`, `public.nexus_*`, `public.kg_usage_edges*`, `public.kg_kasten_node_freq`, `public.recompute_runs`, `public._migrations_applied`, and the original 5 of 11 `public.pricing_*` tables were dropped in Phase 6 (commit `e168b38`); the remaining 6 `public.pricing_*` tables + 6 RPCs were dropped in Phase 8.0.6 via `supabase/website/_v2/31_drop_legacy_pricing.sql` after the pre-T6 audit confirmed zero live website refs. `website/core/supabase_kg/` directory deleted in the same commit.

**Annotated as LEGACY (broken after 2026-05-11):** 6 `ops/scripts/*` still import the retired `website.core.supabase_kg`; revive by porting `get_supabase_client` calls to `get_v2_client()` from `website.core.supabase_v2.client` in a follow-up iteration.

**Test infrastructure:**
- `tests/v2/fixtures/users.py` — `mint_test_user_with_workspaces` (now includes `email` field per Phase 8.0-TX)
- `tests/integration/v2/conftest.py` — `asyncpg_pool`, `mint_user`, `pytest_sessionfinish` cleanup hook
- 3 known not-live flakes documented (2× quantize_bge_int8, cascade_int8)
- Cross-tenant denial tests hardened with UUID-leak assertions per OWASP API1:2023 BOLA pattern

**Pricing module:**
- v2 canonical = `billing.*` schema. `billing.pricing_consume_entitlement` body protected by golden md5.
- Currently fail-open per operator-locked design; multi-period enforcement is Phase 9 (see `docs/db-v2/phase-9-pricing-enforcement-plan.md`).

**kg_features:**
- Partial cleanup landed (Phase 8.0-H7): `retrieval, nl_query, entity_extractor` deleted (referenced dropped v1 tables); `analytics, embeddings` retained as pure-compute helpers with CI guard allow-list.

**Final acceptance test (queued):** `docs/db-v2/final-acceptance-test-plan.md` — Claude in Chrome on the live site, ingesting URLs from `docs/research/Chintan_Testing.md` as Naruto.
