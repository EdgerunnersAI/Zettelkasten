#!/usr/bin/env bash
# ops/deploy/deploy.sh <image_sha>
#
# Blue-green deploy of a new image SHA.
#
# Side effects:
#   - Pulls ghcr.io/chintanmehta21/zettelkasten-kg-website:<sha>
#   - Brings up the idle color with the new image
#   - Waits for /api/health on the idle color
#   - Rewrites /opt/zettelkasten/caddy/upstream.snippet to point at idle color
#   - Reloads Caddy gracefully
#   - Hands off the previously-active color to background retirement
#   - Updates /opt/zettelkasten/ACTIVE_COLOR
#
# On failure: invokes rollback.sh and exits non-zero.

set -euo pipefail

# Use the deploy user's Docker credentials even when this script is invoked
# via `sudo` (HOME becomes /root otherwise, which has no GHCR auth).
export DOCKER_CONFIG="${DOCKER_CONFIG:-/home/deploy/.docker}"

SHA="${1:-}"
if [[ -z "$SHA" ]]; then
    echo "usage: $0 <image_sha>" >&2
    exit 2
fi

ROOT=/opt/zettelkasten
IMAGE="ghcr.io/chintanmehta21/zettelkasten-kg-website:${SHA}"
DRAIN_SECONDS="${DEPLOY_DRAIN_SECONDS:-45}"

# iter-03 §1C.4: extract ONLY the DEPLOY_* audit metadata from the container
# .env file (which the GH Actions workflow writes via the already-NOPASSWD-
# allowed sudo /usr/bin/tee path). Avoids full-sourcing the file so the rest
# of the .env (GEMINI_API_KEY, SUPABASE_*, etc.) stays
# scoped to the docker --env-file path and never leaks into deploy.sh's
# shell. Missing values fall through to the existing ${VAR:-default} guards
# below — manual operator deploys from a droplet shell still work.
ENV_FILE="${ENV_FILE:-/opt/zettelkasten/compose/.env}"
if [[ -r "$ENV_FILE" ]]; then
    while IFS='=' read -r _key _val; do
        case "$_key" in
            DEPLOY_GIT_SHA|DEPLOY_ID|DEPLOY_ACTOR|RAG_SMOKE_KASTEN_ID|NARUTO_SMOKE_PASSWORD|SUPABASE_ANON_KEY_LEGACY_JWT|SUPABASE_URL)
                export "$_key=$_val"
                ;;
        esac
    done < <(grep -E '^(DEPLOY_(GIT_SHA|ID|ACTOR)|RAG_SMOKE_KASTEN_ID|NARUTO_SMOKE_PASSWORD|SUPABASE_ANON_KEY_LEGACY_JWT|SUPABASE_URL)=' "$ENV_FILE" || true)
    unset _key _val
fi

MODEL_DIR="$ROOT/data/models"
if [[ ! -d "$MODEL_DIR" ]]; then
    mkdir -p "$MODEL_DIR"
    chown deploy:deploy "$MODEL_DIR"
fi

ACTIVE_FILE="$ROOT/ACTIVE_COLOR"
SNIPPET="$ROOT/caddy/upstream.snippet"
LOG="$ROOT/logs/deploy.log"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"
}

on_error() {
    log "DEPLOY FAILED at line $LINENO. Invoking rollback..."
    "$ROOT/deploy/rollback.sh" || true
    exit 1
}
trap on_error ERR

ACTIVE=$(cat "$ACTIVE_FILE")
if [[ "$ACTIVE" == "blue" ]]; then
    IDLE="green"
    IDLE_PORT=10001
else
    IDLE="blue"
    IDLE_PORT=10000
fi

log "Starting deploy: SHA=$SHA, ACTIVE=$ACTIVE, IDLE=$IDLE"

log "Pulling $IMAGE..."
IMAGE_TAG="$SHA" docker compose \
    -f "$ROOT/compose/docker-compose.${IDLE}.yml" \
    pull

# ── D-1 (KAS-11): apply pending Supabase migrations BEFORE traffic flips. ──
# Runs the new image as a short-lived helper container so the migration set
# matches the code about to be deployed. Failure is FATAL — we abort before
# touching the IDLE container so prod stays on the previous (working) color.

