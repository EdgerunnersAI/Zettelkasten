# Summarization Engine Plan 6 — YouTube Iteration Loops 1-7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **RUNBOOK:** Execute commands strictly from `docs/summary_eval/RUNBOOK_CODEX.md` — it is the single source of truth for the two-phase state machine, manual_review.md template, per-iter URL allocation, recovery procedures, and the halt switch. The plan below is the goal spec; the runbook is how you actually run it.

**Goal:** Drive YouTube summarization quality to spec-§10.1 "production-grade" criteria (composite ≥ 92, RAGAS faithfulness ≥ 0.95 on training URL; held-out mean ≥ 88; prod-parity delta ≤ 5) through the 7-loop runbook defined in spec §4.1 and §8.2.

**Architecture:** Each loop is a two-phase CLI invocation with Codex's cross-model manual review in between. Tuning loops (2, 3, 5, 8) let Codex edit any allowed surface to move the score; churn protection (spec §8.4) blocks unproductive file re-edits. Measurement loops (1, 4, 6, 7) are scored but not tuned. Auto-triggered extensions 8-9 fire only if loop 6 fails held-out thresholds.

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §4.1 (loop allocation), §8.2 (runbook), §8.3 (edit cycle), §8.4 (churn protection), §10.1 (per-source acceptance)

**Branch:** `eval/summary-engine-v2-scoring-phase0-youtube` — same branch Plan 1 opened. This plan APPENDS iteration-loop commits to that existing draft PR. Do NOT create a new branch.

**Precondition:** Plan 1 complete. Branch exists, draft PR #X open, all Phase 0 + Phase 0.5 smoke tests green. `eval_loop.py --source youtube --list-urls` returns ≥ 3 URLs. Server startable via `python run.py`.

**Deploy discipline:** This plan finishes with `gh pr ready` + a request for human review. **Merging the PR triggers a production deploy** via `.github/workflows/deploy-droplet.yml`. Codex does NOT merge. After the human merges and verifies prod health, they explicitly start Plan 7.

---

## Critical edge cases Codex MUST handle during every loop

Read this section before any loop. Every one of these is defined in the spec; failing to handle them breaks the program contract.

### 1. Blind-review enforcement (spec §3.7)
Every `manual_review.md` MUST start with `eval_json_hash_at_review: "NOT_CONSULTED"` as its first line. The CLI enforces this in Phase B; if absent or if it contains an actual hash, Phase B exits with `status=blind_review_violation` in `next_actions.md` and halts. Codex must NOT read `iter-NN/eval.json` while writing `manual_review.md` — only the prompt file (rubric + summary + atomic_facts + source_text).

### 2. Determinism check (spec §8.2 runbook step 3)
CLI runs at every loop start (except iter-01): re-runs the evaluator on iter-(N-1)'s `summary.json`. If the new composite differs from the stored composite by > 2 pts, writes `status=evaluator_drift` and halts. Common cause: someone edited `evaluator/prompts.py` without bumping `PROMPT_VERSION`. Investigate before resuming.

### 3. Churn protection (spec §8.4)
Edit ledger at `docs/summary_eval/youtube/edit_ledger.json` tracks file edits per iteration + targeted criterion + criterion movement. A file is flagged `churning` if edited in ≥ 3 consecutive tuning iters AND the targeted criterion moved < 1.0 pt combined. When flagged, `next_actions.md` prints `CHURN ALERT`. Codex either:
- **Skips** the churning file this loop (recommended) OR
- **Writes** `docs/summary_eval/youtube/iter-<N>/new_angle.md` explaining the structural difference (e.g., "iter 02/03/05 tuned prompt wording; this iter replaces single-pass with two-turn refinement"). Without new_angle.md, CLI refuses with `status=churn_unresolved`.

### 4. Rubric editing constraint (spec §8.3 step 4)
Edits to `docs/summary_eval/_config/rubric_youtube.yaml` are ALLOWED only to fix misspecifications; FORBIDDEN to soften grading. Concretely forbidden:
- Raising a criterion's `max_points` / `weight` above spec baseline
- Lowering `hallucination_cap` (60), `omission_cap` (75), or `generic_cap` (90) thresholds
- Removing a criterion
- Relaxing `criteria_fired` requirements
Allowed: clarifying examples, typos, splitting an ambiguous criterion into two sharper ones with combined weight equal to original. Rubric-edit commits start with `docs: rubric fix youtube:` + 1-line rationale.

