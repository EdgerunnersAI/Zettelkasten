# syntax=docker/dockerfile:1.7

# --- Stage 1: builder -------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY ops/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r /tmp/requirements.txt

RUN python -m compileall -q /opt/venv

# Fetch the int8 BGE reranker from a private HF Hub repo at build time.
# Migrated out of Git LFS (LFS bandwidth quota became a deploy blocker).
# HF_TOKEN is mounted as a build secret — never lands in any image layer.
# Output dir is consumed by the runtime stage via COPY --from=builder below.
ARG HF_RERANKER_REPO=Zettelkasten/bge-reranker
ARG HF_RERANKER_FILENAME=bge-reranker-base-int8.onnx
ENV HF_HUB_DISABLE_TELEMETRY=1
RUN --mount=type=secret,id=hf_token,required=true \
    REPO="${HF_RERANKER_REPO}" FNAME="${HF_RERANKER_FILENAME}" \
    HF_TOKEN="$(cat /run/secrets/hf_token)" \
    python -c "import os; from huggingface_hub import hf_hub_download; \
hf_hub_download(repo_id=os.environ['REPO'], filename=os.environ['FNAME'], \
local_dir='/tmp/hf-models', token=os.environ['HF_TOKEN'])" \
    && test -s "/tmp/hf-models/${HF_RERANKER_FILENAME}"

# --- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="zettelkasten-kg-website" \
      org.opencontainers.image.description="Zettelkasten Knowledge Graph Web UI" \
      org.opencontainers.image.source="https://github.com/chintanmehta21/Zettelkasten_KG" \
      org.opencontainers.image.licenses="MIT"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

COPY --chown=appuser:appuser website/ website/
COPY --chown=appuser:appuser run.py ./
COPY --chown=appuser:appuser pyproject.toml ./

# D-1: ship the migration runner + SQL files so deploy.sh can run
# `python ops/scripts/apply_migrations.py` from inside the new image
# before the blue/green flip. Also includes ops/scripts/backfill_metadata.py
# and the existing post-cutover backfill scripts referenced by deploy.sh.
#
# Phase 8.5 v2-purge (2026-05-11): deploy runs `apply_migrations.py --v2`
# against supabase/website/_v2/*.sql + expected_schema.json. The legacy
# kg_public/migrations/ tree was archived to migrations_archived_2026-05-11/
# and is no longer copied into the image — it's unrunnable (every file
# references dropped public.kg_* tables) and not referenced by any runtime
# script with --v2.
COPY --chown=appuser:appuser ops/scripts/ ops/scripts/
COPY --chown=appuser:appuser supabase/website/_v2/ \
     supabase/website/_v2/

# iter-03: bake reranker model artifacts (int8 BGE + FlashRank stage-1) into
# the image so /app/models is read-only at runtime. Eliminates the host-state-
# drift class of bug that defeated iter-03-stale (model file silently missing
# on the droplet -> lazy fp32 fallback ate 1 GB RSS per worker).
# 2026-05-03: bge-reranker-base-int8.onnx now comes from HF Hub (private),
# fetched in the builder stage. Everything else (tokenizer, calibration,
# FlashRank stage-1) still ships from the repo's models/ dir.
COPY --chown=appuser:appuser models/ /app/models/
COPY --from=builder --chown=appuser:appuser \
     /tmp/hf-models/bge-reranker-base-int8.onnx /app/models/bge-reranker-base-int8.onnx

RUN mkdir -p /app/kg_output /app/bot_data \
    && chown -R appuser:appuser /app/kg_output /app/bot_data

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

EXPOSE 10000

USER appuser

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl --silent --show-error --fail --max-time 2 http://127.0.0.1:10000/api/health || exit 1

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "run.py"]