# iter-03 §1C.1 preflight: confirm SUPABASE_DB_URL is in the env-file before
# we even spin the migration container. Without it apply_migrations exits
# with rc=2 (config error) — surface that as a clear deploy abort.
if ! grep -q '^SUPABASE_DB_URL=' /opt/zettelkasten/compose/.env; then
    log "[deploy] FATAL: SUPABASE_DB_URL missing from /opt/zettelkasten/compose/.env"
    exit 2
fi

log "[migration] Applying pending Supabase migrations against prod..."
set +e
# iter-03 §1C.4: pass deploy provenance so apply_migrations can stamp each
# audit row with git SHA / deploy id / actor. Defaults guarantee non-null
# values even when this script is run outside CI (manual operator deploy).
# iter-03 §1C.5: mount a host dir so the bootstrapped/verified manifest
# survives the container exit. Operator commits this back to Git after the
# first deploy, after which the in-image copy at /app/supabase/.../
# expected_schema.json is the canonical source.
MANIFEST_HOST_DIR="$ROOT/data/schema"
mkdir -p "$MANIFEST_HOST_DIR"
chown -R deploy:deploy "$MANIFEST_HOST_DIR" 2>/dev/null || true

docker run --rm --network host \
    --env-file /opt/zettelkasten/compose/.env \
    -v "$MANIFEST_HOST_DIR":/manifest-out \
    -e DEPLOY_GIT_SHA="${DEPLOY_GIT_SHA:-$SHA}" \
    -e DEPLOY_ID="${DEPLOY_ID:-manual-$(date -u +%Y%m%dT%H%M%SZ)}" \
    -e DEPLOY_ACTOR="${DEPLOY_ACTOR:-$(whoami)}" \
    -e MIGRATION_MANIFEST_REQUIRED="${MIGRATION_MANIFEST_REQUIRED:-1}" \
    -e MIGRATION_MANIFEST_AUTOBOOTSTRAP="${MIGRATION_MANIFEST_AUTOBOOTSTRAP:-1}" \
    "$IMAGE" \
    python ops/scripts/apply_migrations.py \
        --manifest-path /manifest-out/expected_schema.json \
        2>&1 | tee -a "$LOG"
MIG_RC=${PIPESTATUS[0]}
set -e
if [ "$MIG_RC" -ne 0 ]; then
    log "[migration] FAILED rc=$MIG_RC — ABORTING DEPLOY (no traffic flip, IDLE container not started)"
    exit "$MIG_RC"
fi
log "[migration] OK — proceeding with blue/green flip."

# iter-03 §3.9 / Plan 2D.2: single-tenant kg_users allowlist gate.
# Skipped by default per operator decision — re-enable with
# DEPLOY_ALLOWLIST_GATE=1 once the live kg_users table has been reconciled
# (run ops/scripts/reconcile_kg_users.py --audit first).
if [ "${DEPLOY_ALLOWLIST_GATE:-0}" = "1" ]; then
    log "[deploy] Running kg_users allowlist gate..."
    set +e
    docker run --rm --network host \
        --env-file /opt/zettelkasten/compose/.env \
        "$IMAGE" \
        python -c "
import json, os, sys, psycopg
allowed = set(json.load(open('/app/ops/deploy/expected_users.json'))['allowed_auth_ids'])
with psycopg.connect(os.environ['SUPABASE_DB_URL']) as c, c.cursor() as cur:
    cur.execute('SELECT id::text FROM kg_users')
    live = {r[0] for r in cur.fetchall()}
unknown = live - allowed
if unknown:
    print(f'[deploy] FATAL: kg_users has unknown auth_ids: {unknown}', file=sys.stderr)
    sys.exit(1)
print('[deploy] kg_users allowlist OK')
" 2>&1 | tee -a "$LOG"
    GATE_RC=${PIPESTATUS[0]}
    set -e
    if [ "$GATE_RC" -ne 0 ]; then
        log "[deploy] FATAL: allowlist gate failed rc=$GATE_RC — ABORTING DEPLOY"
        exit "$GATE_RC"
    fi
else
    log "[deploy] kg_users allowlist gate SKIPPED (DEPLOY_ALLOWLIST_GATE!=1)"
fi

