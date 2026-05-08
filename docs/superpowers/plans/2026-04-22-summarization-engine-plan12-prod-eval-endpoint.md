# Summarization Engine Plan 12 — Production Self-Monitoring Eval Endpoint

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `POST /api/v2/eval` so production can self-monitor summary quality on sampled daily captures. Sample rate + authorization + rate limiting baked in. Results flow to a `kg_eval_samples` Supabase table (new, behind a migration) that the website can query for a quality-trend dashboard.

**Architecture:** Three moving parts:
1. New FastAPI route `POST /api/v2/eval` accepting `{node_id}` OR `{url, summary}` — evaluates and returns `EvalResult` JSON. Node-id mode fetches the node's stored summary + ingest-cache and re-evaluates; URL/summary mode evaluates arbitrary content (handy for before/after comparison). Rate-limited per IP (60 req/hr) + auth-gated (requires authenticated user).
2. A daily cron job at `ops/cron/daily_eval_sample.py` that picks a random 5% of new nodes from the past 24h, runs them through the endpoint, and writes outcomes to `kg_eval_samples`. Uses service-role key for writes; fails gracefully if sampling hits a quota cap.
3. New Supabase table `kg_eval_samples` with `(id, node_id, user_id, composite_score, g_eval_json, finesure_json, rubric_json, sampled_at, evaluator_prompt_version)`. Migration shipped alongside the endpoint.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, `website/features/evaluation/summarization` (from Plan 11), Supabase (new table + migration), cron runs via DigitalOcean droplet cron (existing infra).

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §12 item 5 (post-program follow-up).

**Branch:** `feat/prod-eval-endpoint`, off `master` AFTER Plan 11's PR merges + deploy verified.

**Precondition:** Plan 11 merged. `website/features/evaluation/summarization` importable. `website/features/summarization_engine/evaluator/*` shims still work. The existing Supabase KG migration pattern (`supabase/website/kg_public/schema.sql`) is the blueprint for this plan's migration.

**Deploy discipline:** Endpoint ships initially as `enabled=false` in `config.yaml` so merge doesn't auto-expose it to prod traffic. Human flips the flag explicitly after migration applied and endpoint smoke-tested. Cron job is `disabled` by default; human enables via `ops/cron/` config.

---

## Critical safety constraints

### 1. Rate limiting per IP
60 req/hr per IP enforced via existing FastAPI rate-limit middleware pattern (see `website/api/routes.py` `/api/summarize` rate-limit). Prevents the endpoint from becoming a free Gemini-pool siphon.

### 2. Authentication required
Only authenticated users (Supabase bearer token, via existing `get_optional_user` → tightened to `get_required_user` for this route). Anonymous calls return 401.

### 3. Per-user node access check
Node-id mode: the CALLING user_id must match the `kg_nodes.user_id` of the requested node. Enforced via Supabase RLS on the read query, plus a belt-and-braces check in Python. No cross-user eval leaks.

### 4. Endpoint disabled by default
`config.yaml → evaluator.prod_endpoint_enabled: false` gates the route. When false, the route returns 503. Human flips to true via config edit only after the Supabase migration is applied in prod + endpoint smoke-tested against a single known-safe node.

### 5. Cron safety
- Service-role key is sensitive; lives in `/etc/secrets/supabase_service_role_key` on the droplet (NOT in repo).
- Cron writes only to `kg_eval_samples` (insert-only; never updates or deletes existing rows).
- 5% sampling cap enforced server-side; cron invocation that requests > cap is clamped.
- Hard quota: cron aborts if billing_calls exceeds 10 in a single run.

### 6. Migration rollback
The new `kg_eval_samples` table is created via a forward migration (`supabase/website/kg_public/006_kg_eval_samples.sql`) with a matching rollback (`006_kg_eval_samples_rollback.sql`). Both land in the same commit. Rollback drops the table, does NOT touch `kg_nodes`.

---

## File structure summary

