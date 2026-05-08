# Zettelkasten

A web app that captures URLs from five content sources (Reddit, YouTube, GitHub, newsletters, and generic web articles) and produces AI-summarized entries in a Supabase-backed knowledge graph. Paste any URL into the web UI; get a structured summary with tags.

---

## Features

- **Auto-detect source type** - paste any URL and the engine picks the right extractor automatically
- **Five extractors**: Reddit threads (with top comments), YouTube videos (transcript + metadata), GitHub repos/issues/PRs, newsletter articles (Substack, Beehiiv, Buttondown, Mailchimp), and generic web pages
- **AI summarization** - Google Gemini produces a title, summary, and multi-dimensional tags for every capture
- **More reliable Gemini usage** - supports a comma-separated API key pool (`GEMINI_API_KEYS`) with key rotation plus a model fallback chain (graceful degradation to raw content when needed)
- **Web UI** - FastAPI-powered frontend with a URL summarizer and an interactive 3D knowledge graph (desktop + mobile)
- **Knowledge graph analytics** - enriches nodes with metrics (PageRank, communities, centrality) for better exploration
- **Graph search and Q&A (Supabase-backed)** - hybrid search and natural-language querying when Supabase is configured
- **Accounts (optional)** - Supabase Auth-backed user profiles (including avatar), plus a personal home page and zettels view
- **SSRF protection** - URL validation blocks private/reserved IPs; tracking params are stripped for dedup consistency
- **Zero-downtime deploys** - production is designed around a blue/green Docker Compose stack with TLS termination, health checks, and rollback

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/chintanmehta21/Zettelkasten_KG.git
cd Zettelkasten_KG

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r ops/requirements.txt
pip install -r ops/requirements-dev.txt

# 4. Configure
cp ops/.env.example .env
# Open .env and fill in GEMINI_API_KEYS (preferred) or GEMINI_API_KEY

# 5. Run (dev mode)
ENV=dev python run.py
```

The web app starts on `http://localhost:10000`. Paste any URL on the home page to test.

---

## Configuration

Copy `ops/.env.example` to `.env` and fill in your credentials. Settings are loaded from env vars > `.env` > `ops/config.yaml`. See `ops/.env.example` for the canonical list.

---

---

## Web UI

The FastAPI web frontend serves all traffic, with mobile routes under `/m/`:

| Page | URL | Description |
|---|---|---|
| **Summarizer** | `/` | Paste any URL, get an AI summary with tags |
| **Knowledge Graph** | `/knowledge-graph` | Interactive 3D graph of all summarized nodes |
| **Home (optional)** | `/home` | Signed-in home page (Supabase Auth) |
| **Zettels (optional)** | `/home/zettels` | Your captured notes view |
| **Nexus (optional)** | `/home/nexus` | Provider connections and bulk imports (experimental, Supabase required) |

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/summarize` | Summarize a URL (rate-limited: 10 req/min per IP) |
| `GET` | `/api/graph` | Return the current knowledge graph as JSON |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/auth/config` | Public Supabase config for client-side auth init |
| `GET` | `/api/me` | Current user profile (auth required) |
| `POST` | `/api/graph/search` | Hybrid search (Supabase required) |
| `POST` | `/api/graph/query` | Natural-language Q&A over your graph (Supabase required) |

---

## Deployment

This repo includes several ways to run it locally, plus a production-oriented DigitalOcean droplet setup.

### Local Compose (recommended)

Dev mode (fast iteration):

```bash
docker compose -f ops/docker-compose.dev.yml up --build
```

Production-parity rehearsal (Caddy + blue/green + local TLS):

```bash
docker build -f ops/Dockerfile -t zettelkasten-kg-website:local .
docker compose -f ops/docker-compose.prod-local.yml up
```

### Docker (single container)

Build the production image:

```bash
docker build -f ops/Dockerfile -t zettelkasten-kg-website .
```

Run (web UI + API on port 10000):

```bash
docker run -p 10000:10000 --env-file .env zettelkasten-kg-website
```

### Production: DigitalOcean Droplet (blue/green)

The canonical production environment runs on a DigitalOcean Premium Intel droplet (2 GB RAM / 1 vCPU / 70 GB NVMe SSD) with a Reserved IP, fronted by Cloudflare DNS for the apex `zettelkasten.in`. A Caddy 2 container terminates TLS (Let's Encrypt) and reverse-proxies to whichever Docker Compose color is live: blue binds `127.0.0.1:10000`, green binds `127.0.0.1:10001`.

Deploys are automated from GitHub Actions. On pushes to `master`, `.github/workflows/deploy-droplet.yml` runs the mocked pytest suite, builds `ops/Dockerfile`, pushes `ghcr.io/chintanmehta21/zettelkasten-kg-website:<git-sha>`, then SSHes into the droplet and runs `/opt/zettelkasten/deploy/deploy.sh <git-sha>` to flip colors with a graceful Caddy reload (zero dropped connections).

---

## Running Tests

```bash
# All tests
pytest

# Unit tests only (no network)
pytest tests/ -m "not live"

# A specific test module
pytest tests/unit/website/test_settings.py -v

# With coverage
pytest --cov=website --cov-report=term-missing

# Live integration tests (requires real API creds in .env)
pytest --live
```

---

## Adding a New Source

Source ingestion lives in `website/features/summarization_engine/source_ingest/`. The orchestrator (`summarize_url_bundle`) selects an ingestor from `router.py` based on the resolved URL.

1. Add a new ingestor module under `website/features/summarization_engine/source_ingest/` implementing the ingestor protocol (`ingest(url) -> SourceBundle`).
2. Register the URL pattern in `website/features/summarization_engine/source_ingest/router.py`.
3. Add targeted tests under `tests/unit/website/`.

---

## Project Structure

```
|-- run.py                     # Entry point (delegates to website.main)
|-- pyproject.toml             # Project metadata + pytest config
|-- website/                   # FastAPI web application
|   |-- api/                   # REST API routes (/api/*)
|   |-- core/                  # Web pipeline, graph store, Supabase KG integration
|   |-- features/              # Knowledge graph, auth, home, zettels, etc.
|   |-- mobile/                # Mobile UI (/m/*)
|   `-- static/                # Summarizer page assets
|-- ops/                       # Deployment + operations
|   |-- Dockerfile             # Multi-stage Docker build
|   |-- caddy/                 # Caddy TLS / reverse-proxy config
|   |-- deploy/                # Blue/green deploy, healthcheck, rollback scripts
|   |-- host/                  # Droplet bootstrap, firewall, sysctl, logrotate
|   |-- systemd/               # Service units
|   `-- requirements*.txt      # Runtime vs dev/test dependencies
|-- tests/                     # Unit + integration tests
|   `-- integration_tests/     # Live API tests (--live flag)
|-- supabase/                  # Supabase schema definitions
`-- docs/                      # Additional documentation
```

---


