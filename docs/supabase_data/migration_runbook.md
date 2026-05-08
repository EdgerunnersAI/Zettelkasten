# New Supabase Project Migration Runbook

This is the bootstrap procedure for a fresh Supabase project after the previous one was banned. Follow in order.

## Prerequisites

- New Supabase project created (note the project ref + region).
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_DB_URL` (IPv4 pooler) set locally for `apply_migrations.py`.
- This repo at the sha that produced `docs/supabase_data/MANIFEST.json` (verify via `git log`).

## Step 1 — Apply schemas

```bash
# Git Bash from repo root
SUPABASE_DB_URL="<new pooler URL>" \
SUPABASE_ACCESS_TOKEN="<new token>" \
python ops/scripts/apply_migrations.py
```

The script reads `supabase/website/kg_public/migrations/*.sql` in order and uses an advisory lock so concurrent applies are safe. The L2 CI gate (added iter-12) ensures every migration here is also exercised in CI.

> **Why not from `docs/supabase_data/schemas/`?** The script reads from the in-repo path, not the captured copy. The capture is a *backup*; the canonical schemas are the working tree. If the working tree has drifted from the capture, reconcile manually before applying.

## Step 2 — Verify schema

```bash
psql "$SUPABASE_DB_URL" -c "\dt kg_public.*"
psql "$SUPABASE_DB_URL" -c "SELECT count(*) FROM kg_public.migrations_applied;"
```

Expect every table from `supabase/website/kg_public/schema.sql` to be present and `migrations_applied.count` to match the file count of the migrations dir.

## Step 3 — Recreate sandbox membership rows

The `kg_users` and `rag_sandbox_members` rows are environment-specific and were not exportable from the banned project. Recreate manually via Supabase Studio or `psql` using the user emails / IDs known to the operator.

## Step 4 — Re-ingest from Obsidian export (optional, one-shot)

For each `url` in `docs/supabase_data/obsidian_export/INDEX.json` that still resolves, re-feed through `/api/summarize` on the running website (against the new project). Since the deferred re-ingestion script is not built yet, this is currently a manual loop — adapt the snippet below for your environment:

```bash
# Git Bash
python - <<'PY'
import json, time, requests
idx = json.load(open("docs/supabase_data/obsidian_export/INDEX.json"))
for entry in idx:
    url = entry.get("url", "")
    if not url:
        continue
    r = requests.post(
        "https://zettelkasten.in/api/summarize",
        json={"url": url, "user_sub": "<your-user-sub>"},
        headers={"Authorization": "Bearer <your-jwt>"},
        timeout=120,
    )
    print(r.status_code, url)
    time.sleep(2)  # be polite to the API
PY
```

## Step 5 — Switch the droplet over

Update `/opt/zettelkasten/compose/.env.local` on the droplet (operator-override path that survives master pushes):

```bash
# Droplet SSH
sudo tee -a /opt/zettelkasten/compose/.env.local <<'EOF'
SUPABASE_URL=<new project URL>
SUPABASE_ANON_KEY=<new anon key>
SUPABASE_SERVICE_ROLE_KEY=<new service role key>
SUPABASE_DB_URL=<new pooler URL>
EOF
docker compose -f /opt/zettelkasten/compose/docker-compose.<active>.yml up -d --force-recreate
```

## Step 6 — Smoke test

```bash
curl https://zettelkasten.in/api/health
curl https://zettelkasten.in/api/graph
```

Expect 200 from both. `/api/graph` should return at least the file-store-fallback nodes.