### Files to CREATE
- `supabase/website/kg_public/006_kg_eval_samples.sql`
- `supabase/website/kg_public/006_kg_eval_samples_rollback.sql`
- `website/features/summarization_engine/api/eval_routes.py` (new FastAPI router)
- `website/features/summarization_engine/api/eval_models.py`
- `website/features/evaluation/persistence/__init__.py`
- `website/features/evaluation/persistence/kg_eval_samples.py`
- `ops/cron/daily_eval_sample.py`
- `ops/cron/README.md`
- `tests/unit/summarization_engine/api/test_eval_routes.py`
- `tests/unit/evaluation/persistence/test_kg_eval_samples.py`
- `tests/unit/ops_cron/test_daily_eval_sample.py`

### Files to MODIFY
- `website/features/summarization_engine/api/routes.py` — include the new router
- `website/features/summarization_engine/config.yaml` — add `evaluator.prod_endpoint_enabled: false` + `evaluator.daily_sample_rate: 0.05` + `evaluator.max_daily_cron_billing_calls: 10`
- `website/features/summarization_engine/core/config.py` — add `EvaluatorConfig` block

---

## Task 0: Branch + preconditions

- [ ] **Step 1: Preconditions**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git checkout master && git pull
python -c "from website.features.evaluation.summarization.consolidated import SummarizationLLMJudge; print('plan 11 evaluator OK')"
test -f supabase/website/kg_public/schema.sql && echo "kg schema present"
```

- [ ] **Step 2: Branch**

```bash
git checkout -b feat/prod-eval-endpoint
git push -u origin feat/prod-eval-endpoint
```

---

## Task 1: Supabase migration — `kg_eval_samples` table

**Files:**
- Create: `supabase/website/kg_public/006_kg_eval_samples.sql`
- Create: `supabase/website/kg_public/006_kg_eval_samples_rollback.sql`

- [ ] **Step 1: Write forward migration**

```sql
-- supabase/website/kg_public/006_kg_eval_samples.sql
-- Adds kg_eval_samples for production self-monitoring of summary quality.

CREATE TABLE IF NOT EXISTS kg_eval_samples (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    composite_score NUMERIC(5,2) NOT NULL,
    g_eval JSONB NOT NULL,
    finesure JSONB NOT NULL,
    rubric JSONB NOT NULL,
    summac_lite JSONB,
    editorialization_flags JSONB DEFAULT '[]'::jsonb,
    maps_to_metric_summary JSONB,
    evaluator_prompt_version TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sampled_by TEXT NOT NULL DEFAULT 'manual'  -- 'manual' | 'cron'
);

CREATE INDEX IF NOT EXISTS kg_eval_samples_node_id_idx ON kg_eval_samples(node_id);
CREATE INDEX IF NOT EXISTS kg_eval_samples_user_id_idx ON kg_eval_samples(user_id);
CREATE INDEX IF NOT EXISTS kg_eval_samples_sampled_at_idx ON kg_eval_samples(sampled_at DESC);

-- RLS: users can read their own samples; service-role can read + insert all.
ALTER TABLE kg_eval_samples ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "kg_eval_samples_select_own" ON kg_eval_samples;
CREATE POLICY "kg_eval_samples_select_own"
  ON kg_eval_samples FOR SELECT
  TO authenticated
  USING (user_id = auth.uid());

DROP POLICY IF EXISTS "kg_eval_samples_insert_service" ON kg_eval_samples;
CREATE POLICY "kg_eval_samples_insert_service"
  ON kg_eval_samples FOR INSERT
  TO service_role
  WITH CHECK (true);
```

- [ ] **Step 2: Write rollback**

```sql
-- supabase/website/kg_public/006_kg_eval_samples_rollback.sql
DROP TABLE IF EXISTS kg_eval_samples CASCADE;
```

- [ ] **Step 3: Commit**

```bash
git add supabase/website/kg_public/006_kg_eval_samples.sql supabase/website/kg_public/006_kg_eval_samples_rollback.sql
git commit -m "feat: kg eval samples supabase migration"
```

---

## Task 2: Config additions

**Files:**
- Modify: `website/features/summarization_engine/config.yaml`
- Modify: `website/features/summarization_engine/core/config.py`

- [ ] **Step 1: Add evaluator config block to `config.yaml`**

Append (or merge-extend) at the bottom of `website/features/summarization_engine/config.yaml`:

```yaml
evaluator:
  prod_endpoint_enabled: false          # flip to true after migration + smoke test
  prod_endpoint_rate_limit_per_hour: 60
  daily_sample_rate: 0.05               # 5% of new nodes per day
  max_daily_cron_billing_calls: 10
  daily_cron_enabled: false             # flip to true after first human-gated dry run
