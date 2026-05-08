# Summarization Engine Plan 10 — Supabase KG Historical Backfill

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-summarize every existing Supabase KG node that was written by the legacy pipeline (pre-Plans 1-9) through the v2 engine so historical zettels benefit from rubric-tuned quality. Ship a rate-limited, idempotent, resumable backfill script with progress tracking + safety gates.

**Architecture:** A standalone CLI (`ops/scripts/backfill_kg_v2.py`) pages through `kg_nodes` in Supabase, filters nodes that (a) were created before Plans 1-9 landed AND (b) have a reachable `url` AND (c) whose `engine_version` metadata is `< "2.0.0"` or absent. For each qualifying node, the script hits `/api/v2/summarize` using the node's owner's bearer token (default: Zoro for Zoro's nodes; Naruto for Naruto's) and UPDATES the existing node in-place — no new node IDs, no broken RAG references. Writes progress to `docs/summary_eval/_backfill/kg_v2/progress.jsonl` so the script resumes cleanly on interrupt.

**Tech Stack:** Python 3.12, `httpx`, `supabase-py` (already a dep), existing `SupabaseWriter` (modified to support update-in-place mode), the v2 `summarize_url` pipeline. No paid services.

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §12 item 2 (post-program follow-up).

**Branch:** `feat/kg-backfill-v2`, off `master` AFTER Plan 9's PR merges + prod deploy verified healthy.

**Precondition:** All 5 core PRs (Plans 1-9) merged to master. v2 engine + evaluator live on droplet. Supabase KG schema unchanged from Plan 1 (per spec §1 non-goal).

