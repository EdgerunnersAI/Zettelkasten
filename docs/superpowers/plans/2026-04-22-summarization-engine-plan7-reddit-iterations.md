# Summarization Engine Plan 7 — Reddit Iteration Loops 1-7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **RUNBOOK:** Execute commands strictly from `docs/summary_eval/RUNBOOK_CODEX.md` — it is the single source of truth for the two-phase state machine, manual_review.md template, per-iter URL allocation, recovery procedures, and the halt switch. The plan below is the goal spec; the runbook is how you actually run it.

**Goal:** Drive Reddit summarization quality to spec-§10.1 production-grade (composite ≥ 92 + faithfulness ≥ 0.95 on training URL; held-out mean ≥ 88; prod-parity delta ≤ 5) through the 7-loop runbook.

**Architecture:** Same two-phase loop runbook as Plan 6 (Phase A Gemini eval → Codex manual_review → Phase B finalize). Per-source focus: Reddit's rubric_reddit.yaml emphasizes `hedged_attribution` (never assert comment claims as fact), `moderation_context` (mention divergence between `num_comments` and `rendered_count`), and `label.rsubreddit_prefix` (must start with `r/<sub> `). Plan 2 shipped pullpush.io enrichment + divergence tracking; this plan tunes the summarizer to USE those signals in the rubric-required way.

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §4.1, §8.2–§8.4, §10.1

**Branch:** `eval/summary-engine-v2-scoring-reddit` — same branch Plan 2 opened. This plan appends iteration commits to that draft PR.

**Precondition:** Plan 6 PR merged to master + prod deploy verified healthy. `eval/summary-engine-v2-scoring-reddit` branch exists (created in Plan 2 Task 0).

**Deploy discipline:** Finishes with `gh pr ready` + human-review handoff. Codex does NOT merge. Merge triggers prod deploy via GitHub Actions. Plan 8 does not start until human confirms this PR's prod deploy is healthy.

---

## Critical edge cases Codex MUST handle during every loop

Read this section before any loop. Every one of these is defined in the spec; failing to handle them breaks the program contract.

### 1. Blind-review enforcement (spec §3.7)
`manual_review.md` MUST start with `eval_json_hash_at_review: "NOT_CONSULTED"`. CLI Phase B halts with `status=blind_review_violation` otherwise. NEVER read `iter-NN/eval.json` while writing the review — only the prompt file.

### 2. Determinism check (spec §8.2 runbook step 3)
CLI re-runs evaluator on iter-(N-1)'s `summary.json` at every loop start (except iter-01). If composite drifts > 2 pts, halts with `status=evaluator_drift`. Common cause: `evaluator/prompts.py` or `rubric_reddit.yaml` edited without version bump.

### 3. Churn protection (spec §8.4)
`docs/summary_eval/reddit/edit_ledger.json` tracks file edits per iteration + targeted criterion. Files edited in ≥ 3 consecutive tuning iters with combined criterion movement < 1.0 pt are flagged `churning`; `next_actions.md` prints `CHURN ALERT`. Codex either:
- Skips the churning file this loop, OR
- Writes `docs/summary_eval/reddit/iter-<N>/new_angle.md` explaining the structurally-different approach (e.g., "iter 02/03/05 tuned the hedged-attribution prompt wording; this iter replaces the prompt with a post-validator that rejects non-hedged commenter claims").
Without new_angle.md, CLI refuses with `status=churn_unresolved`.

### 4. Rubric editing constraint (spec §8.3 step 4)
`rubric_reddit.yaml` edits: misspecifications only, never grading softening. Commit with `docs: rubric fix reddit:` + rationale. Forbidden:
- Raising max_points/weights
- Lowering hallucination_cap (60), omission_cap (75), generic_cap (90)
- Removing criteria
- Relaxing criteria_fired requirements

### 5. Off-limits files (spec §8.3 step 3)
Never edit in tuning loops:
- `website/features/summarization_engine/evaluator/**`
- `telegram_bot/**`
- `website/api/routes.py`
- Other sources' summarizer/schema/prompts (only Reddit touchable)
- `website/features/api_key_switching/**`
- Own `manual_review.md` after Phase B