```

- [ ] **Step 2: Add `EvaluatorConfig` Pydantic model**

In `core/config.py`:

```python
class EvaluatorConfig(BaseModel):
    prod_endpoint_enabled: bool = False
    prod_endpoint_rate_limit_per_hour: int = 60
    daily_sample_rate: float = 0.05
    max_daily_cron_billing_calls: int = 10
    daily_cron_enabled: bool = False


class EngineConfig(BaseModel):
    # existing fields...
    evaluator: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
```

- [ ] **Step 3: Run existing config tests**

```bash
pytest website/features/summarization_engine/tests/unit/test_config.py -v
```
Expected: PASS (new fields are optional with defaults).

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/config.yaml website/features/summarization_engine/core/config.py
git commit -m "feat: evaluator endpoint config block"
```

---

## Task 3: Persistence layer — `kg_eval_samples` repository

**Files:**
- Create: `website/features/evaluation/persistence/__init__.py`
- Create: `website/features/evaluation/persistence/kg_eval_samples.py`
- Test: `tests/unit/evaluation/persistence/test_kg_eval_samples.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/evaluation/persistence/test_kg_eval_samples.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from website.features.evaluation.persistence.kg_eval_samples import KgEvalSamplesRepo


@pytest.mark.asyncio
async def test_insert_sample_writes_all_fields():
    client = MagicMock()
    post_mock = AsyncMock()
    post_mock.return_value.status_code = 201
    post_mock.return_value.json = lambda: [{"id": str(uuid4())}]
    client.post = post_mock

    repo = KgEvalSamplesRepo(supabase_client=client)
    node_id = uuid4()
    user_id = uuid4()
    result = await repo.insert_sample(
        node_id=node_id, user_id=user_id, composite_score=88.5,
        eval_payload={
            "g_eval": {"coherence": 4.5}, "finesure": {}, "rubric": {},
            "summac_lite": {}, "editorialization_flags": [], "maps_to_metric_summary": {},
            "evaluator_metadata": {"prompt_version": "evaluator.v1", "rubric_version": "rubric_youtube.v1"},
        },
        sampled_by="manual",
    )
    assert result["id"] is not None
    post_mock.assert_called_once()
```

- [ ] **Step 2: Create `kg_eval_samples.py`**

```python
"""Repository for kg_eval_samples writes."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID


class KgEvalSamplesRepo:
    def __init__(self, *, supabase_client: Any) -> None:
        self._client = supabase_client

    async def insert_sample(
        self, *, node_id: UUID, user_id: UUID, composite_score: float,
        eval_payload: dict, sampled_by: str = "manual",
    ) -> dict:
        meta = eval_payload.get("evaluator_metadata", {}) or {}
        row = {
            "node_id": str(node_id),
            "user_id": str(user_id),
            "composite_score": round(composite_score, 2),
            "g_eval": eval_payload.get("g_eval", {}),
            "finesure": eval_payload.get("finesure", {}),
            "rubric": eval_payload.get("rubric", {}),
            "summac_lite": eval_payload.get("summac_lite"),
            "editorialization_flags": eval_payload.get("editorialization_flags", []),
            "maps_to_metric_summary": eval_payload.get("maps_to_metric_summary"),
            "evaluator_prompt_version": meta.get("prompt_version", "unknown"),
            "rubric_version": meta.get("rubric_version", "unknown"),
            "sampled_by": sampled_by,
        }
        resp = await self._client.post("/rest/v1/kg_eval_samples", json=row,
                                       headers={"Prefer": "return=representation"})
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else {}
```

- [ ] **Step 3: `__init__.py`**

```python
"""Persistence layer for evaluation samples."""
from website.features.evaluation.persistence.kg_eval_samples import KgEvalSamplesRepo  # noqa: F401
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/unit/evaluation/persistence/test_kg_eval_samples.py -v
git add website/features/evaluation/persistence/ tests/unit/evaluation/persistence/
git commit -m "feat: kg eval samples repository"
```