### 5. Off-limits files during iteration (spec §8.3 step 3)
Never edit inside a tuning loop:
- `website/features/summarization_engine/evaluator/**` (scoring code; dedicated "evaluator fix" commit outside iteration cycle only, with PROMPT_VERSION bump if prompts changed)
- `telegram_bot/**`
- `website/api/routes.py`
- Other sources' summarizer/schema/prompts (only YouTube surfaces touchable in this plan)
- `website/features/api_key_switching/**` (Plan-1-only surface)
- Own `manual_review.md` after Phase B commit

### 6. Billing-spillover monitoring (spec §9.4-§9.5)
The key pool auto-falls back free → billing when all free keys 429. Every `iter-NN/input.json` records `gemini_calls.role_breakdown.billing_calls` + `quota_exhausted_events`. After every loop:
```bash
python -c "
import json
from pathlib import Path
for d in sorted(Path('docs/summary_eval/youtube').glob('iter-*')):
    inp = json.loads((d / 'input.json').read_text(encoding='utf-8'))
    b = inp.get('gemini_calls', {}).get('role_breakdown', {}).get('billing_calls', 0)
    if b > 0:
        print(f'{d.name}: billing_calls={b}')
"
```
If any loop shows > 10 billing calls, pause + request human approval. Program total budget is ~50 Pro calls worst case (spec §9.6).

### 7. Quota total-exhaustion (`status=quota_all_keys_exhausted`)
All 3 Gemini keys 429 simultaneously. Wait for UTC midnight quota reset or pause for human to add another key. Do NOT retry.

### 8. `.halt` kill switch (spec §8.5)
If `docs/summary_eval/.halt` file exists, CLI exits immediately on any invocation with `status=halted`. Human uses this to pause without killing Python. Codex does not remove the file automatically.

### 9. Server restart after config changes (spec §8.2 runbook step 1.5)
`--manage-server` (default on) restarts FastAPI at every loop start. Required for:
- `config.yaml` edits (lru_cached loader needs fresh process)
- Prompts.py / summarizer.py edits (clean import)
Never omit `--manage-server` — hot-reload has edge cases that corrupt state.

### 10. Manual-review composite estimation (spec §3.6)
Final line of manual_review.md: `estimated_composite: NN.N` computed as:
```
base = 0.60 * rubric_total_of_100
     + 0.20 * finesure.faithfulness * 100
     + 0.10 * finesure.completeness * 100
     + 0.10 * mean(g_eval_4) * 20
composite = apply_caps(base, rubric.caps_applied)
# caps: hallucination=60, omission=75, generic=90; first-match dominates
```
YouTube-specific caps to watch:
- Anti-pattern `clickbait_label_retention` → generic_cap=90
- Anti-pattern `example_verbatim_reproduction` → penalty −3 (no auto-cap)
- Anti-pattern `editorialized_stance` → hallucination_cap=60
- Anti-pattern `speakers_absent` → omission_cap=75
- Anti-pattern `invented_chapter` → hallucination_cap=60
- Editorialization flags ≥ 3 (global rule) → hallucination_cap=60

### 11. Cross-model disagreement (spec §3.7)
CLI computes `divergence = |gemini − codex|` in Phase B. Stamps in manual_review.md:
- ≤ 5: `AGREEMENT`
- 5 < … ≤ 10: `MINOR_DISAGREEMENT`
- > 10: `MAJOR_DISAGREEMENT`; pessimistic rule — LOWER of the two is the score next tuning loop must beat.

After 2 consecutive MAJOR_DISAGREEMENT loops, write `docs/summary_eval/youtube/iter-<N>/disagreement_analysis.md` (one paragraph: which model you trust on this source, tiebreaker hypothesis). Not an auto-halt; an attention anchor.

### 12. Reproducibility `--replay` (spec §8.6)
If a loop score looks suspicious (jump/drop > 10 pts without matching edit), run:
```bash
python ops/scripts/eval_loop.py --source youtube --iter <N> --replay
```
Full re-run from artifacts; composite must match within ±1 pt. If not, evaluator drift or race condition — halt and investigate.

### 13. YouTube-specific: transcript tier logging
After every loop, check which tier fired per URL in `iter-NN/summary.json`:
```bash
python -c "
import json
from pathlib import Path
for d in sorted(Path('docs/summary_eval/youtube').glob('iter-*')):
    s = json.loads((d / 'summary.json').read_text(encoding='utf-8'))
    entries = s if isinstance(s, list) else [{'response': s}]
    for e in entries:
        meta = e.get('response', {}).get('summary', {}).get('metadata', {}) or {}
        print(f'{d.name}: tier_used={meta.get(\"tier_used\",\"?\")} confidence={meta.get(\"extraction_confidence\",\"?\")}')
"
```
Expected in mature iterations: all URLs landed at tier 1 (`ytdlp_player_rotation`) or tier 2 (`transcript_api_direct`) with `confidence=high`. If any URL consistently lands on tier 5 (`gemini_audio`) or tier 6 (`metadata_only`), it's a quality ceiling — note in final_scorecard.md.