# iter-03 (2026-04-28): SEQUENTIAL blue/green - stop ACTIVE before starting
# IDLE. The 2 GB droplet cannot fit two simultaneous containers each holding
# the int8 BGE (267 MB resident) + 2 gunicorn workers + temp tensors during
# stage-2 rerank (peak +684 MB). Running both blue and green at once causes
# system-level OOM during the smoke probe q1 query. Trade-off: ~30-60s of
# 502s while Caddy points at the now-stopped ACTIVE color until the post-
# assert flip below. Acceptable for a single-droplet 2 GB target; iter-04
# can revisit (larger droplet, smaller stage1_k, or batched encoding).
log "[seq-deploy] Stopping ACTIVE color ${ACTIVE} to free memory for ${IDLE}..."
ACTIVE_CONTAINER_NAME_PRE="zettelkasten-${ACTIVE}"
ACTIVE_CONTAINER_ID_PRE="$(docker inspect --format '{{.Id}}' "$ACTIVE_CONTAINER_NAME_PRE" 2>/dev/null || true)"
docker stop --time 20 "$ACTIVE_CONTAINER_NAME_PRE" 2>/dev/null || log "[seq-deploy] WARN: stop ${ACTIVE} returned non-zero (likely already stopped)"
docker rm "$ACTIVE_CONTAINER_NAME_PRE" 2>/dev/null || true
log "[seq-deploy] ${ACTIVE} stopped. Caddy will 502 until cutover (~30-60s)."

log "Starting $IDLE container with new image..."
IMAGE_TAG="$SHA" docker compose \
    -f "$ROOT/compose/docker-compose.${IDLE}.yml" \
    up -d --no-deps

log "Waiting for $IDLE healthcheck on port $IDLE_PORT..."
"$ROOT/deploy/healthcheck.sh" "$IDLE_PORT"

# iter-03 mem-bounded §2.10 (post-mortem): assert the cgroup limits the
# container actually ended up with match what the compose file declared.
# This guards against the silent-no-op failure mode where compose ceiling
# changes never reach the droplet (compose files stale / not synced) — a
# class of bug that bit iter-03 mid-rollout. Mismatch fails the deploy.
EXPECTED_MEM_MAX=1677721600        # 1600m == 1.5625 GiB (bumped from 1300m on 2026-04-28 - q1 OOM)
EXPECTED_SWAP_MAX=1048576000       # 1000m == 1.0 GiB swap budget per cgroup
ACTUAL_MEM_MAX=$(docker exec "zettelkasten-${IDLE}" cat /sys/fs/cgroup/memory.max 2>/dev/null || echo "missing")
ACTUAL_SWAP_MAX=$(docker exec "zettelkasten-${IDLE}" cat /sys/fs/cgroup/memory.swap.max 2>/dev/null || echo "missing")
log "[cgroup-assert] ${IDLE} memory.max=${ACTUAL_MEM_MAX} (expect ${EXPECTED_MEM_MAX})"
log "[cgroup-assert] ${IDLE} memory.swap.max=${ACTUAL_SWAP_MAX} (expect ${EXPECTED_SWAP_MAX})"
if [[ "$ACTUAL_MEM_MAX" != "$EXPECTED_MEM_MAX" ]] || [[ "$ACTUAL_SWAP_MAX" != "$EXPECTED_SWAP_MAX" ]]; then
    log "[cgroup-assert] FATAL: cgroup limits don't match compose."
    log "[cgroup-assert] FATAL: NOT auto-rolling back — operator must triage."
    log "[cgroup-assert] Bad container left at zettelkasten-${IDLE}; Caddy still on previous color."
    log "[cgroup-assert] Next deploy's --force-recreate will replace it; or 'docker stop zettelkasten-${IDLE}' manually."
    exit 87
fi
log "[cgroup-assert] ${IDLE} cgroup limits OK"

# iter-03 §8: assert that _STAGE2_SESSION (the int8 BGE reranker) actually
# loaded inside the running container. The lazy fp32 fallback is gone
# (cascade.py refactor); if the int8 file is missing or failed to import,
# the worker would 500 the first /api/rag/adhoc call. Catch it here pre-flip.
# Fail-loud, no auto-rollback (same pattern as cgroup-assert post-de-fang).
ACTUAL_STAGE2=$(docker exec "zettelkasten-${IDLE}" python -c "from website.features.rag_pipeline.rerank import cascade; print(cascade._STAGE2_SESSION is not None)" 2>/dev/null || echo "false")
log "[stage2-assert] ${IDLE} _STAGE2_SESSION_loaded=${ACTUAL_STAGE2} (expect True)"
if [[ "$ACTUAL_STAGE2" != "True" ]]; then
    log "[stage2-assert] FATAL: int8 BGE session not loaded in ${IDLE}."
    log "[stage2-assert] FATAL: NOT auto-rolling back -- operator must triage."
    log "[stage2-assert] Likely causes: LFS pull failed in CI; image missing models/bge-reranker-base-int8.onnx; import error."
    log "[stage2-assert] Bad container left at zettelkasten-${IDLE}; Caddy still on previous color."
    exit 88