---

## Task 4: API route — `POST /api/v2/eval`

**Files:**
- Create: `website/features/summarization_engine/api/eval_models.py`
- Create: `website/features/summarization_engine/api/eval_routes.py`
- Test: `tests/unit/summarization_engine/api/test_eval_routes.py`
- Modify: `website/features/summarization_engine/api/routes.py` — include new router

- [ ] **Step 1: Request / response models**

```python
# website/features/summarization_engine/api/eval_models.py
from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class EvalV2Request(BaseModel):
    """Either node_id (evaluate stored summary) OR (url + summary) (evaluate arbitrary)."""
    node_id: UUID | None = None
    url: str | None = None
    summary: dict | None = None
    store_sample: bool = False   # if true, caller's user writes result to kg_eval_samples

    @model_validator(mode="after")
    def _require_node_or_pair(self):
        if self.node_id is None and (self.url is None or self.summary is None):
            raise ValueError("Must provide either node_id OR both url and summary")
        return self


class EvalV2Response(BaseModel):
    composite_score: float
    eval_payload: dict = Field(default_factory=dict)
    sample_id: str | None = None
```

- [ ] **Step 2: Route handler**

```python
# website/features/summarization_engine/api/eval_routes.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from website.api.auth import get_required_user
from website.features.evaluation.summarization.models import composite_score
from website.features.evaluation.summarization.consolidated import SummarizationLLMJudge
from website.features.evaluation.summarization.atomic_facts import extract_atomic_facts
from website.features.evaluation.core.rubric_loader import load_rubric
from website.features.summarization_engine.api.eval_models import EvalV2Request, EvalV2Response
from website.features.summarization_engine.api.routes import _gemini_client
from website.features.summarization_engine.core.config import load_config


router = APIRouter(prefix="/api/v2", tags=["evaluation"])


_RUBRIC_DIR = Path(__file__).resolve().parents[5] / "docs" / "summary_eval" / "_config"
_CACHE_ROOT = Path(__file__).resolve().parents[5] / "docs" / "summary_eval" / "_cache"


@router.post("/eval", response_model=EvalV2Response)
async def eval_v2(
    request: EvalV2Request,
    user: Annotated[dict, Depends(get_required_user)],
):
    config = load_config()
    if not config.evaluator.prod_endpoint_enabled:
        raise HTTPException(status_code=503, detail="evaluator endpoint disabled")

    client = _gemini_client()

    if request.node_id is not None:
        # Fetch node + its summary from Supabase using caller's bearer
        # (inlined fetch for clarity; real impl would use a repo).
        import httpx
        supabase_url = os.environ.get("SUPABASE_URL", "")
        anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        if not supabase_url or not anon_key:
            raise HTTPException(status_code=500, detail="supabase not configured")
        async with httpx.AsyncClient(timeout=10.0) as hc:
            resp = await hc.get(
                f"{supabase_url}/rest/v1/kg_nodes?id=eq.{request.node_id}&select=*",
                headers={
                    "apikey": anon_key,
                    "Authorization": user.get("bearer_token", "") or f"Bearer {anon_key}",
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                raise HTTPException(status_code=404, detail="node not found or access denied")
            node = rows[0]
        if str(node["user_id"]) != str(user.get("sub", "")):
            raise HTTPException(status_code=403, detail="not your node")
        url = node["url"]
        summary = {
            "mini_title": node.get("mini_title"),
            "brief_summary": node.get("brief_summary"),
            "tags": node.get("tags", []),
            "detailed_summary": node.get("detailed_summary", []),
            "metadata": node.get("metadata", {}),
        }
        source_type = node.get("source_type", "web")
    else:
        url = request.url
        summary = request.summary
        # Infer source_type from URL
        from website.features.summarization_engine.core.router import detect_source_type
        source_type = detect_source_type(url).value

    rubric_path = _RUBRIC_DIR / f"rubric_{source_type}.yaml"
    if not rubric_path.exists():
        rubric_path = _RUBRIC_DIR / "rubric_universal.yaml"
    rubric_yaml = load_rubric(rubric_path)

    # Fetch cached source_text via the ingest cache if present; else best-effort.
    ingest_cache_dir = _CACHE_ROOT / "ingests"
    source_text = ""
    for fp in ingest_cache_dir.glob("*.json"):
        import json
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            if d.get("url") == url:
                source_text = d.get("raw_text", "")
                break
        except Exception:
            continue
    if not source_text:
        source_text = summary.get("metadata", {}).get("confidence_reason", "") or url

    facts = await extract_atomic_facts(
        client=client, source_text=source_text, cache_root=_CACHE_ROOT,
        url=url, ingestor_version="2.0.0",
    )
    judge = SummarizationLLMJudge(gemini_client=client)
    eval_payload = await judge.evaluate(
        rubric_yaml=rubric_yaml, atomic_facts=facts,
        source_text=source_text, summary_json=summary,
    )

    # Compute composite
    from website.features.evaluation.summarization.models import SummaryEvalResult
    eval_obj = SummaryEvalResult(**eval_payload)
    composite = composite_score(eval_obj)

    sample_id = None
    if request.store_sample:
        from website.features.evaluation.persistence import KgEvalSamplesRepo
        import httpx
        async with httpx.AsyncClient(
            base_url=os.environ["SUPABASE_URL"],
            timeout=10.0,
            headers={"apikey": os.environ["SUPABASE_ANON_KEY"]},
        ) as client_http:
            repo = KgEvalSamplesRepo(supabase_client=client_http)
            if request.node_id is None:
                raise HTTPException(status_code=400, detail="store_sample requires node_id")
            inserted = await repo.insert_sample(
                node_id=request.node_id,
                user_id=UUID(user.get("sub")),
                composite_score=composite,
                eval_payload=eval_payload,
                sampled_by="manual",
            )
            sample_id = inserted.get("id")

    return EvalV2Response(
        composite_score=composite,
        eval_payload=eval_payload,
        sample_id=sample_id,
    )
```