### 6. Billing-spillover monitoring (spec §9.4-§9.5)
After every loop check `iter-NN/input.json → gemini_calls.role_breakdown.billing_calls`:
```bash
python -c "
import json
from pathlib import Path
for d in sorted(Path('docs/summary_eval/reddit').glob('iter-*')):
    inp = json.loads((d / 'input.json').read_text(encoding='utf-8'))
    b = inp.get('gemini_calls', {}).get('role_breakdown', {}).get('billing_calls', 0)
    if b > 0:
        print(f'{d.name}: billing_calls={b}')
"
```
Pause + request human approval if any loop fires > 10 billing calls. Program total ~50 Pro calls worst case.

### 7. Quota total-exhaustion (`status=quota_all_keys_exhausted`)
All 3 keys 429. Wait UTC-midnight quota reset, or pause for human to add a key. Do NOT retry.

### 8. `.halt` kill switch (spec §8.5)
`docs/summary_eval/.halt` present = CLI exits immediately with `status=halted`. Honor it.

### 9. Server restart after config changes
`--manage-server` (default on) restarts FastAPI at every loop start. Required for config.yaml, prompts.py, summarizer.py, ingest.py edits. Never omit.

### 10. Manual-review composite math (spec §3.6)
Final line: `estimated_composite: NN.N` computed as:
```
base = 0.60 * rubric_total_of_100
     + 0.20 * finesure.faithfulness * 100
     + 0.10 * finesure.completeness * 100
     + 0.10 * mean(g_eval_4) * 20
composite = apply_caps(base, rubric.caps_applied)
# caps: hallucination=60, omission=75, generic=90; first-match dominates
```
Reddit-specific caps to watch:
- Anti-pattern `comment_claim_asserted_as_fact` → hallucination_cap=60
- Anti-pattern `missing_removed_comment_note` → omission_cap=75 (when divergence_pct > 20 but summary silent on removed comments)
- Anti-pattern `editorialized_stance` → hallucination_cap=60
- Editorialization flags ≥ 3 (global rule) → hallucination_cap=60

### 11. Cross-model disagreement (spec §3.7)
divergence = |gemini − codex|; stamp AGREEMENT (≤5) / MINOR_DISAGREEMENT (5-10) / MAJOR_DISAGREEMENT (>10). Pessimistic rule: LOWER is the score to beat. After 2 consecutive MAJORs, write `iter-<N>/disagreement_analysis.md` (one paragraph).

### 12. Reproducibility `--replay` (spec §8.6)
Sudden score jump/drop > 10 pts without matching edit → run `python ops/scripts/eval_loop.py --source reddit --iter <N> --replay`. Must match within ±1 pt. If not, evaluator drift or race — halt + investigate.

### 13. Reddit-specific: pullpush enrichment visibility
Plan 2 shipped pullpush.io enrichment that triggers when comment_divergence_pct ≥ 20. After every loop, check whether expected-to-have-removed-comments URLs actually recovered content:
```bash
python -c "
import json
from pathlib import Path
for d in sorted(Path('docs/summary_eval/reddit').glob('iter-*')):
    s_path = d / 'summary.json'
    if not s_path.exists(): continue
    s = json.loads(s_path.read_text(encoding='utf-8'))
    for entry in (s if isinstance(s, list) else [{'response': s}]):
        meta = entry.get('response', {}).get('summary', {}).get('metadata', {}) or {}
        div = meta.get('comment_divergence_pct', 0)
        fetched = meta.get('pullpush_fetched', 0)
        print(f'{d.name}: divergence={div}% pullpush_fetched={fetched}')
"
```
If an iteration shows `divergence > 20%` but `pullpush_fetched == 0`, the enrichment silently failed — investigate before next iter.

### 14. Reddit-specific: single held-out URL caveat
links.txt `# Reddit` has 4 URLs total → 3 used for training/cross-URL (loops 1-5) + 1 held-out (loops 6-7). Statistical power on held-out is LOW; a single held-out failure defines the entire aggregate. Record this caveat in final_scorecard.md. If the single held-out fails loop-6 thresholds, strongly consider extension loops 8-9 rather than accepting `degraded` immediately.

