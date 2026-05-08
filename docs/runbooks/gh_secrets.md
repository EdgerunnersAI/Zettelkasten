# GitHub Actions Secrets — Audit & Rotation Runbook

iter-03 Phase 1D.2 deliverable. This is the canonical list of GitHub
Actions secrets that the deploy + ops workflows in this repo consume.
The runbook below is the exact `gh` CLI sequence an operator runs to
**audit** what is currently set and **rotate** any value. Nothing in
this document is executed automatically — every command requires an
explicit human invocation.

> Repo: `chintanmehta21/Zettelkasten_KG`
>
> **Do not** paste secret values into chat or commit them to the repo.
> Wrap any secret value in `<private>...</private>` tags before sharing.

---

## 1. Required secrets

The following secrets are **required** for `deploy-droplet.yml` and
`read_recent_logs.yml` to succeed. Missing any of these aborts the
workflow with a clear error.

| Secret name             | Consumer(s)                                   | What it is                                                                                                                                                                  |
|-------------------------|-----------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `DROPLET_HOST`          | `deploy-droplet.yml`, `read_recent_logs.yml`  | DigitalOcean Reserved IP (or DNS) of the production droplet.                                                                                                                |
| `DROPLET_SSH_USER`      | `deploy-droplet.yml`, `read_recent_logs.yml`  | SSH login user (typically `deploy`).                                                                                                                                        |
| `DROPLET_SSH_PORT`      | `deploy-droplet.yml`, `read_recent_logs.yml`  | SSH port (default `22`; pin to whatever the droplet listens on).                                                                                                            |
| `DROPLET_SSH_KEY`       | `deploy-droplet.yml`, `read_recent_logs.yml`  | Private key (PEM) authorised on the droplet for `DROPLET_SSH_USER`. Generate a per-environment keypair; never reuse a personal SSH key.                                     |
| `GHCR_READ_PAT`         | `deploy-droplet.yml`                          | Classic personal access token with `read:packages` scope so the droplet can `docker pull` from `ghcr.io`. Owned by the `chintanmehta21` GitHub account.                     |
| `SUPABASE_DB_URL`       | `deploy-droplet.yml` (via container env-file) | Postgres connection string for the prod Supabase project. **Must** be the IPv4 pooler endpoint (`postgres.<ref>:<DB_PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres`). The script `apply_migrations.py` hard-fails without it. |

## 2. Optional / app-runtime secrets

These are not required by the workflow itself but are forwarded into the
container `.env` by the `Build container .env body from environment secrets`
step in `deploy-droplet.yml`. Setting them updates production behaviour.

| Secret name                       | Purpose                                                                                                                       |
|-----------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `GEMINI_API_KEY` / `GEMINI_API_KEYS` | Single key or newline-separated list. The pool prefers `GEMINI_API_KEYS`.                                                   |
| `SUPABASE_URL`                    | Public Supabase URL (for the website KG client; not the DB DSN).                                                              |
| `SUPABASE_ANON_KEY`               | Anon JWT for the website KG client.                                                                                           |
| `SUPABASE_SERVICE_ROLE_KEY`       | Service-role JWT used by ops scripts (e.g. `migrate_graph_to_supabase.py`). **Not** the DB password.                          |
| `REDDIT_CLIENT_ID`                | Reddit OAuth client id. Without it, RAG chunk density caps at ~1/post.                                                        |
| `REDDIT_CLIENT_SECRET`            | Reddit OAuth client secret.                                                                                                   |

## 3. Audit current state (read-only)

Run on a workstation with `gh` installed (`gh auth login` first if needed):

```bash
# Git Bash / PowerShell on workstation
gh secret list --repo chintanmehta21/Zettelkasten_KG
```

Expected: a table of secret names + last-updated timestamps. Cross-check
the names against §1 and §2 above. Anything in the workflow YAML that
is **not** in the printed list will fail at deploy time.

To audit a specific secret without revealing its value (only existence
+ last-updated):

```bash
gh secret list --repo chintanmehta21/Zettelkasten_KG \
  | grep -E '^(SUPABASE_DB_URL|DROPLET_HOST|DROPLET_SSH_USER|DROPLET_SSH_PORT|DROPLET_SSH_KEY|GHCR_READ_PAT)\b'
```

## 4. Set or rotate a secret (write — requires human approval)

> Each command below is the **exact** invocation an operator runs.
> Do **not** paste real values into chat. Use `--body-file -` so the
> value is read from stdin and never appears in shell history.

### 4.1 Required-set examples

```bash
# Git Bash / PowerShell on workstation
# SUPABASE_DB_URL — from Supabase Studio > Project Settings > Database
gh secret set SUPABASE_DB_URL \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -
# (paste the postgresql:// URL, then Ctrl-D)

# DROPLET_HOST — Reserved IP from DigitalOcean dashboard
gh secret set DROPLET_HOST \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -

# DROPLET_SSH_USER — typically "deploy"
gh secret set DROPLET_SSH_USER \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -

# DROPLET_SSH_PORT — typically "22"
gh secret set DROPLET_SSH_PORT \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -

# DROPLET_SSH_KEY — full PEM; pipe from a local file then shred it.
gh secret set DROPLET_SSH_KEY \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file ~/.ssh/zettelkasten_deploy_droplet
# After confirming the secret is set, shred the key from the workstation
# if it lives only on this machine:
#   shred -u ~/.ssh/zettelkasten_deploy_droplet  # Linux/macOS
#   Remove-Item -Force ~/.ssh/zettelkasten_deploy_droplet  # PowerShell

# GHCR_READ_PAT — classic GH PAT with read:packages
gh secret set GHCR_READ_PAT \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -
```

### 4.2 Optional-set examples

```bash
# Git Bash / PowerShell on workstation
gh secret set GEMINI_API_KEYS \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file ~/.config/zettelkasten/gemini_keys.txt

gh secret set REDDIT_CLIENT_SECRET \
  --repo chintanmehta21/Zettelkasten_KG \
  --body-file -
```

## 5. Verification after rotation

1. `gh secret list --repo chintanmehta21/Zettelkasten_KG` — confirm
   `Updated` column moved to the current timestamp for the rotated key.
2. Trigger a `workflow_dispatch` run of `Deploy to DigitalOcean Droplet`
   (or push a no-op commit to `master`). Tail the workflow run with
   `gh run watch`. The `Build container .env body from environment secrets`
   step must report a non-zero `secret_count`. The migration step must
   not log `[deploy] FATAL: SUPABASE_DB_URL missing`.
3. Trigger `Read Recent Logs` with default inputs. Download the
   `recent-logs` artifact and confirm it contains live application logs
   (i.e. the SSH key + droplet host + user are correct).

## 6. What this runbook deliberately does **not** do

- **No real `gh secret set` invocations live in CI or in any script.**
  All writes are operator-driven. The CI pipeline only ever reads
  secrets that were set by a human via the commands above.
- No secret values are echoed, logged, or committed. Every command in
  §4 reads the value from stdin or a local file and never from an
  environment variable that might leak via process listings.
- This runbook does not auto-rotate. Rotation cadence is operator
  judgement — rotate `DROPLET_SSH_KEY` and `GHCR_READ_PAT` at least
  yearly and immediately on suspected compromise.