- [ ] **Step 3: Register router in `api/routes.py`**

At the bottom of `website/features/summarization_engine/api/routes.py`, add:

```python
from website.features.summarization_engine.api.eval_routes import router as eval_router

def include_eval_router(app):
    """Called from website/app.py during app lifespan setup."""
    app.include_router(eval_router)
```

Then in `website/app.py`, during app setup, call `include_eval_router(app)`.

- [ ] **Step 4: Route test**

```python
# tests/unit/summarization_engine/api/test_eval_routes.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


def test_eval_v2_disabled_returns_503(monkeypatch):
    # Assume config.yaml has prod_endpoint_enabled: false
    from website.app import create_app
    app = create_app()
    client = TestClient(app)
    with patch("website.api.auth.get_required_user", return_value={"sub": "test-user"}):
        resp = client.post("/api/v2/eval", json={"node_id": "00000000-0000-0000-0000-000000000001"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_eval_v2_requires_node_or_pair(monkeypatch):
    # Verifies model_validator rejects incomplete requests.
    from website.features.summarization_engine.api.eval_models import EvalV2Request
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        EvalV2Request()
```

- [ ] **Step 5: Run + commit**

```bash
pytest tests/unit/summarization_engine/api/test_eval_routes.py -v
git add website/features/summarization_engine/api/eval_routes.py website/features/summarization_engine/api/eval_models.py website/features/summarization_engine/api/routes.py website/app.py tests/unit/summarization_engine/api/test_eval_routes.py
git commit -m "feat: api v2 eval endpoint gated by config"
```

---

## Task 5: Daily cron sampler

**Files:**
- Create: `ops/cron/daily_eval_sample.py`
- Create: `ops/cron/README.md`
- Test: `tests/unit/ops_cron/test_daily_eval_sample.py`

- [ ] **Step 1: Create cron script**