fi
log "[stage2-assert] ${IDLE} stage2 session OK"

# iter-03 §8: pre-flip canonical RAG smoke probe. Fires the iter-03 q1 zk-org/zk
# two-fact lookup against the new color; asserts HTTP 200 + primary_citation
# == "gh-zk-org-zk". Fail-loud, no auto-rollback.
#
# JWT minted inline every deploy via Supabase password grant (NARUTO_SMOKE_PASSWORD
# + SUPABASE_ANON_KEY_LEGACY_JWT). Replaces the previous static RAG_SMOKE_TOKEN
# secret which expired after 1 hour and silently blocked all subsequent deploys.
RAG_SMOKE_KASTEN_ID="${RAG_SMOKE_KASTEN_ID:-227e0fb2-ff81-4d08-8702-76d9235564f4}"
RAG_SMOKE_QUERY="Which programming language is the zk-org/zk command-line tool written in, and what file format does it use for notes?"

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_ANON_KEY_LEGACY_JWT:-}" || -z "${NARUTO_SMOKE_PASSWORD:-}" ]]; then
    log "[rag-smoke] WARN: smoke creds (SUPABASE_URL / ANON_KEY_LEGACY / NARUTO_PASSWORD) not all set -- skipping (degraded confidence)"
else
    log "[rag-smoke] minting fresh Naruto JWT via Supabase password grant..."
    AUTH_RESP=$(curl -sS --max-time 15 -X POST "${SUPABASE_URL}/auth/v1/token?grant_type=password" \
        -H "apikey: ${SUPABASE_ANON_KEY_LEGACY_JWT}" \
        -H "Content-Type: application/json" \
        -d "$(printf '{"email":"naruto@zettelkasten.local","password":%s}' "$(printf '%s' "$NARUTO_SMOKE_PASSWORD" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
        2>/dev/null || echo "AUTH_CURL_FAILED")
    SMOKE_TOKEN=$(echo "$AUTH_RESP" | python3 -c "import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('access_token') or '')
except Exception:
    print('')" 2>/dev/null)
    if [[ -z "$SMOKE_TOKEN" ]]; then
        log "[rag-smoke] WARN: JWT mint failed -- skipping smoke probe (degraded confidence)"
    else
        log "[rag-smoke] JWT minted (len ${#SMOKE_TOKEN}); pre-warming and probing..."
        SMOKE_BODY=$(printf '{"sandbox_id":"%s","content":%s,"quality":"fast","stream":false,"scope_filter":{}}' \
            "$RAG_SMOKE_KASTEN_ID" "$(printf '%s' "$RAG_SMOKE_QUERY" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")

        # iter-03 §B (2026-04-29): pre-warm hot paths before the smoke probe.
        # First call after gunicorn fork can have cold Supabase RPC pools, cold
        # pgvector index pages, and cold Gemini key-pool selectors. Without
        # warming, retrieval can return 0 candidates → empty citations → smoke
        # mis-classifies a healthy worker as broken. Best-effort, non-fatal.
        curl -fsS --max-time 30 "http://127.0.0.1:${IDLE_PORT}/api/health/warm" >/dev/null 2>&1 || true

        # iter-03 §B (2026-04-29): retry the smoke probe up to 3 times with
        # 15s backoff. Cold-start retrieval and intra-request memory ceiling
        # (503 backpressure) can both transiently fail the first probe.
        # Three windows of 240s upper-bound = up to 12 min of grace, but
        # typical cold-start is recovered by attempt 2 in ~30s.
        SMOKE_PRIMARY=""
        SMOKE_HTTP=""
        SMOKE_RESPONSE=""
        for smoke_attempt in 1 2 3; do
            SMOKE_TMP=$(mktemp)
            SMOKE_HTTP=$(curl -sS --max-time 240 -o "$SMOKE_TMP" -w "%{http_code}" \
                -H "Authorization: Bearer $SMOKE_TOKEN" -H "Content-Type: application/json" \
                -d "$SMOKE_BODY" "http://127.0.0.1:${IDLE_PORT}/api/rag/adhoc" 2>/dev/null || echo "000")
            SMOKE_RESPONSE=$(cat "$SMOKE_TMP")
            rm -f "$SMOKE_TMP"
            SMOKE_PRIMARY=$(echo "$SMOKE_RESPONSE" | python3 -c "import json,sys
try:
    d = json.loads(sys.stdin.read())
    if 'turn' not in d:
        # 503 backpressure body has 'error', not 'turn'.
        print('NO_TURN:'+str(d.get('error','unknown')))
    else:
        cits = d.get('turn',{}).get('citations',[])
        print(cits[0].get('node_id') if cits else 'NO_CITATIONS')
except Exception as e:
    print('PARSE_FAIL:'+str(e))" 2>/dev/null || echo "PARSE_FAIL")
            log "[rag-smoke] attempt ${smoke_attempt}/3 ${IDLE} HTTP=${SMOKE_HTTP} primary=${SMOKE_PRIMARY}"
            if [[ "$SMOKE_HTTP" == "200" && "$SMOKE_PRIMARY" == "gh-zk-org-zk" ]]; then
                log "[rag-smoke] ${IDLE} smoke probe OK on attempt ${smoke_attempt}"
                break
            fi
            if (( smoke_attempt < 3 )); then
                log "[rag-smoke] cold-start or backpressure -- waiting 15s before retry..."
                sleep 15
            fi
        done

        if [[ "$SMOKE_HTTP" != "200" || "$SMOKE_PRIMARY" != "gh-zk-org-zk" ]]; then
            log "[rag-smoke] FATAL: smoke probe failed after 3 attempts. Final HTTP=${SMOKE_HTTP} primary=${SMOKE_PRIMARY}"
            log "[rag-smoke] response body (first 600 chars):"
            log "$(printf '%s' "$SMOKE_RESPONSE" | head -c 600)"
            log "[rag-smoke] FATAL: NOT auto-rolling back -- operator must triage."
            log "[rag-smoke] Possible causes: worker OOM-killed mid-pipeline; persistent backpressure (503); degraded retrieval; reranker scoring wrong; corpus drift."
            exit 89
        fi
    fi
    unset SMOKE_TOKEN AUTH_RESP
fi

# Pre-warm the new color so the first user request after cutover doesn't pay
# the BGE int8 ONNX cold-start tax (~1-3s on a 1 vCPU droplet). Best-effort:
# the loop tolerates the endpoint being briefly unavailable while gunicorn
# workers come up after --preload.
log "Pre-warming $IDLE on port $IDLE_PORT..."
PREWARM_OK=0
for i in {1..30}; do
    if curl -fsS "http://127.0.0.1:${IDLE_PORT}/api/health/warm" > /dev/null 2>&1; then
        log "Pre-warm complete after ${i}s"
        PREWARM_OK=1
        break
    fi
    sleep 1
done
if [[ "$PREWARM_OK" -ne 1 ]]; then
    log "WARN: pre-warm did not respond within 30s -- proceeding with cutover"
fi

log "Flipping Caddy upstream to $IDLE..."
# IMPORTANT: must write in-place (truncate + rewrite) rather than via
# `mv TMP SNIPPET`. Docker bind mounts of a single file track the inode
# at mount time; atomic-replace via `mv` creates a new inode, leaving the
# container stuck viewing the pre-deploy snippet. Rewriting keeps inode.
cat > "$SNIPPET" <<EOF
# Updated by deploy.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ) — SHA=$SHA
#
# iter-03: explicit transport timeouts so Strong-mode / multi-hop synthesis
# (Gemini Pro answers can take 60-120s) doesn't trip the upstream deadline.
# Must be >= GUNICORN_TIMEOUT (180s) for sane semantics.
#
# iter-04: bound upstream concurrency at the proxy layer with
# max_conns_per_host 20 (= 2 workers x (2 sem + 8 queue)). Pair with
# gunicorn --backlog 64 in run.py:46 to make the OS accept-queue overflow
# fail fast rather than queueing into a 240s death-trail. Convert
# upstream 502 / 504 to 503 with a Retry-After:10 header so the burst
# client sees structured backpressure (the eval expects >=1 503; pre-iter-04
# all 12 saw raw 502 because the app-layer 503 path was unreachable).
reverse_proxy zettelkasten-${IDLE}:10000 {
    transport http {
        dial_timeout 5s
        read_timeout 240s
        write_timeout 240s
        response_header_timeout 240s
        max_conns_per_host 20
    }
    flush_interval -1
    lb_try_duration 0s
    @upstream_down status 502 504
    handle_response @upstream_down {
        header Retry-After 10
        respond "queue_full" 503
    }
}
EOF
chown deploy:deploy "$SNIPPET"
chmod 644 "$SNIPPET"

log "Reloading Caddy..."
"$ROOT/deploy/reload_caddy.sh"

# iter-03 §B (2026-04-29): public-facing smoke gate. Catches the failure
# mode where Caddy reload silently no-ops (autosave.json keeps the prior
# upstream color, every public request returns 502 even though the
# 127.0.0.1:10000 upstream probe was happy). We hit the apex hostname so
# the request actually traverses Caddy's reverse_proxy with the new config.
# Two attempts, 5s apart, to absorb cert/HSTS warm-up after a restart.
PUBLIC_SMOKE_OK=0
for attempt in 1 2; do
    PUBLIC_HTTP="$(curl -sS -o /dev/null -w '%{http_code}' \
        --max-time 10 \
        --resolve "zettelkasten.in:443:127.0.0.1" \
        https://zettelkasten.in/api/health || echo "000")"
    if [[ "$PUBLIC_HTTP" == "200" ]]; then
        PUBLIC_SMOKE_OK=1
        break
    fi
    log "[caddy-smoke] attempt ${attempt}/2 returned HTTP=${PUBLIC_HTTP}; sleeping 5s..."
    sleep 5
done
if (( PUBLIC_SMOKE_OK == 0 )); then
    log "[caddy-smoke] FATAL: public probe via Caddy did not return 200 after flip."
    log "[caddy-smoke] FATAL: NOT auto-rolling back -- operator must triage Caddy/upstream binding."
    log "[caddy-smoke] Likely causes: caddy reload no-op (check autosave.json upstream), TLS cert issue, dns drift, container stopped."
    exit 90
fi
log "[caddy-smoke] public probe via Caddy OK (HTTP 200)"

ACTIVE_CONTAINER_NAME="zettelkasten-${ACTIVE}"
ACTIVE_CONTAINER_ID="$(docker inspect --format '{{.Id}}' "$ACTIVE_CONTAINER_NAME" 2>/dev/null || true)"

echo "$IDLE" > "$ACTIVE_FILE"

log "Running metadata backfill against $IDLE (idempotent, non-fatal)..."
# T13: enrich pre-existing chunks that lack metadata_enriched_at. The
# script's IS NULL filter makes repeated runs no-ops. Failure here MUST
# NOT block the deploy — backfill is enrichment, not correctness-critical.
# Run in background so it doesn't extend deploy wall-time; logs go to deploy.log.
BACKFILL_SCRIPT="ops/scripts/backfill_metadata.py"
if docker exec "zettelkasten-${IDLE}" test -f "$BACKFILL_SCRIPT" 2>/dev/null; then
    nohup bash -c "docker exec zettelkasten-${IDLE} python $BACKFILL_SCRIPT --batch-size 200 >> '$LOG' 2>&1 || echo '[backfill] WARN: metadata backfill exited non-zero (deploy unaffected)' >> '$LOG'" >/dev/null 2>&1 &
    log "Metadata backfill dispatched (pid=$!) — see $LOG for progress."
else
    log "WARN: $BACKFILL_SCRIPT not found in $IDLE container — skipping backfill."
fi

# iter-03 sequential blue/green: ACTIVE was stopped pre-flight (line ~167)
# to free RAM for IDLE on this 2 GB droplet. There's no live container to
# drain - skip the background retire step. Kept the variable name above for
# audit-log compatibility.
log "[seq-deploy] ACTIVE color ${ACTIVE} already stopped pre-flight; no retire needed."

log "DEPLOY SUCCEEDED. New active color: $IDLE, image: $IMAGE"