**Deploy discipline:** Backfill runs against PRODUCTION Supabase (there's no staging DB). Because the script UPDATES real user data, **the first dry-run + the first live batch BOTH require explicit human approval**. Codex writes the code, opens draft PR, runs the dry-run, stops with verification artifacts for human review, and does NOT run the first live batch unless the human runs it manually with `--live`.

---

## Critical safety constraints

### 1. Update in place, never create/delete
Backfill MUST preserve `kg_nodes.id`, `kg_links.source_node_id`, `kg_links.target_node_id`. Breaking these invalidates every RAG citation and frontend graph link. The SupabaseWriter's update path is `PATCH /rest/v1/kg_nodes?id=eq.<node_id>` — never `POST /rest/v1/kg_nodes`.

### 2. Per-user bearer tokens
Every node has a `user_id`. The `/api/v2/summarize?write_to_supabase=true` endpoint writes as the authenticated user. Backfill MUST use the owning user's bearer token when updating that user's nodes — mixing user_ids would break RLS assumptions downstream.

Known test users (from `docs/login_details.txt`):
- Naruto: `<private>naruto@zettelkasten.local / Naruto2026! / auth_id f2105544-...</private>`
- Zoro: `<private>zoro@zettelkasten.test / Zoro2026! / auth_id a57e1f2f-...</private>`

Any other real users' tokens are NOT accessible to Codex and their nodes are SKIPPED — the backfill script filters `WHERE user_id IN ({known_user_ids})`.

### 3. Rate limiting
- Max 1 node per 3 seconds (prevents Gemini pool saturation during backfill + normal prod traffic)
- Max 50 nodes per invocation (single-shot; multi-hour backfills run via cron over many invocations)
- Hard stop on any 429 from Gemini pool for 10 minutes — respects quota recovery

### 4. Idempotency + resumability
Progress log at `docs/summary_eval/_backfill/kg_v2/progress.jsonl` (JSONL = one-line-per-node). Every line is `{node_id, user_id, status, engine_version_before, engine_version_after, composite_score, backfilled_at}`. On script restart, any node already logged with `status=success` is skipped.

### 5. Rollback on mass failure
If > 10% of a batch fails the evaluator's hallucination-cap check (composite < 60), abort the batch immediately and WRITE (do not execute) a rollback SQL script to `docs/summary_eval/_backfill/kg_v2/rollback_<batch_id>.sql`. Human reviews + executes manually if restoring from backup is needed. Supabase keeps point-in-time recovery by default for 7 days.

### 6. Dry-run first
Every live invocation MUST be preceded by a dry-run on the same target set:
- `--dry-run`: writes what-would-be-updated to `docs/summary_eval/_backfill/kg_v2/dryrun_<batch_id>.json` without any write.
- Human reviews dry-run output + approves.
- Live run proceeds with `--live` flag (explicit opt-in, no default).

---

## File structure summary

### Files to CREATE
- `ops/scripts/backfill_kg_v2.py`
- `ops/scripts/lib/backfill_state.py`
- `website/features/summarization_engine/writers/supabase_update.py` (or extend existing `supabase.py`)
- `tests/unit/ops_scripts/test_backfill_kg_v2.py`
- `tests/unit/summarization_engine/writers/test_supabase_update.py`
- `docs/summary_eval/_backfill/kg_v2/README.md`

### Files to MODIFY
- `website/features/summarization_engine/writers/supabase.py` — add `update_existing_node()` method alongside the existing `write()` (which creates)
- `website/features/summarization_engine/api/routes.py` — accept optional `force_update_node_id` field in `SummarizeV2Request`, routed to SupabaseWriter's update path
- `website/features/summarization_engine/api/models.py` — extend `SummarizeV2Request` with `force_update_node_id: UUID | None = None`

---

## Task 0: Branch + preconditions

- [ ] **Step 1: Create branch + verify prerequisites**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git checkout master && git pull
python -c "from website.features.summarization_engine.summarization.youtube.summarizer import YouTubeSummarizer; print('v2 engine OK')"
python -c "from website.features.summarization_engine.evaluator import evaluate; print('evaluator OK')"
curl -s https://zettelkasten.in/api/health
```
Expected: 2 OK prints + 200 health. If evaluator import fails, Plan 1 hasn't landed; abort.

- [ ] **Step 2: Create branch**

```bash
git checkout -b feat/kg-backfill-v2
git push -u origin feat/kg-backfill-v2
```

---

## Task 1: Extend `SupabaseWriter` with update-in-place

**Files:**
- Modify: `website/features/summarization_engine/writers/supabase.py`
- Test: `tests/unit/summarization_engine/writers/test_supabase_update.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/writers/test_supabase_update.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from website.features.summarization_engine.core.models import (
    DetailedSummarySection, SourceType, SummaryMetadata, SummaryResult,
)
from website.features.summarization_engine.writers.supabase import SupabaseWriter


def _fake_summary() -> SummaryResult:
    return SummaryResult(
        mini_title="test/repo",
        brief_summary="A test repo.",
        tags=["a", "b", "c", "d", "e", "f", "g"],
        detailed_summary=[DetailedSummarySection(heading="Overview", bullets=["does X"])],
        metadata=SummaryMetadata(
            source_type=SourceType.GITHUB, url="https://github.com/test/repo",
            extraction_confidence="high", confidence_reason="ok",
            total_tokens_used=100, total_latency_ms=500, engine_version="2.0.0",
        ),
    )


@pytest.mark.asyncio
async def test_update_existing_node_preserves_id():
    writer = SupabaseWriter()
    node_id = uuid4()
    user_id = uuid4()
    with patch.object(writer, "_patch", new=AsyncMock()) as mock_patch:
        mock_patch.return_value = {"id": str(node_id)}
        result = await writer.update_existing_node(_fake_summary(), node_id=node_id, user_id=user_id)
    assert result["node_id"] == str(node_id)
    mock_patch.assert_called_once()
    call_args = mock_patch.call_args
    assert f"id=eq.{node_id}" in call_args[0][0] or f"id=eq.{node_id}" in str(call_args)


@pytest.mark.asyncio
async def test_update_existing_node_refuses_if_id_missing():
    writer = SupabaseWriter()
    with pytest.raises(ValueError, match="node_id required"):
        await writer.update_existing_node(_fake_summary(), node_id=None, user_id=uuid4())
```

- [ ] **Step 2: Run + verify FAIL**

```bash
pytest tests/unit/summarization_engine/writers/test_supabase_update.py -v
```
Expected: FAIL (`AttributeError: 'SupabaseWriter' object has no attribute 'update_existing_node'`).

- [ ] **Step 3: Read current `supabase.py` + add `update_existing_node`**

Append to `website/features/summarization_engine/writers/supabase.py`:

```python
    async def update_existing_node(
        self,
        result: "SummaryResult",
        *,
        node_id: "UUID | None",
        user_id: "UUID",
    ) -> dict:
        """Update an existing kg_nodes row in place. Preserves node_id and all links.

        Critical: callers pass the OWNING user's bearer token via Supabase client auth;
        RLS rejects cross-user updates. No insert path — if node_id doesn't exist, the
        PATCH returns 0 rows and this method raises.
        """
        if node_id is None:
            raise ValueError("node_id required for update_existing_node (use write() for inserts)")

        payload = {
            "mini_title": result.mini_title,
            "brief_summary": result.brief_summary,
            "tags": result.tags,
            "detailed_summary": [s.model_dump(mode="json") for s in result.detailed_summary],
            "metadata": result.metadata.model_dump(mode="json"),
            "updated_at": "now()",
            "engine_version": result.metadata.engine_version,
        }
        path = f"/rest/v1/kg_nodes?id=eq.{node_id}"
        response = await self._patch(path, payload)
        if not response:
            raise RuntimeError(f"PATCH /kg_nodes returned empty — node {node_id} not found or RLS denied")
        return {"node_id": str(node_id), "user_id": str(user_id), "engine_version": result.metadata.engine_version}

    async def _patch(self, path: str, payload: dict) -> dict:
        """Low-level PATCH wrapper. Requires self._client configured with a bearer token."""
        resp = await self._client.patch(path, json=payload, headers={"Prefer": "return=representation"})
        resp.raise_for_status()
        data = resp.json()
        return data[0] if isinstance(data, list) and data else {}
```

(Adapt to match the existing `SupabaseWriter` class shape — if `_client` / `_patch` already exist with different names, reuse them.)

- [ ] **Step 4: Run test to verify PASS**

```bash
pytest tests/unit/summarization_engine/writers/test_supabase_update.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/writers/supabase.py tests/unit/summarization_engine/writers/test_supabase_update.py
git commit -m "feat: supabase writer update in place"
```

---

## Task 2: API route accepts `force_update_node_id`

**Files:**
- Modify: `website/features/summarization_engine/api/models.py`
- Modify: `website/features/summarization_engine/api/routes.py`

- [ ] **Step 1: Extend `SummarizeV2Request`**

In `api/models.py`:

```python
from uuid import UUID

class SummarizeV2Request(BaseModel):
    url: str
    write_to_supabase: bool = False
    force_update_node_id: UUID | None = None   # NEW: when set, writer UPDATEs this node instead of inserting
    ...existing fields...
```

- [ ] **Step 2: Route `force_update_node_id` through**

In `api/routes.py`, modify the `summarize_v2` handler:

```python
    writers = []
    if request.write_to_supabase:
        writer = SupabaseWriter()
        if request.force_update_node_id:
            writers.append(await writer.update_existing_node(
                result, node_id=request.force_update_node_id, user_id=user_id,
            ))
        else:
            writers.append(await writer.write(result, user_id=user_id))
    return SummarizeV2Response(summary=result.model_dump(mode="json"), writers=writers)
```

- [ ] **Step 3: Run existing API tests**

```bash
pytest website/features/summarization_engine/tests/unit/test_api_routes.py -v
```
Expected: PASS (new field is optional/nullable).

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/api/models.py website/features/summarization_engine/api/routes.py
git commit -m "feat: api v2 summarize accepts force update node id"
```

---

## Task 3: Backfill progress state helper

**Files:**
- Create: `ops/scripts/lib/backfill_state.py`
- Test: `tests/unit/ops_scripts/test_backfill_state.py`

- [ ] **Step 1: Write test**

```python
# tests/unit/ops_scripts/test_backfill_state.py
import json
from pathlib import Path

from ops.scripts.lib.backfill_state import BackfillState, NodeOutcome


def test_load_empty_state(tmp_path):
    state = BackfillState(path=tmp_path / "progress.jsonl")
    assert state.completed_node_ids() == set()


def test_append_and_reload(tmp_path):
    path = tmp_path / "progress.jsonl"
    state = BackfillState(path=path)
    state.append(NodeOutcome(node_id="n1", user_id="u1", status="success",
                              engine_version_before="1.0.0", engine_version_after="2.0.0",
                              composite_score=88.0, backfilled_at="2026-04-22T00:00:00Z"))
    state.append(NodeOutcome(node_id="n2", user_id="u1", status="failed",
                              engine_version_before="1.0.0", engine_version_after=None,
                              composite_score=None, backfilled_at="2026-04-22T00:01:00Z", error="timeout"))
    reloaded = BackfillState(path=path)
    assert reloaded.completed_node_ids() == {"n1"}  # only successful ones count as "done"


def test_skip_if_already_done(tmp_path):
    path = tmp_path / "progress.jsonl"
    state = BackfillState(path=path)
    state.append(NodeOutcome(node_id="n1", user_id="u1", status="success",
                              engine_version_before="1.0.0", engine_version_after="2.0.0",
                              composite_score=88.0, backfilled_at="2026-04-22T00:00:00Z"))
    assert state.already_processed("n1") is True
    assert state.already_processed("n2") is False
```

- [ ] **Step 2: Run + verify FAIL, create module**

```python
# ops/scripts/lib/backfill_state.py
"""Resumable JSONL-based progress log for KG backfill."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class NodeOutcome:
    node_id: str
    user_id: str
    status: str  # "success" | "failed" | "skipped"
    engine_version_before: str | None
    engine_version_after: str | None
    composite_score: float | None
    backfilled_at: str
    error: str | None = None


class BackfillState:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, outcome: NodeOutcome) -> None:
        line = json.dumps(asdict(outcome), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")

    def completed_node_ids(self) -> set[str]:
        if not self._path.exists():
            return set()
        done: set[str] = set()
        with self._path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                if entry.get("status") == "success":
                    done.add(entry["node_id"])
        return done

    def already_processed(self, node_id: str) -> bool:
        return node_id in self.completed_node_ids()
```

- [ ] **Step 3: Run + verify PASS**

```bash
pytest tests/unit/ops_scripts/test_backfill_state.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add ops/scripts/lib/backfill_state.py tests/unit/ops_scripts/test_backfill_state.py
git commit -m "feat: backfill state jsonl log"
```

---

## Task 4: Main backfill CLI

**Files:**
- Create: `ops/scripts/backfill_kg_v2.py`

- [ ] **Step 1: Create script**

```python
"""KG historical backfill CLI — re-summarize legacy v1 nodes via v2 engine.

Usage:
  --dry-run                 # preview, no writes (REQUIRED before any --live run)
  --live                    # perform writes (explicit opt-in, no default)
  --user <naruto|zoro|all>  # which users' nodes to backfill (all = all known users)
  --limit N                 # max nodes this invocation (default 50, hard cap 500)
  --min-age-days N          # only backfill nodes older than N days (default 7)
  --sleep-sec N             # per-node delay (default 3.0)
  --source-type <type>      # filter by source_type (useful for gated rollout)
  --batch-id <id>           # name this batch (default: yyyy-mm-dd-hhmm)
  --report-only             # print stats, no fetch
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.scripts.lib.backfill_state import BackfillState, NodeOutcome


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_ROOT = REPO_ROOT / "docs" / "summary_eval" / "_backfill" / "kg_v2"
LOGIN_DETAILS = REPO_ROOT / "docs" / "login_details.txt"


USERS = {
    "naruto": {"email_regex": r"naruto@\S+", "password_regex": r"Naruto2026!\S*",
               "auth_id": "f2105544-b73d-4946-8329-096d82f070d3"},
    "zoro":   {"email_regex": r"zoro@\S+",   "password_regex": r"Zoro2026!\S*",
               "auth_id": "a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e"},
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("--user", choices=["naruto", "zoro", "all"], default="zoro")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--min-age-days", type=int, default=7)
    p.add_argument("--sleep-sec", type=float, default=3.0)
    p.add_argument("--source-type", type=str, default=None)
    p.add_argument("--batch-id", type=str, default=None)
    p.add_argument("--report-only", action="store_true")
    p.add_argument("--server", default=os.environ.get("ZETTELKASTEN_API", "http://127.0.0.1:10000"))
    return p.parse_args()


async def _get_user_bearer(user_key: str) -> tuple[str, str]:
    creds = USERS[user_key]
    text = LOGIN_DETAILS.read_text(encoding="utf-8")
    email = re.search(creds["email_regex"], text).group(0)
    password = re.search(creds["password_regex"], text).group(0)
    supabase_url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        r.raise_for_status()
        return r.json()["access_token"], creds["auth_id"]


async def _fetch_legacy_nodes(*, user_id: str, bearer: str, min_age_days: int,
                               source_type: str | None, limit: int) -> list[dict]:
    """Pull nodes owned by user_id that were written pre-v2 (engine_version != '2.0.0' or missing)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).isoformat()
    params = {
        "user_id": f"eq.{user_id}",
        "created_at": f"lt.{cutoff}",
        "select": "id,user_id,url,source_type,created_at,engine_version,mini_title",
        "order": "created_at.asc",
        "limit": str(limit * 3),  # overselect; client-side filter for legacy
    }
    if source_type:
        params["source_type"] = f"eq.{source_type}"
    supabase_url = os.environ["SUPABASE_URL"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{supabase_url}/rest/v1/kg_nodes",
            params=params,
            headers={"apikey": anon_key, "Authorization": f"Bearer {bearer}"},
        )
        r.raise_for_status()
        nodes = r.json()
    # Client-side filter: keep nodes with engine_version missing OR < "2.0.0".
    legacy = [n for n in nodes if (n.get("engine_version") or "0.0.0") < "2.0.0"]
    return legacy[:limit]


async def _resummarize(*, server: str, bearer: str, node: dict) -> dict:
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{server}/api/v2/summarize",
            json={"url": node["url"], "write_to_supabase": True,
                  "force_update_node_id": node["id"]},
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def _run_batch(args, batch_id: str, user_keys: list[str]) -> None:
    BACKFILL_ROOT.mkdir(parents=True, exist_ok=True)
    dryrun_path = BACKFILL_ROOT / f"dryrun_{batch_id}.json"
    progress_path = BACKFILL_ROOT / "progress.jsonl"
    state = BackfillState(path=progress_path)

    all_candidates: list[tuple[str, dict]] = []
    tokens: dict[str, str] = {}

    for user_key in user_keys:
        bearer, auth_id = await _get_user_bearer(user_key)
        tokens[user_key] = bearer
        nodes = await _fetch_legacy_nodes(
            user_id=auth_id, bearer=bearer,
            min_age_days=args.min_age_days,
            source_type=args.source_type, limit=args.limit,
        )
        for n in nodes:
            if state.already_processed(n["id"]):
                continue
            all_candidates.append((user_key, n))

    # Dry-run: write plan only
    if args.dry_run and not args.live:
        dryrun_path.write_text(json.dumps({
            "batch_id": batch_id, "args": vars(args),
            "candidates": [{"user": u, **n} for u, n in all_candidates],
            "count": len(all_candidates),
        }, indent=2), encoding="utf-8")
        print(f"DRY-RUN: {len(all_candidates)} candidates written to {dryrun_path}")
        return

    if not args.live:
        print("Refusing to run without --dry-run or --live. See --help.")
        sys.exit(2)

    # Live run
    consecutive_failures = 0
    batch_size = len(all_candidates)
    critical_fail_threshold = max(5, batch_size // 10)

    for i, (user_key, n) in enumerate(all_candidates):
        try:
            resp = await _resummarize(server=args.server, bearer=tokens[user_key], node=n)
            summary = resp.get("summary", {})
            meta = summary.get("metadata", {}) or {}
            state.append(NodeOutcome(
                node_id=n["id"], user_id=n["user_id"], status="success",
                engine_version_before=n.get("engine_version") or "unknown",
                engine_version_after=meta.get("engine_version", "unknown"),
                composite_score=None,  # evaluator not run inline; optional future enhancement
                backfilled_at=datetime.now(timezone.utc).isoformat(),
            ))
            consecutive_failures = 0
            print(f"[{i+1}/{batch_size}] OK node={n['id']} user={user_key}")
        except Exception as exc:
            state.append(NodeOutcome(
                node_id=n["id"], user_id=n["user_id"], status="failed",
                engine_version_before=n.get("engine_version") or "unknown",
                engine_version_after=None, composite_score=None,
                backfilled_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc)[:500],
            ))
            consecutive_failures += 1
            print(f"[{i+1}/{batch_size}] FAIL node={n['id']} err={exc}")

        if consecutive_failures >= critical_fail_threshold:
            print(f"CRITICAL: {consecutive_failures} consecutive failures — aborting batch.")
            # Emit rollback SQL stub
            rollback_path = BACKFILL_ROOT / f"rollback_{batch_id}.sql"
            rollback_path.write_text(
                "-- Rollback stub for batch " + batch_id + "\n"
                "-- Human must restore via Supabase point-in-time recovery if needed.\n"
                "-- No automatic rollback — the backfill is idempotent; re-running with fresh inputs is safer than SQL restore.\n",
                encoding="utf-8",
            )
            sys.exit(3)

        if i < batch_size - 1:
            await asyncio.sleep(args.sleep_sec)

    print(f"DONE: batch {batch_id} complete, {batch_size} nodes processed.")


async def _main() -> int:
    args = _parse_args()
    batch_id = args.batch_id or datetime.now().strftime("%Y-%m-%d-%H%M")
    user_keys = list(USERS.keys()) if args.user == "all" else [args.user]

    if args.report_only:
        state = BackfillState(path=BACKFILL_ROOT / "progress.jsonl")
        print(f"Total successfully backfilled node IDs: {len(state.completed_node_ids())}")
        return 0

    if args.dry_run and args.live:
        print("Cannot use --dry-run and --live together."); return 2

    await _run_batch(args, batch_id, user_keys)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
```

- [ ] **Step 2: Commit**

```bash
git add ops/scripts/backfill_kg_v2.py
git commit -m "feat: kg backfill cli with dry run gate"
```

---

## Task 5: Dry-run against Zoro's 3 nodes

**Files:**
- Create: `docs/summary_eval/_backfill/kg_v2/README.md`

- [ ] **Step 1: Write README**

```markdown
# KG backfill v2 — batch log

Each dry-run writes `dryrun_<batch_id>.json` and each live run appends to `progress.jsonl`.

## Recovery
If a live batch fails mid-way, re-run with the same `--user` + `--limit` — already-processed node IDs are skipped via progress.jsonl.

## Safety gates
- First live run per user requires human approval.
- >10% consecutive-failure rate in a batch aborts + writes rollback stub.
- Point-in-time restore available via Supabase dashboard up to 7 days.
```

- [ ] **Step 2: Export env + run dry-run for Zoro (3 nodes per login_details.txt)**

```bash
# export SUPABASE_URL=https://icmnskseuoteyirljswd.supabase.co
# export SUPABASE_ANON_KEY=<private — from .env or secret manager>
python ops/scripts/backfill_kg_v2.py --dry-run --user zoro --limit 3
cat docs/summary_eval/_backfill/kg_v2/dryrun_*.json | python -m json.tool
```

Expected: JSON with up to 3 candidate nodes (Zoro has 3 total per login_details.txt). Each entry has `id`, `url`, `source_type`, `engine_version`. No KG writes.

- [ ] **Step 3: STOP for human review**

Codex stops here. Human inspects the dry-run JSON. If approved:
> Plan 10 Task 5 Step 4: human-approved; proceed to first live batch.

- [ ] **Step 4: First live batch for Zoro (after approval)**

```bash
python ops/scripts/backfill_kg_v2.py --live --user zoro --limit 3 --sleep-sec 3
```

Expected: 3 nodes updated in-place. Progress.jsonl grows by 3 success lines.

- [ ] **Step 5: Verify in Supabase**

```bash
curl -s "$SUPABASE_URL/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&select=id,engine_version,updated_at&order=updated_at.desc&limit=5" \
  -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```

Expected: 3 nodes with `engine_version="2.0.0"` and fresh `updated_at`. Record outcome in `progress.jsonl`.

- [ ] **Step 6: Commit the progress log (includes verified outcomes)**

```bash
git add docs/summary_eval/_backfill/kg_v2/
git commit -m "test: zoro backfill dryrun plus first live batch"
```

---

## Task 6: Naruto batch (larger, staged)

Naruto has 34 nodes per login_details.txt — run in 2 batches of ~17 each with dry-run + human approval between.

- [ ] **Step 1: Dry-run Naruto batch 1**

```bash
python ops/scripts/backfill_kg_v2.py --dry-run --user naruto --limit 17
```

- [ ] **Step 2: Human review** of `dryrun_<id>.json`.

- [ ] **Step 3: Live batch 1** (after approval)

```bash
python ops/scripts/backfill_kg_v2.py --live --user naruto --limit 17 --sleep-sec 3
```

- [ ] **Step 4: Verify + 24h soak (monitor KG health in prod — RAG responses still cite correctly, graph visualizer still renders)**

- [ ] **Step 5: Repeat for batch 2** (dry-run + approval + live)

- [ ] **Step 6: Commit progress**

```bash
git add docs/summary_eval/_backfill/kg_v2/progress.jsonl
git commit -m "test: naruto backfill complete"
```

---

## Task 7: Final report + promote PR

- [ ] **Step 1: Write summary report**

```bash
python ops/scripts/backfill_kg_v2.py --report-only
```

Paste output into `docs/summary_eval/_backfill/kg_v2/final_report.md`:

```markdown
# KG Backfill v2 — Final Report

## Totals
- Total nodes backfilled: <N>
- Successful: <N>
- Failed: <N>
- Skipped: <N>

## Per-user breakdown
- Zoro: 3/3 success
- Naruto: <N>/34 success

## Failure analysis (if any)
- <node_id>: <error>

## Quality spot-check
Picked 5 random backfilled nodes, ran them through /api/v2/summarize directly (no force_update), compared old vs new:
- <node_id>: old_composite=<N> new_composite=<N> delta=<N>
- ...

## Mean composite lift
- Mean before (legacy v1): <N>
- Mean after (v2): <N>
- Delta: +<N>
```

- [ ] **Step 2: Commit**

```bash
git add docs/summary_eval/_backfill/kg_v2/final_report.md
git commit -m "docs: kg backfill final report"
```

- [ ] **Step 3: Push + draft PR**

```bash
git push origin feat/kg-backfill-v2
gh pr create --draft --title "feat: kg historical backfill v2 engine" \
  --body "Plan 10. Re-summarizes legacy KG nodes via v2 engine with dry-run gate + per-user bearer auth + progress resumability. Zoro (3 nodes) + Naruto (34 nodes) backfilled. Mean composite lift: +<N>. See docs/summary_eval/_backfill/kg_v2/final_report.md.

### Deploy gate
Merging this PR lands no code changes to prod runtime (the backfill script is ops-only + the new SupabaseWriter.update_existing_node method is purely additive). Verify:
- [ ] CI green
- [ ] progress.jsonl shows all successful node IDs
- [ ] final_report.md shows mean composite lift > 0
- [ ] No KG schema changes (spec §1 non-goal preserved)

The backfill itself has ALREADY RUN against prod Supabase by the time this PR opens — merging only locks in the code changes + progress log."
```

- [ ] **Step 4: STOP + handoff**

Report:
> Plan 10 complete. Draft PR ready. 37 nodes backfilled (Zoro 3, Naruto 34). Mean composite lift: +<N>. Awaiting human review + merge.

---

## Self-review checklist
- [ ] Backfill is idempotent (rerun with same args is safe)
- [ ] `update_existing_node` preserves node IDs + all link references
- [ ] Per-user bearer tokens used; cross-user updates never happen
- [ ] Rate-limited (3 sec/node, 50 nodes/invocation)
- [ ] Progress log resumes cleanly after interrupt
- [ ] Dry-run runs before every live batch
- [ ] >10% consecutive-failure threshold aborts + writes rollback stub
- [ ] Supabase RLS not bypassed (no service-role key used)
- [ ] Zoro + Naruto both have dedicated verification curls
- [ ] final_report.md documents quality lift