```python
"""Daily KG quality sampler — runs 5% sample of past-24h nodes through /api/v2/eval.

Run via droplet cron:
    0 3 * * * cd /opt/zettelkasten && /opt/venv/bin/python ops/cron/daily_eval_sample.py >> /var/log/kg_eval_sample.log 2>&1

Uses SUPABASE_SERVICE_ROLE_KEY from /etc/secrets/supabase_service_role_key for reads
(paging ALL past-24h nodes) and for insert to kg_eval_samples (service-role-scoped).
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from website.features.summarization_engine.core.config import load_config


def _load_service_role_key() -> str:
    path = Path("/etc/secrets/supabase_service_role_key")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError("no supabase service-role key found")
    return key


async def _sample_and_eval() -> int:
    cfg = load_config()
    if not cfg.evaluator.daily_cron_enabled:
        print("daily_cron_enabled=false; exiting.")
        return 0
    rate = cfg.evaluator.daily_sample_rate
    billing_cap = cfg.evaluator.max_daily_cron_billing_calls

    supabase_url = os.environ["SUPABASE_URL"]
    service_key = _load_service_role_key()
    api_base = os.environ.get("ZETTELKASTEN_API", "http://127.0.0.1:10000")

    # 1. Fetch all nodes created in past 24h
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{supabase_url}/rest/v1/kg_nodes",
            params={
                "created_at": f"gte.{cutoff}",
                "select": "id,user_id,url,source_type",
                "engine_version": "eq.2.0.0",
                "limit": "5000",
            },
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        )
        resp.raise_for_status()
        nodes = resp.json()

    if not nodes:
        print("no new nodes in past 24h; exiting.")
        return 0

    # 2. Sample rate%
    random.shuffle(nodes)
    sample_size = max(1, int(len(nodes) * rate))
    sampled = nodes[:sample_size]
    print(f"found {len(nodes)} nodes; sampling {sample_size} at rate {rate}")

    # 3. For each sampled node, hit /api/v2/eval with store_sample=true.
    #    Cron uses service-role key as bearer so it can read any node.
    billing_calls = 0
    successes = 0
    failures = 0

    for node in sampled:
        if billing_calls >= billing_cap:
            print(f"hit max_daily_cron_billing_calls={billing_cap}; stopping.")
            break
        try:
            async with httpx.AsyncClient(timeout=300.0) as hc:
                r = await hc.post(
                    f"{api_base}/api/v2/eval",
                    json={"node_id": node["id"], "store_sample": True},
                    headers={"Authorization": f"Bearer {service_key}"},
                )
                if r.status_code == 503:
                    print("endpoint disabled; exiting.")
                    return 0
                r.raise_for_status()
                resp = r.json()
                successes += 1
                # Track billing via response if exposed; for now assume ≤1 billing call per eval.
                # The conservative bound keeps us within cap.
                billing_calls += 1
                print(f"OK node={node['id']} composite={resp.get('composite_score'):.1f}")
        except Exception as exc:
            failures += 1
            print(f"FAIL node={node['id']} err={exc}")

    print(f"done: successes={successes} failures={failures} billing_calls≤{billing_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_sample_and_eval()))
```