### 14. YouTube-specific: schema required fields
`YouTubeStructuredPayload` requires `speakers: list[str]` (min_length=1) and `chapters_or_segments: list[ChapterBullet]` (min_length=1). Schema-validation failures fall back to default payload with `schema_validation_failed=true` in metadata. Watch for this in Plans 2/3 and investigate if summarizer output repeatedly fails validation — likely a prompt tightening needed.

---

## URL allocation (from links.txt `# YouTube` section)

| Role | URL index |
|---|---|
| Training URL (#1) — used in loops 1, 2, 3, 5 | links.txt YouTube URL 1 |
| Cross-URL probe (#2) — used in loop 4, 5 | links.txt YouTube URL 2 |
| Cross-URL #3 — used in loop 5 | links.txt YouTube URL 3 |
| Held-out (#4, #5) — used in loops 6, 7 | links.txt YouTube URLs 4-5 |

Codex reads these dynamically via `parse_links_file()` — no hardcoding.

---

## File structure summary

### Files CREATED per loop (under `docs/summary_eval/youtube/iter-NN/`)
- `input.json`, `summary.json`, `eval.json`, `manual_review_prompt.md`, `manual_review.md`, `diff.md`, `next_actions.md`, `run.log`

### Files CODEX MAY EDIT in tuning loops (per spec §8.3)
- `website/features/summarization_engine/summarization/youtube/{prompts,schema,summarizer}.py`
- `website/features/summarization_engine/summarization/common/*.py` (cross-cutting; flag in commit)
- `website/features/summarization_engine/source_ingest/youtube/{ingest,tiers}.py`
- `website/features/summarization_engine/config.yaml`
- `docs/summary_eval/_config/rubric_youtube.yaml` (misspecification fixes only; never grading softening)

### Off-limits during iteration (spec §8.3)
- `website/features/summarization_engine/evaluator/**`
- `telegram_bot/**`
- `website/api/routes.py`
- Any other source's summarizer/schema/prompts

### Files CREATED at plan end
- `docs/summary_eval/youtube/final_scorecard.md`

---

## Task 0: Preflight + server start

**Files:**
- None created

- [ ] **Step 1: Checkout the Plan 1 branch**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git fetch origin
git checkout eval/summary-engine-v2-scoring-phase0-youtube
git pull
```

- [ ] **Step 2: Confirm Plan 1 artifacts present**

```bash
test -f docs/summary_eval/_config/rubric_youtube.yaml && echo "rubric OK"
test -f docs/summary_eval/youtube/phase0.5-ingest/decision.md && echo "phase0.5 OK"
python -c "from website.features.summarization_engine.summarization.youtube.summarizer import YouTubeSummarizer; print('import OK')"
```
Expected: 3 OK lines.

- [ ] **Step 3: Confirm 3+ training URLs + held-out URLs available**

```bash
python ops/scripts/eval_loop.py --source youtube --list-urls
```
Expected: JSON list with ≥ 5 URLs (training + held-out).

- [ ] **Step 4: Start managed server**

```bash
python run.py &
sleep 5
curl -s http://127.0.0.1:10000/api/health
```
Expected: 200 response.

- [ ] **Step 5: Verify no stale iteration artifacts**

```bash
ls docs/summary_eval/youtube/iter-*/ 2>/dev/null
```
Expected: no output (no prior iteration folders). If any exist from aborted prior runs, delete them before starting:
```bash
rm -rf docs/summary_eval/youtube/iter-*
```

---

## Task 0.6: Pre-loop correctness gate (schema-routing smoke)

Before burning any training URL, prove that the YouTube summarizer actually routes through `YouTubeStructuredPayload` and produces source-shaped output. If this gate fails, STOP — iter-01 would score garbage and every later loop would inherit the bug.

**Files:**
- None created (assertions only).

- [ ] **Step 1: In-process route smoke + shape assertions**

Uses the same `summarize_url` entry-point the eval_loop calls in Phase A, so the gate sees exactly what iter-01 will see. Prints `GATE OK` on success, exits non-zero on any failure.

```bash
python - <<'PY'
import asyncio, json, os, re, sys
from uuid import UUID
from pathlib import Path
from website.features.summarization_engine.core.orchestrator import summarize_url
from website.features.summarization_engine.api.routes import _gemini_client
from ops.scripts.lib.links_parser import parse_links_file

by_source = parse_links_file(Path("docs/testing/links.txt"))
urls = by_source.get("youtube") or []
assert urls, "no youtube URLs in links.txt"
url = urls[0]
client = _gemini_client()
USER = UUID("00000000-0000-0000-0000-000000000001")
res = asyncio.run(summarize_url(url, user_id=USER, gemini_client=client))
r = res.model_dump(mode="json")
md = r.get("metadata", {}) or {}
sp = md.get("structured_payload") or {}
errs = []
if md.get("is_schema_fallback"): errs.append("is_schema_fallback=True (routing broken)")
if "_schema_fallback_" in (r.get("tags") or []): errs.append("_schema_fallback_ tag present")
for bp in ("zettelkasten","summary","capture","research","notes"):
    if bp in (r.get("tags") or []): errs.append(f"boilerplate tag: {bp}")
ds = sp.get("detailed_summary") or {}
for req in ("thesis","chapters_or_segments","closing_takeaway"):
    if req not in ds: errs.append(f"missing YouTube field: detailed_summary.{req}")
if not (ds.get("chapters_or_segments") or []): errs.append("chapters_or_segments empty")
if errs:
    print("GATE FAILED:", *errs, sep="\n - "); sys.exit(1)
print(f"GATE OK url={url}")
PY
```
Expected: `GATE OK url=...`. If it fails: fix routing/schema BEFORE starting iter-01. Do not proceed.

- [ ] **Step 2: Evaluator-version gate**

```bash
python -c "from website.features.summarization_engine.evaluator.prompts import PROMPT_VERSION; assert PROMPT_VERSION=='evaluator.v3', PROMPT_VERSION; print('evaluator.v3 OK')"
```

---

## Task 1: Loop 1 — Baseline (URL #1, measurement only)

**Files:**
- Create: `docs/summary_eval/youtube/iter-01/*` (via CLI)

- [ ] **Step 1: Run Phase A**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 1 --phase iter --manage-server
```
Expected: `status=awaiting_manual_review path=docs/summary_eval/youtube/iter-01/manual_review_prompt.md`

- [ ] **Step 2: Write `manual_review.md` (Codex action)**

Read `docs/summary_eval/youtube/iter-01/manual_review_prompt.md`. Read the rubric YAML + summary.json + atomic_facts + source_text embedded in it. Do NOT open `iter-01/eval.json`.

Write `docs/summary_eval/youtube/iter-01/manual_review.md`:
```
eval_json_hash_at_review: "NOT_CONSULTED"

# Manual Review — YouTube iter-01 (baseline, URL #1)

## brief_summary (/25)
<5-15 sentence prose, criterion-by-criterion from rubric_youtube.yaml components[0].criteria, with scores summed>

## detailed_summary (/45)
<prose>

## tags (/15)
<prose>

## label (/15)
<prose>

## Anti-patterns
<any triggered: yes/no + which>

## Most impactful improvement for iter-02
<one-paragraph note>

estimated_composite: NN.N
```

- [ ] **Step 3: Run Phase B (auto-detect)**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 1 --phase iter
```
CLI auto-detects `manual_review.md` exists → runs Phase B (divergence computation, diff.md, next_actions.md, commit). Commit lands: `test: youtube iter-01 score <N>→<N>`.

- [ ] **Step 4: Record baseline**

Open `docs/summary_eval/youtube/iter-01/eval.json`. Record the `composite_score` as `BASELINE_COMPOSITE` for comparison against later loops. Open `next_actions.md` to see the `status=` field: must be `continue`, else halt and investigate.

- [ ] **Step 5: Note (no code edits in loop 1)**

Loop 1 is measurement-only (spec §4.1). No tuning-edit commits happen in this loop. Proceed to Task 2.

---

## Task 2: Loop 2 — First tune on URL #1

**Files:**
- Create: `docs/summary_eval/youtube/iter-02/*`
- Possibly modify: YouTube summarizer / prompts / schema / ingest / config / rubric

- [ ] **Step 1: Read iter-01 next_actions**

Open `docs/summary_eval/youtube/iter-01/next_actions.md`. It lists ranked edit proposals targeting the lowest-scoring criteria from iter-01 eval.

- [ ] **Step 2: Apply edits (Codex judgment)**

Per spec §8.3 + §8.4: apply edits to any allowed surfaces that the ranked list proposes, in any quantity. No hard cap on files or lines. Churn protection kicks in at iter-04+; not yet relevant.

Example edit patterns from rubric criteria likely to fire on iter-01:
- `brief.thesis_capture` low → sharpen the YouTube CoD densifier prompt's thesis-extraction instruction in `summarization/youtube/prompts.py`
- `brief.speakers_captured` low → make `speakers` field required-and-populated in `summarization/youtube/schema.py` with a model_validator
- `detailed.all_chapters_covered` low → raise `chain_of_density.iterations` from 2 to 3 in `config.yaml` for YouTube-typed source context
- `label.no_clickbait_retention` low → add explicit "remove exclamation, hook phrasing, curiosity-gap" clause to youtube/prompts.py STRUCTURED_EXTRACT_INSTRUCTION
- Anti-pattern `speakers_absent` triggered → tighten YouTube schema to reject payloads with empty speakers list (already done in Plan 1 Task 4; verify)

- [ ] **Step 3: Run pytest (allowed-surface regression guard)**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
```
Expected: green. If red, fix regressions before running the loop.

- [ ] **Step 4: Commit edits**

Multiple commits allowed. Tags per CLAUDE.md:
```bash
git commit -m "feat: youtube cod thesis extraction tightened"
# (and/or)
git commit -m "refactor: youtube schema enforce speakers list"
# (and/or)
git commit -m "docs: rubric youtube fix clickbait criterion wording"
```

- [ ] **Step 5: Run Phase A for iter-02**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 2 --phase iter --manage-server
```
`--manage-server` restarts FastAPI so the edited summarizer code is loaded. Expected: `status=awaiting_manual_review`.

- [ ] **Step 6: Write iter-02 manual_review.md**

Same template as Task 1 Step 2, filled for iter-02 scores.

- [ ] **Step 7: Run Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 2 --phase iter
```
Commit lands: `test: youtube iter-02 score <prev>→<cur>`.

- [ ] **Step 8: Assert score moved the right direction**

Open `docs/summary_eval/youtube/iter-02/diff.md`. `score_delta_vs_prev` should be > 0. If score decreased by ≥ 5: revert edits (`git revert <commit_shas>`), write `docs/summary_eval/youtube/iter-02/regression_note.md` explaining why, and re-run iter-02 with different edits.

---

## Task 3: Loop 3 — Second tune on URL #1

- [ ] **Step 1: Read iter-02 next_actions.md**

- [ ] **Step 2: Apply edits** targeting the criteria still below full credit after iter-02.

Typical iter-03 focus areas:
- Mid-range criteria (e.g., `detailed.demonstrations_preserved`, `tags.topical_specificity`) that didn't fully resolve in iter-02
- `maps_to_metric_summary` showing weakest dimension (usually finesure.faithfulness or g_eval.coherence)

- [ ] **Step 3: pytest + commit**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive tag per change>"
```

- [ ] **Step 4: Phase A**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 3 --phase iter --manage-server
```

- [ ] **Step 5: Write iter-03 manual_review.md**

- [ ] **Step 6: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 3 --phase iter
```

- [ ] **Step 7: Optional early-stop check**

Per spec §3.8: source is "done" iterating when ≥ 3 of the last 5 tuning iters had composite ≥ 92 AND ragas_faithfulness ≥ 0.95 on URL #1, AND loop-5 all 3 URLs ≥ 88. At iter-03 this can't trigger yet (we haven't run loop 4 or 5). But if iter-03 already hits composite ≥ 92 on URL #1, it's a strong signal. Record it; proceed to loop 4.

---

## Task 4: Loop 4 — Cross-URL probe (URLs #1 + #2, no edits)

- [ ] **Step 1: Run Phase A against URLs #1 + #2**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 4 --phase iter --manage-server
```
The CLI per §4.1 allocation runs both URL #1 and URL #2 in this loop. `summary.json` contains 2 entries; `eval.json` contains 2.

- [ ] **Step 2: Write iter-04 manual_review.md**

Review BOTH summaries (one section per URL) in the same manual_review.md. `estimated_composite` is the MEAN of URLs #1 + #2 scores.

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 4 --phase iter
```

- [ ] **Step 4: Measure overfitting**

Open `docs/summary_eval/youtube/iter-04/diff.md`. Record:
- URL #1 score at iter-04 vs iter-03 (should be ~same — same URL, same code)
- URL #2 score at iter-04 (first-time measurement)
- Gap = (URL #1 iter-03 composite) - (URL #2 iter-04 composite)

If gap > 15, the tuning from loops 2-3 overfit to URL #1's specifics. Loop 5 (joint tune) will need to broaden. Note this in iter-04 diff.md.

- [ ] **Step 5: No edits in loop 4** (measurement-only). Proceed to loop 5.

---

## Task 5: Loop 5 — Joint tune (URLs #1 + #2 + #3)

**This is the convergence gate.** Per spec §3.8: loop 5 must have URLs #1, #2, #3 each ≥ 88 composite for early-stop to fire.

- [ ] **Step 1: Read iter-04 next_actions.md + cross-reference iter-04 diff.md**

Identify criteria that scored well on URL #1 but poorly on URL #2 (overfitting signal).

- [ ] **Step 2: Apply joint-broadening edits**

Strategy for joint-tune loops (per spec philosophy): edits target criteria that fail on BOTH URLs, prefer source-agnostic YouTube prompt tweaks over URL-specific ones. Avoid edits that only help URL #1 at the cost of URL #2.

- [ ] **Step 3: pytest + commit**

- [ ] **Step 4: Phase A (3 URLs this loop)**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 5 --phase iter --manage-server
```
CLI runs URL #1 + #2 + #3. Evaluator runs against all 3.

- [ ] **Step 5: Write iter-05 manual_review.md**

Three URL sections, one composite per URL, overall `estimated_composite = mean(3)`.

- [ ] **Step 6: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 5 --phase iter
```

- [ ] **Step 7: Check early-stop gate**

Open `docs/summary_eval/youtube/iter-05/eval.json`. Check:
- URL #1 composite ≥ 92 AND ragas_faithfulness ≥ 0.95: yes/no
- URL #2 composite ≥ 88: yes/no
- URL #3 composite ≥ 88: yes/no
- Count across iter-01 through iter-05: how many iters had URL #1 composite ≥ 92 AND ragas ≥ 0.95? (Should be ≥ 3 for early-stop.)

If ALL gates pass → `status=converged` in `iter-05/next_actions.md` (CLI sets it). Loop 6 + 7 still run (they're held-out + prod-parity validation, not early-stop-eligible). Proceed to loop 6.

If gates fail → continue to loop 6 normally; extension loops may trigger at loop 6.

---

## Task 6: Loop 6 — Held-out validation (all remaining URLs, no edits)

- [ ] **Step 1: Run Phase A against all held-out URLs**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 6 --phase iter --manage-server
```
CLI runs all YouTube URLs in links.txt beyond URL #1-#3. For 5 total YouTube URLs: runs URLs #4, #5.

Artifact layout (spec §5):
```
docs/summary_eval/youtube/iter-06/
├── held_out/
│   ├── <url4_sha256>/summary.json + eval.json
│   └── <url5_sha256>/summary.json + eval.json
├── aggregate.md              # CLI-written: per-URL and mean scores
├── manual_review_prompt.md
├── manual_review.md          # Codex writes AFTER aggregate.md exists
├── diff.md
└── next_actions.md
```

- [ ] **Step 2: Write iter-06 manual_review.md**

Review each held-out URL's summary + eval. Write one section per held-out URL. `estimated_composite` = mean across held-out set.

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 6 --phase iter
```

- [ ] **Step 4: Check held-out thresholds**

Open `docs/summary_eval/youtube/iter-06/aggregate.md`:
- Held-out mean composite ≥ 88: yes/no
- Minimum ragas_faithfulness across held-out: ≥ 0.95: yes/no
- Any held-out URL with composite < 85: count

If held-out mean ≥ 88 AND minimum faithfulness ≥ 0.95 → **proceed to loop 7**. CLI will set `status=continue`.

If either fails → **extension loops 8-9 will auto-trigger**. CLI will set `status=extension_required` in `iter-06/next_actions.md`. Skip to Task 8.

---

## Task 7: Loop 7 — Prod-parity validation (Zoro auth, SUMMARIZE_ENV=prod-parity)

Per spec §1 (non-goal) + §8.2 (runbook): loop 7 uses local server but with prod-parity config overrides + Zoro Supabase auth. The KG + RAG pipeline is exercised because summaries write to the real Supabase under Zoro's user_id.

- [ ] **Step 1: Export Supabase creds + kill-restart server with prod-parity env**

```bash
export SUMMARIZE_ENV=prod-parity
# SUPABASE_URL is public-safe; ANON_KEY comes from your secure store — export it manually.
export SUPABASE_URL=https://icmnskseuoteyirljswd.supabase.co
# export SUPABASE_ANON_KEY=<private — from .env or secret manager>

# Restart the managed server so it picks up the env
kill %1 2>/dev/null
python run.py &
sleep 5
curl -s http://127.0.0.1:10000/api/health
```
Expected: 200.

- [ ] **Step 2: Run Phase A with --env prod-parity**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 7 --phase iter --env prod-parity --manage-server
```
CLI will:
1. Fetch Zoro bearer token via `/auth/v1/token?grant_type=password` using creds parsed from `docs/login_details.txt`.
2. Write `docs/summary_eval/youtube/iter-07/prod_parity_auth.txt` with `authenticated_as=zoro user_id=a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e`.
3. POST each held-out URL to `/api/v2/summarize` with `write_to_supabase=true` and the bearer token.
4. Verify response contains a Supabase KG node_id.

If SUPABASE_ANON_KEY is not exported, CLI logs `prod_parity_auth skipped; summaries still run but no KG writes`. Tolerate this — Zoro writes are nice-to-have, not required for the loop-7 score.

- [ ] **Step 3: Write iter-07 manual_review.md**

Same held-out format as loop 6. Use the iter-07 summaries (which may differ slightly from iter-06 because caches were bypassed in prod-parity mode).

- [ ] **Step 4: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 7 --phase iter --env prod-parity
```

- [ ] **Step 5: Check prod-parity delta**

```bash
python -c "
import json
iter6 = json.load(open('docs/summary_eval/youtube/iter-06/aggregate.md' if False else 'docs/summary_eval/youtube/iter-06/eval.json'))
iter7 = json.load(open('docs/summary_eval/youtube/iter-07/eval.json'))
# compare mean composite per spec §10.1 prod-parity delta ≤ 5
"
```

Per spec §10.1: `|prod-parity composite − held-out composite| ≤ 5`. If delta > 5, record as risk in `iter-07/next_actions.md` and in `final_scorecard.md` as a known issue but do not fail the PR — a small delta can reflect cache warmth differences.

- [ ] **Step 6: If Supabase KG writes landed, verify KG + RAG end-to-end**

```bash
# Query Zoro's nodes
curl -s "https://icmnskseuoteyirljswd.supabase.co/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&select=id,mini_title,source_type&order=created_at.desc&limit=5" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```
Expected: the loop-7 held-out URL mini_titles appear. Record the top 3 node_ids in `iter-07/zoro_kg_verification.md`. This proves the KG pipeline end-to-end.

For RAG: open `https://zettelkasten.in/chat` (or the local equivalent), log in as Zoro, ask a question referencing one of Zoro's new YouTube zettels. Verify the RAG answer cites the new node. Record outcome in same verification file.

- [ ] **Step 7: Reset prod-parity env**

```bash
unset SUMMARIZE_ENV
kill %1
```

---

## Task 8: Loop 8 — Extension (conditional: only if loop 6 failed)

Skip this task entirely if loop 6's `next_actions.md` set `status=continue`. Only run if `status=extension_required`.

- [ ] **Step 1: Check extension trigger**

```bash
grep -q "^status: extension_required" docs/summary_eval/youtube/iter-06/next_actions.md && echo "EXTEND" || echo "SKIP"
```

- [ ] **Step 2: Read iter-06 aggregate.md for root-cause signals**

Identify which held-out URLs failed and which criteria. Cross-reference with iter-05 — do failures correlate with a specific format/topic absent from URLs #1-#3?

- [ ] **Step 3: Apply joint re-tune targeting failed held-out URLs**

Loop 8 is a full tuning loop. Edit allowed surfaces; focus on the criteria that failed on held-out. Churn protection kicks in — if an allowed file was edited in iter-02/03/05 already without moving its target criterion, the CLI will print CHURN ALERT during the run. Either skip that file or write `iter-08/new_angle.md` justifying a structurally different angle (spec §8.4).

- [ ] **Step 4: pytest + commit + Phase A**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source youtube --iter 8 --phase iter --manage-server
```

- [ ] **Step 5: Write iter-08 manual_review.md**

Review URL #1 + URL #2 + URL #3 + any failed held-out URLs. `estimated_composite` = mean across all.

- [ ] **Step 6: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 8 --phase iter
```

---

## Task 9: Loop 9 — Extension final validation (conditional)

Skip unless loop 8 ran.

- [ ] **Step 1: Run Phase A on all held-out URLs (no edits)**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 9 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-09 manual_review.md**

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source youtube --iter 9 --phase iter
```

- [ ] **Step 4: Final check**

If iter-09 held-out still fails thresholds: YouTube source is marked `degraded` in `final_scorecard.md` with root-cause analysis. Program continues to Plan 7 regardless (spec §10.1 explicit: degraded sources don't block the program).

---

## Task 10: Final scorecard + promote PR

**Files:**
- Create: `docs/summary_eval/youtube/final_scorecard.md`

- [ ] **Step 1: Write `final_scorecard.md`**

```markdown
# YouTube — Final Scorecard

## Per-loop progression
| Loop | URLs | Composite (mean) | Faithfulness (min) | Status |
|---|---|---|---|---|
| 1 (baseline) | #1 | <score> | <faith> | baseline |
| 2 (tune) | #1 | <score> | <faith> | <delta> |
| 3 (tune) | #1 | <score> | <faith> | <delta> |
| 4 (probe) | #1, #2 | <score> | <faith> | cross-URL probe |
| 5 (joint) | #1, #2, #3 | <score> | <faith> | <converged? yes/no> |
| 6 (held-out) | #4, #5 | <score> | <faith> | <thresholds met? yes/no> |
| 7 (prod-parity) | #4, #5 | <score> | <faith> | <delta ≤5? yes/no> |
| 8 (ext, if fired) | ... | ... | ... | ... |
| 9 (ext, if fired) | ... | ... | ... | ... |

## Acceptance (spec §10.1)
- [ ] Training URL composite ≥ 92 in ≥ 3 of last 5 tuning iters
- [ ] Training URL ragas_faithfulness ≥ 0.95
- [ ] URLs #1, #2, #3 each ≥ 88 at loop 5
- [ ] Held-out mean ≥ 88 AND min ragas_faithfulness ≥ 0.95 (loop 6)
- [ ] Prod-parity delta ≤ 5 (loop 7)
- [ ] No iteration triggered hallucination cap (60) in loops 5-7
- [ ] Rubric label regex match in 100% of loop-6 held-out runs

**Overall: PRODUCTION-GRADE | DEGRADED**

## Lessons (for Plan 10 cross_source_lessons.md)
- <3-5 bullets on what moved the score most + what didn't>

## Known risks / follow-ups
- <any open issues>

## Zoro prod-parity verification (loop 7)
- Supabase KG writes: <N> new nodes under user_id a57e1f2f-...
- RAG retrieval: <yes/no> — verified that new nodes surface in chat
- Reference: docs/summary_eval/youtube/iter-07/zoro_kg_verification.md
```

- [ ] **Step 2: Commit scorecard**

```bash
git add docs/summary_eval/youtube/final_scorecard.md
git commit -m "docs: youtube final scorecard"
```

- [ ] **Step 3: Push + promote draft PR to ready**

```bash
git push origin eval/summary-engine-v2-scoring-phase0-youtube
gh pr ready <PR_NUMBER>
```

Update the PR body to append:
```markdown

## Iteration loops complete (Plan 6)
- Final composite (mean, held-out): <N>
- Final ragas_faithfulness (min, held-out): <N>
- Zoro prod-parity verified: yes / no
- Status: **production-grade** | **degraded** with <reason>

### Deploy gate
Merging this PR triggers a production deploy. Verify:
- [ ] CI green
- [ ] final_scorecard.md status is production-grade OR documented degraded
- [ ] Zoro KG writes verified (loop 7)
- [ ] No secrets in diff

Do NOT merge Plan 7 (Reddit) before this PR's deploy is verified healthy.
```

- [ ] **Step 4: STOP. Wait for human approval.**

Codex does NOT merge. Codex does NOT run Plan 7. Hand control back to the human:

> Plan 6 complete. Draft PR #<N> promoted to ready. Branch: `eval/summary-engine-v2-scoring-phase0-youtube`. Final composite: <score>. Zoro verification: <status>. Ready for human review + merge. Will not proceed to Plan 7 until you confirm deploy health.

---

## Self-review checklist
- [ ] Every loop has Phase A + manual_review + Phase B
- [ ] Every manual_review.md has `eval_json_hash_at_review: "NOT_CONSULTED"` stamp
- [ ] Loops 1, 4, 6, 7 did NOT include code edits (measurement-only)
- [ ] Loops 2, 3, 5 (and 8 if fired) DID include commits with `feat:` / `refactor:` / `docs:` tags
- [ ] Churn protection respected — any 3-consecutive edits to same file had `new_angle.md`
- [ ] Loop 7 attempted Zoro auth; verification recorded (or documented skip)
- [ ] Extension loops 8-9 only ran if loop-6 thresholds failed
- [ ] final_scorecard.md acceptance table filled
- [ ] PR body updated with deploy gate block
- [ ] NO merge/push-to-master performed