---

## URL allocation (from links.txt `# Reddit` section)

4 URLs present in links.txt:
1. `r/IndianStockMarket/...rajkot_collapsed_hyundai_ipo` — training URL (#1), used in loops 1, 2, 3, 5
2. `r/IAmA/...9ke63/i_did_heroin_yesterday` — cross-URL #2, used in loops 4, 5. Has removed comments → exercises pullpush enrichment.
3. `r/IAmA/...9ohdc/2_weeks_ago_i_tried_heroin_once` — cross-URL #3, used in loop 5. Also has removed comments.
4. `r/hinduism/...a_lifelong_atheist_turning_to_hindu_spirituality` — held-out, used in loops 6, 7.

Since there's only 1 held-out URL, loop 6's aggregate is a single-URL score; loop 7 prod-parity exercises the same single URL with `SUMMARIZE_ENV=prod-parity`.

---

## File structure summary

### Created per loop
`docs/summary_eval/reddit/iter-NN/` with `input.json`, `summary.json`, `eval.json`, `manual_review_prompt.md`, `manual_review.md`, `diff.md`, `next_actions.md`, `run.log`.

### Codex-allowed edit surfaces in tuning loops
- `website/features/summarization_engine/summarization/reddit/{prompts,schema,summarizer}.py`
- `website/features/summarization_engine/summarization/common/*.py` (flag cross-cutting in commit)
- `website/features/summarization_engine/source_ingest/reddit/{ingest,pullpush}.py`
- `website/features/summarization_engine/config.yaml` (`sources.reddit.*`, `structured_extract.*`)
- `docs/summary_eval/_config/rubric_reddit.yaml` (misspecification fixes only)

### Off-limits
- `website/features/summarization_engine/evaluator/**`
- `telegram_bot/**`, `website/api/routes.py`
- Other sources' summarizers/schemas/prompts

### Final output
- `docs/summary_eval/reddit/final_scorecard.md`

---

## Task 0: Preflight

- [ ] **Step 1: Checkout the Plan 2 branch**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git fetch origin
git checkout eval/summary-engine-v2-scoring-reddit
git pull
```

- [ ] **Step 2: Confirm prerequisites**

```bash
test -f docs/summary_eval/_config/rubric_reddit.yaml && echo "rubric OK"
test -f docs/summary_eval/reddit/phase0.5-ingest/decision.md && echo "phase0.5 OK"
python -c "from website.features.summarization_engine.summarization.reddit.summarizer import RedditSummarizer; print('import OK')"
python -c "from website.features.summarization_engine.source_ingest.reddit.pullpush import recover_removed_comments; print('pullpush OK')"
```

- [ ] **Step 3: List URLs**

```bash
python ops/scripts/eval_loop.py --source reddit --list-urls
```
Expected: 4 URLs.

- [ ] **Step 4: Start server**

```bash
python run.py &
sleep 5
curl -s http://127.0.0.1:10000/api/health
```
Expected: 200.

- [ ] **Step 5: Clean prior iteration artifacts (if any)**

```bash
rm -rf docs/summary_eval/reddit/iter-*
```

---

## Task 0.6: Pre-loop correctness gate (schema-routing smoke)

Prove Reddit summarizer routes through `RedditStructuredPayload`. If this gate fails, STOP — iter-01 would score garbage.

- [ ] **Step 1: In-process route smoke + shape assertions**

```bash
python - <<'PY'
import asyncio, re, sys
from uuid import UUID
from pathlib import Path
from website.features.summarization_engine.core.orchestrator import summarize_url
from website.features.summarization_engine.api.routes import _gemini_client
from ops.scripts.lib.links_parser import parse_links_file

by_source = parse_links_file(Path("docs/testing/links.txt"))
urls = by_source.get("reddit") or []
assert urls, "no reddit URLs in links.txt"
url = urls[0]
client = _gemini_client()
USER = UUID("00000000-0000-0000-0000-000000000001")
res = asyncio.run(summarize_url(url, user_id=USER, gemini_client=client))
r = res.model_dump(mode="json")
md = r.get("metadata", {}) or {}
sp = md.get("structured_payload") or {}
errs = []
if md.get("is_schema_fallback"): errs.append("is_schema_fallback=True")
if "_schema_fallback_" in (r.get("tags") or []): errs.append("_schema_fallback_ tag present")
for bp in ("zettelkasten","summary","capture","research","notes"):
    if bp in (r.get("tags") or []): errs.append(f"boilerplate tag: {bp}")
mt = r.get("mini_title","") or ""
if not re.match(r"^r/[A-Za-z0-9_]+", mt): errs.append(f"mini_title missing r/SUB prefix: {mt!r}")
ds = sp.get("detailed_summary") or {}
for req in ("op_intent","reply_clusters","counterarguments","unresolved_questions"):
    if req not in ds: errs.append(f"missing Reddit field: detailed_summary.{req}")
if not (ds.get("reply_clusters") or []): errs.append("reply_clusters empty")
if errs:
    print("GATE FAILED:", *errs, sep="\n - "); sys.exit(1)
print(f"GATE OK url={url}")
PY
```

- [ ] **Step 2: Evaluator-version gate**

```bash
python -c "from website.features.summarization_engine.evaluator.prompts import PROMPT_VERSION; assert PROMPT_VERSION=='evaluator.v3', PROMPT_VERSION; print('evaluator.v3 OK')"
```

---

## Task 1: Loop 1 — Baseline (URL #1)

- [ ] **Step 1: Phase A**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 1 --phase iter --manage-server
```
Expected: `status=awaiting_manual_review`.

- [ ] **Step 2: Write iter-01/manual_review.md**

Read `iter-01/manual_review_prompt.md` (contains rubric_reddit.yaml + summary.json + atomic_facts + source_text + eval.json SHA256). Do NOT open `iter-01/eval.json`.

Write:
```
eval_json_hash_at_review: "NOT_CONSULTED"

# Manual Review — Reddit iter-01 (baseline, URL #1)

## brief_summary (/25)
<per-criterion scoring, especially op_intent_captured, response_range, consensus_signal, caveats_surfaced, neutral_tone>

## detailed_summary (/45)
<per-criterion: reply_clusters, hedged_attribution, counterarguments_included, external_refs_captured, unresolved_questions, moderation_context, no_joke_chains>

## tags (/15)
<per-criterion: count_7_to_10, subreddit_present, thread_type, no_value_judgments, topical_specificity>

## label (/15)
<per-criterion: rsubreddit_prefix, central_issue, neutral>

## Anti-patterns
<comment_claim_asserted_as_fact, missing_removed_comment_note, editorialized_stance: triggered?>

## Most impactful improvement for iter-02
<one paragraph>

estimated_composite: NN.N
```

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 1 --phase iter
```
Commit: `test: reddit iter-01 score <N>→<N>`.

- [ ] **Step 4: Record baseline composite**

Open `docs/summary_eval/reddit/iter-01/eval.json`. Record baseline for comparison.

---

## Task 2: Loop 2 — First tune on URL #1

Typical iter-02 Reddit edits based on rubric_reddit.yaml criteria:
- `detailed.hedged_attribution` low → tighten `reddit/prompts.py` STRUCTURED_EXTRACT_INSTRUCTION: require every commenter claim to be prefixed with "commenters argue", "one user claims", "replies cluster around". Reinforce in SOURCE_CONTEXT.
- `detailed.moderation_context` 0 → add schema validator in `reddit/schema.py` that requires `moderation_context` be non-null whenever `IngestResult.metadata.comment_divergence_pct > 20`, OR add prompt instruction forcing the LLM to mention removed-comment count when divergence_pct was in the source.
- `label.rsubreddit_prefix` low → already enforced by schema regex; if still failing, the LLM may be returning the wrong format in early passes — tighten prompt to show an exact example.
- `detailed.no_joke_chains` low → add CoD densifier directive to drop joke-chain bullets.
- Anti-pattern `comment_claim_asserted_as_fact` triggered → sharpen summarizer's base-prompt to never allow unhedged attribution.

- [ ] **Step 1: Read iter-01 next_actions.md**

- [ ] **Step 2: Apply edits** (unbounded surface, targeted at iter-01's lowest criteria)

- [ ] **Step 3: pytest + commit**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "feat: reddit prompt require hedged attribution"
# (or however the edits shake out)
```

- [ ] **Step 4: Phase A**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 2 --phase iter --manage-server
```

- [ ] **Step 5: Write iter-02 manual_review.md**

- [ ] **Step 6: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 2 --phase iter
```

- [ ] **Step 7: Check delta**

Open `iter-02/diff.md`. If `score_delta_vs_prev >= +2`, continue. If delta < 0 by ≥ 5, revert + retry.

---

## Task 3: Loop 3 — Second tune on URL #1

- [ ] **Step 1-7: Same pattern as Task 2**

Focus iter-03 on mid-range criteria that iter-02 didn't fully resolve. Likely candidates:
- `brief.caveats_surfaced` — tune CoD densifier to preserve safety/risk/regional caveats as first-class bullets (important for the heroin-thread URLs where medical caveats matter)
- `detailed.external_refs_captured` — prompt to explicitly list cited links/data/examples as their own bullet
- `tags.subreddit_present` — make `r-<subreddit>` tag auto-injection a post-process step in `reddit/summarizer.py` using `IngestResult.metadata.subreddit`

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source reddit --iter 3 --phase iter --manage-server
# Codex writes iter-03/manual_review.md
python ops/scripts/eval_loop.py --source reddit --iter 3 --phase iter
```

---

## Task 4: Loop 4 — Cross-URL probe (URLs #1 + #2, no edits)

URL #2 is `r/IAmA/...heroin_yesterday` — the pullpush enrichment path should fire here (thread has removed comments per spec §7.2 decision).

- [ ] **Step 1: Phase A (URLs #1 + #2)**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 4 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-04 manual_review.md** — review both URLs, `estimated_composite` = mean.

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 4 --phase iter
```

- [ ] **Step 4: Verify pullpush worked on URL #2**

Open `docs/summary_eval/reddit/iter-04/summary.json`. Find URL #2's entry. Check:
- `response.summary.metadata.pullpush_fetched > 0` (recovered comments from archive)
- `response.summary.metadata.comment_divergence_pct > 20` (divergence triggered enrichment)

If pullpush_fetched is 0 on a URL expected to have removed comments: record as risk in `iter-04/next_actions.md` — the ingest chain may have regressed, investigate before iter-05.

- [ ] **Step 5: Measure overfitting gap**

(URL #1 iter-03 composite) - (URL #2 iter-04 composite) — note gap in iter-04 diff.md. If > 15, loop 5 must broaden the tune.

---

## Task 5: Loop 5 — Joint tune (URLs #1 + #2 + #3, convergence gate)

Both URL #2 and URL #3 are `r/IAmA` heroin threads. The tune must generalize across them. Expected per-source tune pattern: prompts and schema become less specific to financial-thread style (URL #1) and accommodate confessional/experiential content (URLs #2, #3).

- [ ] **Step 1: Read iter-04 diff.md + next_actions.md**

- [ ] **Step 2: Apply joint-broadening edits**

Target criteria failing on BOTH URL #2 and URL #3. Common issues:
- `detailed.caveats_surfaced` — personal-experience threads have strong implicit caveats ("do not try this", "my experience may differ") that need explicit capture
- `detailed.no_joke_chains` — r/IAmA threads attract derail/joke replies more than r/IndianStockMarket; tune the prompt to filter more aggressively
- `brief.neutral_tone` — medical/experiential subjects bait editorializing; tighten the stance-preservation clause

- [ ] **Step 3: pytest + commit + Phase A**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source reddit --iter 5 --phase iter --manage-server
```

- [ ] **Step 4: Write iter-05 manual_review.md**

3 URL sections. `estimated_composite` = mean of all 3.

- [ ] **Step 5: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 5 --phase iter
```

- [ ] **Step 6: Early-stop check**

Per spec §3.8:
- URL #1 composite ≥ 92 AND ragas_faithfulness ≥ 0.95 at iter-05?
- URLs #2, #3 each ≥ 88 at iter-05?
- ≥ 3 iters across 01–05 had URL #1 composite ≥ 92 AND ragas ≥ 0.95?

If all yes → CLI sets `status=converged` in iter-05/next_actions.md. Loops 6 and 7 still run (they are validation, not early-stop-eligible).

---

## Task 6: Loop 6 — Held-out validation (URL #4, no edits)

Only 1 held-out URL (r/hinduism). The aggregate IS that single URL.

- [ ] **Step 1: Phase A**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 6 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-06 manual_review.md** (single held-out URL)

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 6 --phase iter
```

- [ ] **Step 4: Check held-out thresholds**

Per spec §10.1:
- Held-out composite ≥ 88?
- Held-out ragas_faithfulness ≥ 0.95?

If both yes → proceed to loop 7. CLI sets `status=continue`.
If either fails → CLI sets `status=extension_required`. Skip to Task 8.

**Reddit-specific caveat:** With only 1 held-out URL, a single bad URL defines the whole held-out score. Recording this as "low held-out statistical power" is appropriate — the acceptance bar should remain at ≥ 88, but flag in `final_scorecard.md` that statistical confidence is lower than sources with more held-out URLs.

---

## Task 7: Loop 7 — Prod-parity (Zoro auth, SUMMARIZE_ENV=prod-parity)

Same Zoro auth pattern as Plan 6 Task 7.

- [ ] **Step 1: Export env + restart server**

```bash
kill %1 2>/dev/null
export SUMMARIZE_ENV=prod-parity
export SUPABASE_URL=https://icmnskseuoteyirljswd.supabase.co
# export SUPABASE_ANON_KEY=<private — from .env or secret manager>
python run.py &
sleep 5
curl -s http://127.0.0.1:10000/api/health
```

- [ ] **Step 2: Phase A with --env prod-parity**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 7 --phase iter --env prod-parity --manage-server
```

CLI fetches Zoro bearer token + writes `iter-07/prod_parity_auth.txt` + POSTs held-out URL with `write_to_supabase=true`.

- [ ] **Step 3: Write iter-07 manual_review.md**

- [ ] **Step 4: Phase B**

```bash
python ops/scripts/eval_loop.py --source reddit --iter 7 --phase iter --env prod-parity
```

- [ ] **Step 5: Check prod-parity delta**

`|iter-07 composite − iter-06 composite| ≤ 5` per spec §10.1. Record outcome.

- [ ] **Step 6: Verify Zoro KG write landed**

```bash
curl -s "https://icmnskseuoteyirljswd.supabase.co/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&source_type=eq.reddit&select=id,mini_title&order=created_at.desc&limit=3" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```

Expected: at least one Reddit node under Zoro's user_id matching the held-out URL's mini_title (should start with `r/hinduism `). Record node_id in `docs/summary_eval/reddit/iter-07/zoro_kg_verification.md`.

For RAG check: log in as Zoro, query a Reddit-specific question. Verify the new node surfaces.

- [ ] **Step 7: Reset env**

```bash
unset SUMMARIZE_ENV
kill %1
```

---

## Task 8: Loop 8 — Extension (conditional, only if loop 6 failed)

```bash
grep -q "^status: extension_required" docs/summary_eval/reddit/iter-06/next_actions.md && echo "EXTEND" || echo "SKIP"
```

If EXTEND:
- Read iter-06 aggregate + next_actions. Identify criterion failures.
- Apply joint re-tune targeting failed criteria. Churn protection applies.
- pytest + commit.
- Run Phase A + manual_review + Phase B.

```bash
python ops/scripts/eval_loop.py --source reddit --iter 8 --phase iter --manage-server
# Codex writes iter-08/manual_review.md
python ops/scripts/eval_loop.py --source reddit --iter 8 --phase iter
```

---

## Task 9: Loop 9 — Extension final (conditional, only if loop 8 ran)

```bash
python ops/scripts/eval_loop.py --source reddit --iter 9 --phase iter --manage-server
# Codex writes iter-09/manual_review.md (no edits — measurement only)
python ops/scripts/eval_loop.py --source reddit --iter 9 --phase iter
```

If iter-09 held-out still fails: Reddit marked `degraded` in final_scorecard.md with documented root-cause. Program continues to Plan 8.

---

## Task 10: Final scorecard + promote PR

- [ ] **Step 1: Write `docs/summary_eval/reddit/final_scorecard.md`**

```markdown
# Reddit — Final Scorecard

## Per-loop progression
| Loop | URLs | Composite (mean) | Faithfulness (min) | Notes |
|---|---|---|---|---|
| 1 (baseline) | #1 | <N> | <N> | baseline |
| 2 (tune) | #1 | <N> | <N> | <delta + which criteria moved> |
| 3 (tune) | #1 | <N> | <N> | <delta> |
| 4 (probe) | #1, #2 | <N> | <N> | overfitting gap = <N> |
| 5 (joint) | #1, #2, #3 | <N> | <N> | converged? yes/no |
| 6 (held-out) | #4 | <N> | <N> | single-URL held-out (low statistical power flagged) |
| 7 (prod-parity) | #4 | <N> | <N> | prod-parity delta = <N> |
| 8 (ext, if fired) | ... | ... | ... | ... |
| 9 (ext, if fired) | ... | ... | ... | ... |

## Acceptance (spec §10.1)
- [ ] Training URL composite ≥ 92 in ≥ 3 of last 5 tuning iters
- [ ] Training URL ragas_faithfulness ≥ 0.95
- [ ] URLs #1, #2, #3 each ≥ 88 at loop 5
- [ ] Held-out URL #4 composite ≥ 88 + ragas ≥ 0.95
- [ ] Prod-parity delta ≤ 5
- [ ] No hallucination cap triggered in loops 5-7
- [ ] `r/<subreddit>` label regex match in 100% of loop-6 runs

**Overall: PRODUCTION-GRADE | DEGRADED**

## Pullpush enrichment verification
- URLs expected to have removed comments: 2 (both r/IAmA heroin threads)
- URLs where `pullpush_fetched > 0`: <count>
- Recovered-comments helped `detailed.moderation_context` score: yes / no (cite iter N)

## Zoro prod-parity verification (loop 7)
- Reddit KG writes: <N> new nodes under user_id a57e1f2f-...
- RAG retrieval: <yes/no> — verified Reddit node surfaces in chat
- Reference: docs/summary_eval/reddit/iter-07/zoro_kg_verification.md

## Lessons (for Plan 10 cross_source_lessons.md)
- <bullets>

## Known risks
- Single held-out URL = low statistical power on held-out mean
- <other>
```

- [ ] **Step 2: Commit**

```bash
git add docs/summary_eval/reddit/final_scorecard.md
git commit -m "docs: reddit final scorecard"
```

- [ ] **Step 3: Push + promote draft PR**

```bash
git push origin eval/summary-engine-v2-scoring-reddit
gh pr ready <PR_NUMBER>
```

Update PR body to include the iteration-complete + deploy-gate blocks (same template as Plan 6 Task 10 Step 3).

- [ ] **Step 4: STOP. Wait for human approval.**

Report:
> Plan 7 complete. Draft PR #<N> promoted to ready. Final composite: <N>. Zoro verification: <status>. Reddit = production-grade / degraded. Awaiting human review + merge before Plan 8.

---

## Self-review checklist
- [ ] Every loop ran Phase A + manual_review + Phase B
- [ ] All manual_review.md files stamped `eval_json_hash_at_review: "NOT_CONSULTED"`
- [ ] Measurement loops (1, 4, 6, 7) had no code edits
- [ ] Pullpush enrichment verified working on r/IAmA threads
- [ ] Loop 7 attempted Zoro auth; verification or skip-reason recorded
- [ ] final_scorecard.md acceptance table filled
- [ ] PR body updated with deploy-gate
- [ ] NO merge/push-to-master