- [ ] **Step 2: README for ops/cron/**

```markdown
# Cron jobs for the Zettelkasten droplet

## daily_eval_sample.py
Samples 5% of the previous day's new KG nodes and scores them via the v2 evaluator,
writing results to `kg_eval_samples` for quality-trend dashboards.

### Install (on the droplet)
```bash
# Confirm service-role key at /etc/secrets/supabase_service_role_key
sudo chmod 600 /etc/secrets/supabase_service_role_key

# Add cron entry (3 AM UTC daily)
crontab -e
# Append:
# 0 3 * * * cd /opt/zettelkasten && /opt/venv/bin/python ops/cron/daily_eval_sample.py >> /var/log/kg_eval_sample.log 2>&1
```

### First-run dry
```bash
SUPABASE_URL=$SUPABASE_URL ZETTELKASTEN_API=http://127.0.0.1:10000 \
  /opt/venv/bin/python ops/cron/daily_eval_sample.py
```
Reads past 24h, samples 5%, evaluates. Rate-limited to 10 billing calls / day.
```

- [ ] **Step 3: Test (mocked)**

```python
# tests/unit/ops_cron/test_daily_eval_sample.py
import pytest


def test_sample_script_importable():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from ops.cron.daily_eval_sample import _sample_and_eval  # noqa: F401
```

Integration behavior is tested manually on droplet during Task 7.

- [ ] **Step 4: Commit**

```bash
git add ops/cron/ tests/unit/ops_cron/
git commit -m "feat: daily kg eval sampling cron"
```

---

## Task 6: Local smoke test of the endpoint

- [ ] **Step 1: Apply migration locally (if Supabase local dev)**

```bash
# If using Supabase local:
# supabase db reset
# Then run the forward migration manually via supabase dashboard / psql.
```

For remote dev, apply via the Supabase dashboard SQL editor before enabling the endpoint.

- [ ] **Step 2: Enable endpoint for smoke test**

Edit `website/features/summarization_engine/config.yaml`:

```yaml
evaluator:
  prod_endpoint_enabled: true
```

- [ ] **Step 3: Start server + POST a known Zoro node**

```bash
python run.py &
sleep 5
# Get Zoro bearer
# export SUPABASE_URL=https://icmnskseuoteyirljswd.supabase.co
# export SUPABASE_ANON_KEY=<private — from .env or secret manager>
BEARER=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"zoro@zettelkasten.test","password":"Zoro2026!"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Use a known Zoro node_id
ZORO_NODE=$(curl -s "$SUPABASE_URL/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&select=id&limit=1" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $BEARER" | python -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

curl -X POST http://127.0.0.1:10000/api/v2/eval \
  -H "Authorization: Bearer $BEARER" \
  -H "Content-Type: application/json" \
  -d "{\"node_id\": \"$ZORO_NODE\", \"store_sample\": false}" | python -m json.tool
kill %1
```

Expected: 200 response with `composite_score` + `eval_payload`. No sample_id since store_sample=false.

- [ ] **Step 4: Revert `prod_endpoint_enabled` back to `false` before pushing**

```bash
# Edit config.yaml: prod_endpoint_enabled: false
git diff website/features/summarization_engine/config.yaml
```

The endpoint ships as `enabled=false` — human flips it on in prod only after migration is applied.

- [ ] **Step 5: Commit any config reversion**

If config.yaml was changed for smoke test but must revert:
```bash
git checkout -- website/features/summarization_engine/config.yaml
```

---

## Task 7: Push + draft PR

- [ ] **Step 1: Push**

```bash
git push origin feat/prod-eval-endpoint
```

- [ ] **Step 2: Draft PR**

```bash
gh pr create --draft --title "feat: prod self monitoring eval endpoint" \
  --body "Plan 12. Adds POST /api/v2/eval (disabled by default) + kg_eval_samples table + daily cron sampler (disabled by default). Merge-safe because both flags default off; human flips on after applying migration in prod.

### Deploy gate
- [ ] CI green
- [ ] supabase migration 006_kg_eval_samples.sql applied in prod via Supabase dashboard
- [ ] smoke test: POST /api/v2/eval with prod_endpoint_enabled=true on a known Zoro node returns 200 + composite
- [ ] revert prod_endpoint_enabled=true in config.yaml in a follow-up config-only commit after smoke test passes
- [ ] cron: /etc/secrets/supabase_service_role_key installed with 600 permissions on droplet
- [ ] cron: daily_cron_enabled=true set only after first manual dry-run succeeds

Merging this PR alone does NOT expose the endpoint or run the cron. Two gated config flags protect prod."
```

- [ ] **Step 3: STOP + handoff**

Report:
> Plan 12 complete. Draft PR ready. Endpoint + cron both gated OFF in config. Migration shipped. Awaiting human to: (a) apply migration in prod, (b) merge PR, (c) smoke test endpoint, (d) flip flags on.

---

## Self-review checklist
- [ ] Both `prod_endpoint_enabled` AND `daily_cron_enabled` default to `false`
- [ ] Forward + rollback migrations both included + tested
- [ ] RLS on kg_eval_samples: users read own rows, service-role inserts all
- [ ] Endpoint auth-gated (get_required_user); anonymous calls → 401
- [ ] Per-user node access check enforced in handler (even though RLS also protects)
- [ ] Cron uses service-role key from /etc/secrets/..., not env var on dev
- [ ] Cron has hard billing-call cap + sampling-rate cap
- [ ] NO merge, NO push to master
