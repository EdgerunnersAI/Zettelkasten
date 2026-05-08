# Summarization Engine Plan 8 — GitHub Iteration Loops 1-7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **RUNBOOK:** Execute commands strictly from `docs/summary_eval/RUNBOOK_CODEX.md` — it is the single source of truth for the two-phase state machine, manual_review.md template, per-iter URL allocation, recovery procedures, and the halt switch. The plan below is the goal spec; the runbook is how you actually run it.

**Goal:** Drive GitHub summarization quality to spec-§10.1 production-grade (composite ≥ 92 + ragas_faithfulness ≥ 0.95 on training URL; held-out mean ≥ 88; prod-parity delta ≤ 5) through the 7-loop runbook.

**Architecture:** Same two-phase loop runbook as Plans 6/7. Per-source focus: GitHub's rubric_github.yaml gates heavily on `label.owner_slash_repo` (exact `owner/repo` format), `brief.no_maturity_fabrication` (no "production-ready" claim without README evidence), `detailed.interfaces_exact` (API routes/CLI commands by exact name), and the `invented_public_interface` anti-pattern (auto-cap 60). Plan 3 shipped 5 additional REST API signals (pages/workflows/releases/languages/root-dir) + Gemini-Flash architecture overview; this plan tunes the summarizer to USE those signals in the rubric-required way.

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §3.2 (eval.json shape), §3.6 (composite formula), §3.7 (cross-model manual review), §3.8 (stop criterion), §4.1 (loop allocation), §8.2 (runbook), §8.3 (edit cycle + off-limits), §8.4 (churn protection), §8.5 (guardrails), §8.6 (reproducibility), §9.1-§9.5 (budget), §10.1 (acceptance).

**Branch:** `eval/summary-engine-v2-scoring-github` — same branch Plan 3 opened. Appends iteration commits to that draft PR.

**Precondition:** Plan 7 PR merged to master + prod deploy verified healthy. Branch exists. `# GitHub` section of links.txt has ≥ 3 URLs (user-added per spec §3.6 Option A, or auto-discovered via `ops/scripts/lib/url_discovery.py`).

**Deploy discipline:** Finishes with `gh pr ready` + human-review handoff. Codex does NOT merge. Plan 9 does not start until human confirms this PR's deploy health.

---

## Critical edge cases Codex MUST handle during every loop

Read this section before any loop. Every one of these is defined in the spec; failing to handle them breaks the program contract.

### 1. Blind-review enforcement
Every `manual_review.md` MUST start with `eval_json_hash_at_review: "NOT_CONSULTED"` as its first line. The CLI enforces this in Phase B; if absent or if it contains an actual hash, Phase B exits with `status=blind_review_violation` in `next_actions.md` and halts. Codex must NOT read `iter-NN/eval.json` while writing `manual_review.md` — only the prompt file (which contains rubric + summary + atomic_facts + source_text).

### 2. Determinism check (loop start)
CLI runs at every loop start (except iter-01): re-runs the evaluator on iter-(N-1)'s `summary.json`. If the new composite differs from the stored composite by > 2 pts, writes `status=evaluator_drift` to next_actions.md and halts. Means the evaluator prompt or rubric YAML was silently changed — Codex must investigate before resuming. Common cause: someone edited `evaluator/prompts.py` without bumping `PROMPT_VERSION`.

### 3. Churn protection (spec §8.4)
The CLI maintains `docs/summary_eval/github/edit_ledger.json` tracking every file edited per iteration + its targeted criterion + criterion movement. A file is flagged `churning` if edited in ≥ 3 consecutive tuning iterations AND its targeted criterion moved by < 1.0 pt combined. When flagged, `next_actions.md` prints `CHURN ALERT`. Codex then either:
- **Skips** the churning file this loop (recommended) OR
- **Writes a new_angle.md** at `docs/summary_eval/github/iter-<N>/new_angle.md` explaining the structural difference from prior edits (e.g., "previous 3 iters tuned wording; this iter changes prompt structure to two-turn"). Without new_angle.md, CLI refuses with `status=churn_unresolved`.

### 4. Rubric editing constraint (spec §8.3 step 4)
Edits to `docs/summary_eval/_config/rubric_github.yaml` are ALLOWED only to fix misspecifications; FORBIDDEN to soften grading. Concretely forbidden:
- Raising a criterion's `max_points` or `weight` above spec-doc baseline
- Lowering `hallucination_cap` (60), `omission_cap` (75), or `generic_cap` (90) thresholds
- Removing a criterion listed in the research doc
- Relaxing `criteria_fired` requirements (e.g., changing "must capture owner/repo format" to "optional")

Allowed: adding clarifying examples, typo fixes, splitting an ambiguous criterion into two sharper ones with combined weight equal to original. Every rubric edit commit must start with `docs: rubric fix github:` + 1-line rationale.

### 5. Off-limits files (spec §8.3 step 3)
Never edit inside a tuning loop:
- `website/features/summarization_engine/evaluator/**` (scoring code; if buggy, a separate "evaluator fix" commit lands outside iteration cycle and bumps `PROMPT_VERSION`)
- `telegram_bot/**` (entire legacy pipeline off-limits during this program)
- `website/api/routes.py` (endpoint signatures stable)
- Other sources' summarizer/schema/prompts (only GitHub surfaces touchable in this plan)
- `website/features/api_key_switching/**` (changes allowed only in Plan 1)
- `docs/summary_eval/github/iter-<N>/manual_review.md` — this is Codex's own output written once per iteration; never overwritten

### 6. Billing-spillover monitoring
The key pool auto-falls back free → billing when all free keys 429. Every `iter-NN/input.json` records `gemini_calls.role_breakdown.billing_calls` + `quota_exhausted_events`. After every loop, Codex checks:
```bash
python -c "
import json
from pathlib import Path
for d in sorted(Path('docs/summary_eval/github').glob('iter-*')):
    inp = json.loads((d / 'input.json').read_text(encoding='utf-8'))
    b = inp.get('gemini_calls', {}).get('role_breakdown', {}).get('billing_calls', 0)
    if b > 0:
        print(f'{d.name}: billing_calls={b}')
"
```
If any loop shows > 10 billing calls, pause and request human approval before continuing. The program's total billing budget is ~50 Pro calls worst case (per spec §9.6); significant overrun signals a misconfiguration.

### 7. Quota total-exhaustion (`status=quota_all_keys_exhausted`)
If all 3 Gemini keys 429 simultaneously, the CLI raises and writes this status. Wait for quota reset (next UTC midnight) or pause for human to add another key. Do NOT retry.

### 8. `.halt` kill switch
If `docs/summary_eval/.halt` file exists, CLI exits immediately on any invocation with `status=halted`. Human uses this to pause a run without killing Python. Codex honors it (no flag removes the file automatically).

### 9. Server restart after config changes
`--manage-server` (default on) restarts the FastAPI process at every loop start. Config.yaml edits require a full process restart (not hot-reload) because `load_config()` is lru_cached. Prompts.py / summarizer.py edits also require restart for clean import. The CLI handles this transparently — never omit `--manage-server`.

### 10. Manual-review composite estimation math
When writing `estimated_composite: NN.N` on the final line of manual_review.md, compute it via spec §3.6 formula:
```
base = 0.60 * rubric_total_of_100 + 0.20 * finesure_faithfulness * 100 + 0.10 * finesure_completeness * 100 + 0.10 * mean(g_eval_4_scores) * 20
then apply_caps: hallucination_cap(60) | omission_cap(75) | generic_cap(90) dominate the base
```
Estimate each component independently (rubric total from per-criterion scores, finesure dims from inspecting the summary vs atomic_facts, g_eval dims as subjective 0-5 ratings). The GitHub-specific anti-patterns (`production_ready_claim_no_evidence` → cap 60, `invented_public_interface` → cap 60, `label_not_owner_repo` → cap 75) must be explicitly checked.

### 11. Cross-model disagreement handling (spec §3.7)
After Phase B, CLI computes `divergence = |gemini_composite - codex_composite|`. Banner stamped at top of manual_review.md:
- ≤ 5: `AGREEMENT`
- 5 < … ≤ 10: `MINOR_DISAGREEMENT`
- > 10: `MAJOR_DISAGREEMENT`; both scores logged; LOWER of the two is the reference score the next tuning loop must beat (pessimistic rule).

After 2 consecutive MAJOR_DISAGREEMENT loops, write `docs/summary_eval/github/iter-<N>/disagreement_analysis.md` (one paragraph: hypothesis on why Gemini and Codex disagree, which model you trust more on this source, proposed tiebreaker). Not an automatic halt — an attention anchor.

### 12. Reproducibility (`--replay`)
If a loop score looks suspicious (sudden jump/drop > 10 pts without a corresponding large edit), run:
```bash
python ops/scripts/eval_loop.py --source github --iter <N> --replay
```
Re-runs the full loop from artifacts; composite must match within ±1 pt. If not, evaluator drift or a race condition.

---

## URL allocation

GitHub URLs must be user-added to links.txt `# GitHub` section before loop 1, or auto-discovered via Plan 1's `url_discovery.py` during Task 0. Assume 3 URLs minimum.

| Role | URL index |
|---|---|
| Training (#1) — loops 1, 2, 3, 5 | links.txt GitHub URL 1 |
| Cross-URL (#2) — loops 4, 5 | links.txt GitHub URL 2 |
| Cross-URL (#3) — loop 5 | links.txt GitHub URL 3 |
| Held-out (#4+) — loops 6, 7 | links.txt GitHub URL 4+ if present |

If only 3 URLs available, loop 6 runs against URL #1 (warm cache hit) as a low-power held-out. Flag in final_scorecard.md as "low held-out statistical power — consider adding more URLs for future rerun".

---

## Codex-allowed edit surfaces (GitHub-specific)
- `website/features/summarization_engine/summarization/github/{prompts,schema,summarizer}.py`
- `website/features/summarization_engine/summarization/common/*.py` (cross-cutting; flag in commit)
- `website/features/summarization_engine/source_ingest/github/{ingest,api_client,architecture}.py`
- `website/features/summarization_engine/config.yaml` (`sources.github.*`, `structured_extract.*`)
- `docs/summary_eval/_config/rubric_github.yaml` (misspecification fixes only, per Edge Case 4)

---

## Task 0: Preflight

- [ ] **Step 1: Checkout + pull**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git fetch origin
git checkout eval/summary-engine-v2-scoring-github
git pull
```

- [ ] **Step 2: Confirm prerequisites**

```bash
test -f docs/summary_eval/_config/rubric_github.yaml && echo "rubric OK"
test -f docs/summary_eval/github/phase0.5-ingest/decision.md && echo "phase0.5 OK"
python -c "from website.features.summarization_engine.summarization.github.summarizer import GitHubSummarizer; print('summarizer OK')"
python -c "from website.features.summarization_engine.source_ingest.github.api_client import GitHubApiClient; print('api_client OK')"
python -c "from website.features.summarization_engine.source_ingest.github.architecture import extract_architecture_overview; print('arch OK')"
```
Expected: 5 OK lines.

- [ ] **Step 3: Check URL count**

```bash
count=$(python ops/scripts/eval_loop.py --source github --list-urls | python -c "import sys,json; print(len(json.load(sys.stdin)))")
echo "GitHub URL count: $count"
```
If `< 3`: Codex must add URLs via one of:
- **A.** Ask the human to append 3+ GitHub URLs under `# GitHub` in `docs/testing/links.txt`.
- **B.** Auto-discover via Gemini google_search grounding:
  ```bash
  python -c "
  import asyncio, sys
  sys.path.insert(0, '.')
  from ops.scripts.lib.url_discovery import discover_urls, write_discovery_report
  from website.features.summarization_engine.api.routes import _gemini_client
  from pathlib import Path
  async def main():
      client = _gemini_client()
      urls = await discover_urls('github', client, count=3)
      out = Path('docs/summary_eval/github/auto_discovered_urls.md')
      out.parent.mkdir(parents=True, exist_ok=True)
      write_discovery_report('github', urls, out)
      # Append to links.txt
      p = Path('docs/testing/links.txt'); c = p.read_text(encoding='utf-8')
      import re
      new = '\n'.join(u.get('url','') for u in urls if u.get('url'))
      c = re.sub(r'(^# GitHub\s*\n(?:.*\n)*?)(?=^#|\Z)', lambda m: m.group(1).rstrip() + '\n' + new + '\n', c, count=1, flags=re.MULTILINE)
      p.write_text(c, encoding='utf-8')
  asyncio.run(main())
  "
  ```

- [ ] **Step 4: Start server**

```bash
python run.py &
sleep 5
curl -s http://127.0.0.1:10000/api/health
```

- [ ] **Step 5: Clean prior iteration artifacts**

```bash
rm -rf docs/summary_eval/github/iter-*
```

---

## Task 0.6: Pre-loop correctness gate (schema-routing smoke)

Prove GitHub summarizer routes through `GitHubStructuredPayload`. If this gate fails, STOP.

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
urls = by_source.get("github") or []
assert urls, "no github URLs in links.txt"
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
if not re.match(r"^[^/\s]+/[^/\s]+$", mt): errs.append(f"mini_title not owner/repo: {mt!r}")
for req in ("architecture_overview",):
    if req not in sp: errs.append(f"missing GitHub field: {req}")
ds = sp.get("detailed_summary") or []
if not (isinstance(ds, list) and ds and ds[0].get("heading")): errs.append("detailed_summary must be non-empty list of {heading,bullets,...}")
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
python ops/scripts/eval_loop.py --source github --iter 1 --phase iter --manage-server
```
Expected: `status=awaiting_manual_review path=docs/summary_eval/github/iter-01/manual_review_prompt.md`

- [ ] **Step 2: Write iter-01/manual_review.md**

Read `manual_review_prompt.md` (rubric_github.yaml + summary.json + atomic_facts + source_text). Do NOT open `eval.json`.

```
eval_json_hash_at_review: "NOT_CONSULTED"

# Manual Review — GitHub iter-01 (baseline, URL #1)

## brief_summary (/25)
Per rubric_github.yaml components[0].criteria:
- brief.user_facing_purpose (/6): <prose, score>
- brief.architecture_high_level (/5): <prose, score>
- brief.languages_and_frameworks (/4): <prose, score>
- brief.usage_pattern (/4): <prose, score>
- brief.public_surface (/4): <prose, score>
- brief.no_maturity_fabrication (/2): <prose, score>
Subtotal: <N>/25

## detailed_summary (/45)
Per rubric components[1].criteria:
- detailed.features_bullets (/8): <prose, score>
- detailed.architecture_modules (/8): <prose, score>
- detailed.interfaces_exact (/8): <prose, score>
- detailed.operational (/6): <prose, score>
- detailed.limitations_docs (/5): <prose, score>
- detailed.benchmarks_tests_examples (/5): <prose, score>
- detailed.bullets_focused (/5): <prose, score>
Subtotal: <N>/45

## tags (/15)
- tags.count_7_to_10 (/2): <score>
- tags.domain_tag (/3): <score>
- tags.languages (/3): <score>
- tags.technical_concepts (/3): <score>
- tags.no_unsupported_claims (/4): <score> — flag if summary claims "production-ready" without README evidence
Subtotal: <N>/15

## label (/15)
- label.owner_slash_repo (/10): must exactly match `^[^/]+/[^/]+$`
- label.no_extra_descriptors (/5)
Subtotal: <N>/15

## Anti-patterns explicit check
- `production_ready_claim_no_evidence` (auto_cap=60): <triggered? yes/no, where in summary>
- `invented_public_interface` (auto_cap=60): <triggered? yes/no, which invented interface>
- `label_not_owner_repo` (auto_cap=75): <triggered? yes/no>

## Editorialization check (global rule)
Review summary sentences for stance/judgment/framing absent from README. Count flags. If ≥ 3, hallucination_cap=60 applies (global rule in rubric_github.yaml).
Count: <N>

## FineSurE dimension estimates (subjective)
- faithfulness (0-1): <N> — fraction of summary claims grounded in source
- completeness (0-1): <N> — fraction of atomic_facts (importance ≥ 3) covered
- conciseness (0-1): <N> — fraction of summary sentences that carry a keyfact

## G-Eval dimension estimates (0-5 each)
- coherence, consistency, fluency, relevance

## Composite computation
base = 0.60 * <rubric_total> + 0.20 * <faith> * 100 + 0.10 * <comp> * 100 + 0.10 * <mean_g_eval> * 20
cap_applied = <hallucination 60 | omission 75 | generic 90 | none>
composite = min(base, cap) if cap else base

## Most impactful improvement for iter-02
<one paragraph>

estimated_composite: NN.N
```

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 1 --phase iter
```

- [ ] **Step 4: Record baseline composite + check billing**

```bash
python -c "
import json
e = json.loads(open('docs/summary_eval/github/iter-01/eval.json').read())
if isinstance(e, list): e = e[0]
print('baseline_composite:', e.get('composite_score_cached', 'see eval'))
i = json.loads(open('docs/summary_eval/github/iter-01/input.json').read())
print('billing_calls:', i.get('gemini_calls', {}).get('role_breakdown', {}).get('billing_calls', 0))
"
```

---

## Task 2: Loop 2 — First tune on URL #1

Typical iter-02 GitHub edits based on likely-failing iter-01 criteria:
- `label.owner_slash_repo` low → verify `GitHubStructuredPayload.mini_title` regex is `^[^/]+/[^/]+$` (already in Plan 1 Task 6); if LLM still returns wrong format, tighten prompt to show an exact example and add a post-validation coercion in `github/summarizer.py`.
- `brief.no_maturity_fabrication` low (or anti-pattern `production_ready_claim_no_evidence` fired) → add explicit clause to `github/prompts.py` SOURCE_CONTEXT: "Never claim 'production-ready', 'battle-tested', 'stable', 'widely used' unless the README explicitly says so."
- `detailed.interfaces_exact` low (or anti-pattern `invented_public_interface` fired) → tune CoD + structured-extract prompt to require exact-name citation: "For every public interface listed, cite the exact function signature or route path from the source."
- `detailed.architecture_modules` low → ensure `GitHubStructuredPayload.architecture_overview` is populated and the detailed_summary bullets reference modules by name — tighten schema validator.
- `detailed.benchmarks_tests_examples` low → the Plan-3 root-dir scan gives us `has_benchmarks/has_tests/has_examples` booleans. Tune prompt to populate this field only when the directory exists per ingest metadata.

- [ ] **Step 1: Read iter-01/next_actions.md** (CLI-synthesized ranked edits)
- [ ] **Step 2: Apply edits** targeting lowest-scoring iter-01 criteria. Unbounded surface per spec §8.3.
- [ ] **Step 3: pytest**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
```

- [ ] **Step 4: Commit with descriptive tags** (multiple commits allowed)

```bash
git commit -m "feat: github prompt enforce owner slash repo label"
git commit -m "feat: github prompt forbid production ready unsupported"
# etc.
```

- [ ] **Step 5: Phase A**

```bash
python ops/scripts/eval_loop.py --source github --iter 2 --phase iter --manage-server
```

- [ ] **Step 6: Write iter-02/manual_review.md** (same template as Task 1)

- [ ] **Step 7: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 2 --phase iter
```

- [ ] **Step 8: Delta check + edge-case banners**

Open iter-02/diff.md. Assert `score_delta_vs_prev > 0`. If score dropped ≥ 5, revert edits.

Check for edge-case banners in iter-02/next_actions.md:
- `CHURN ALERT` on any file (irrelevant at iter-02, cannot fire until iter-04 at earliest)
- `MAJOR_DISAGREEMENT` banner (divergence > 10) → record in manual_review.md
- `status=evaluator_drift` → investigate (edge case 2)
- `status=blind_review_violation` → rewrite manual_review.md without reading eval.json

---

## Task 3: Loop 3 — Second tune on URL #1

Focus on mid-range criteria after iter-02. Typical:
- `detailed.operational` — tune prompt to include install steps, env vars, deploy instructions when README covers them
- `tags.technical_concepts` — inject auto-tag logic using `IngestResult.metadata.languages` top-3 + root-dir flags
- Anti-pattern `invented_public_interface` lingering → add a post-validation check in `github/summarizer.py` that every entry in `detailed_summary[].public_interfaces` appears verbatim in `ingest.raw_text` (case-insensitive substring)

- [ ] **Step 1-8: Same pattern as Task 2** (read next_actions → edit → pytest → commit → Phase A → manual_review → Phase B → delta check)

```bash
python ops/scripts/eval_loop.py --source github --iter 3 --phase iter --manage-server
# Codex writes iter-03/manual_review.md
python ops/scripts/eval_loop.py --source github --iter 3 --phase iter
```

---

## Task 4: Loop 4 — Cross-URL probe (URLs #1 + #2, measurement only)

- [ ] **Step 1: Phase A (runs URLs #1 + #2)**

```bash
python ops/scripts/eval_loop.py --source github --iter 4 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-04/manual_review.md**

Two URL sections. Per-URL composite. `estimated_composite` = mean.

Key GitHub consideration: URL #2 may be a different repo archetype than URL #1 (e.g., training was a Python library, #2 is a TypeScript web app). Criteria like `brief.languages_and_frameworks` and `tags.languages` will exercise different code paths — this is the whole point of cross-URL probing.

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 4 --phase iter
```

- [ ] **Step 4: Overfitting measurement**

Gap = (URL #1 iter-03 composite) − (URL #2 iter-04 composite). Record. If > 15, loop 5 tune must broaden source-type handling.

---

## Task 5: Loop 5 — Joint tune (URLs #1 + #2 + #3)

Convergence gate per spec §3.8.

- [ ] **Step 1: Read iter-04 next_actions.md + diff.md**
- [ ] **Step 2: Apply joint-broadening edits**

Focus on making prompts source-archetype-agnostic:
- If URL #1 and URL #2 have different primary languages, tune prompts to handle "multi-language repos" and "single-language repos" without over-fitting to one.
- Monorepo vs single-purpose detection: if `root_dir_flags.has_docs_dir + has_examples + has_demo` all true, it's likely a monorepo — spec §6.1 says label should stay the root slug, but brief_summary should call out sub-projects.

- [ ] **Step 3: pytest + commit + Phase A**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source github --iter 5 --phase iter --manage-server
```

- [ ] **Step 4: Write iter-05/manual_review.md** (3 URL sections, mean composite)

- [ ] **Step 5: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 5 --phase iter
```

- [ ] **Step 6: Early-stop check (spec §3.8)**

- URL #1 composite ≥ 92 AND ragas_faithfulness ≥ 0.95 at iter-05: yes/no
- URLs #1, #2, #3 each ≥ 88 at iter-05: yes/no
- ≥ 3 of last 5 tuning iters (iter-01 through iter-05) had URL #1 composite ≥ 92 AND ragas ≥ 0.95: yes/no

All yes → CLI sets `status=converged`. Loops 6 + 7 still run (validation, not early-stop-eligible).

- [ ] **Step 7: Churn ledger review**

```bash
cat docs/summary_eval/github/edit_ledger.json | python -m json.tool | tail -40
```
Note any `churn_flags.<file>.status=churning`. In Task 8 (extension), if these churning files remain the critical path, write new_angle.md (edge case 3).

---

## Task 6: Loop 6 — Held-out validation (all remaining URLs, no edits)

- [ ] **Step 1: Phase A**

```bash
python ops/scripts/eval_loop.py --source github --iter 6 --phase iter --manage-server
```
CLI runs all URLs beyond #1-#3.

- [ ] **Step 2: Write iter-06/manual_review.md**

One section per held-out URL. Mean composite = estimated_composite.

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 6 --phase iter
```

- [ ] **Step 4: Check thresholds (spec §10.1)**

- Held-out mean composite ≥ 88: yes/no
- Min ragas_faithfulness across held-out ≥ 0.95: yes/no

Both yes → `status=continue`, proceed to loop 7.
Either no → `status=extension_required`, skip to Task 8.

---

## Task 7: Loop 7 — Prod-parity + Zoro (SUMMARIZE_ENV=prod-parity)

- [ ] **Step 1: Export prod-parity env**

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
python ops/scripts/eval_loop.py --source github --iter 7 --phase iter --env prod-parity --manage-server
```
CLI fetches Zoro bearer token from creds in `docs/login_details.txt`, POSTs held-out URLs with `write_to_supabase=true`, writes `iter-07/prod_parity_auth.txt` recording `user_id=a57e1f2f-...`.

- [ ] **Step 3: Write iter-07/manual_review.md**

- [ ] **Step 4: Phase B**

```bash
python ops/scripts/eval_loop.py --source github --iter 7 --phase iter --env prod-parity
```

- [ ] **Step 5: Prod-parity delta check (spec §10.1)**

`|iter-07 mean composite − iter-06 mean composite| ≤ 5`. Record.

- [ ] **Step 6: Verify Zoro KG + RAG end-to-end**

```bash
curl -s "https://icmnskseuoteyirljswd.supabase.co/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&source_type=eq.github&select=id,mini_title,created_at&order=created_at.desc&limit=5" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```
Expected: GitHub nodes matching held-out URLs' `owner/repo` mini_titles. Record node_ids in `docs/summary_eval/github/iter-07/zoro_kg_verification.md`.

RAG check: log in as Zoro on `https://zettelkasten.in/chat`, query a question about one of the newly-summarized repos. Verify RAG response cites the new node. Record outcome.

- [ ] **Step 7: Reset env**

```bash
unset SUMMARIZE_ENV
kill %1
```

---

## Task 8: Loop 8 — Extension (conditional)

```bash
grep -q "^status: extension_required" docs/summary_eval/github/iter-06/next_actions.md && echo "EXTEND" || echo "SKIP"
```

If EXTEND:
- Read iter-06 aggregate + failed held-out URLs' eval.json details.
- Apply joint re-tune targeting the specific failed criteria.
- CHURN ALERT may fire if prior edits targeted same files — write `iter-08/new_angle.md` if needed (edge case 3).
- pytest + commit + Phase A + manual_review + Phase B.

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source github --iter 8 --phase iter --manage-server
# Codex writes iter-08/manual_review.md
python ops/scripts/eval_loop.py --source github --iter 8 --phase iter
```

---

## Task 9: Loop 9 — Extension final (conditional)

Only if Task 8 ran.

```bash
python ops/scripts/eval_loop.py --source github --iter 9 --phase iter --manage-server
# Codex writes iter-09/manual_review.md (no edits — measurement only)
python ops/scripts/eval_loop.py --source github --iter 9 --phase iter
```

If still failing: GitHub marked `degraded` in final_scorecard.md with documented root-cause.

---

## Task 10: Final scorecard + PR promotion

- [ ] **Step 1: Write `docs/summary_eval/github/final_scorecard.md`**

```markdown
# GitHub — Final Scorecard

## Per-loop progression
| Loop | URLs | Composite (mean) | Faithfulness (min) | Status | Edits committed |
|---|---|---|---|---|---|
| 1 (baseline) | #1 | <N> | <N> | baseline | — |
| 2 (tune) | #1 | <N> | <N> | <Δ> | <shas> |
| 3 (tune) | #1 | <N> | <N> | <Δ> | <shas> |
| 4 (probe) | #1,#2 | <N> | <N> | gap=<N> | — |
| 5 (joint) | #1,#2,#3 | <N> | <N> | converged? <y/n> | <shas> |
| 6 (held-out) | #4+ | <N> | <N> | thresholds met? <y/n> | — |
| 7 (prod-parity) | #4+ | <N> | <N> | delta≤5? <y/n> | — |
| 8 (ext) | ... | ... | ... | ... | <shas if fired> |
| 9 (ext) | ... | ... | ... | ... | — |

## Acceptance (spec §10.1)
- [ ] Training URL #1 composite ≥ 92 in ≥ 3 of last 5 tuning iters
- [ ] Training URL #1 ragas_faithfulness ≥ 0.95
- [ ] URLs #1, #2, #3 each ≥ 88 at loop 5
- [ ] Held-out mean ≥ 88 AND min ragas_faithfulness ≥ 0.95 (loop 6)
- [ ] Prod-parity delta ≤ 5 (loop 7)
- [ ] No hallucination cap triggered in loops 5-7
- [ ] `owner/repo` label regex match in 100% of loop-6 held-out runs
- [ ] Anti-pattern `production_ready_claim_no_evidence` never triggered in loops 5-7
- [ ] Anti-pattern `invented_public_interface` never triggered in loops 5-7

**Overall: PRODUCTION-GRADE | DEGRADED with reason: <...>**

## Signal utilization (Plan 3 Phase 0.5 signals put to use)
- `pages_url`: surfaced in summary when present? <y/n, iter N>
- `has_workflows` + workflow_count: surfaced in `usability_signals`? <y/n>
- `releases`: surfaced in brief_summary? <y/n>
- `languages` top-3 auto-injected into tags? <y/n>
- `has_benchmarks/has_tests/has_examples` populated `benchmarks_tests_examples`? <y/n>
- `architecture_overview` from Gemini Flash cached + used in brief_summary? <y/n>

## Cross-model disagreement summary
- Loops with `AGREEMENT`: <N>/<total>
- Loops with `MAJOR_DISAGREEMENT`: <N>/<total>
- `disagreement_analysis.md` written? (required after 2 consecutive MAJOR) <y/n, which iter>

## Billing spend
- Total billing_calls across iter-01 through iter-07: <N>
- Loops where billing fired: <list>
- Within spec §9.6 ceiling? <y/n>

## Zoro prod-parity verification (loop 7)
- GitHub KG writes: <N> new nodes under user_id a57e1f2f-...
- RAG retrieval: <y/n>
- Reference: docs/summary_eval/github/iter-07/zoro_kg_verification.md

## Lessons (for Plan 10 cross_source_lessons.md)
- <3-5 bullets>

## Known risks / follow-ups
- <bullets>
```

- [ ] **Step 2: Commit scorecard**

```bash
git add docs/summary_eval/github/final_scorecard.md
git commit -m "docs: github final scorecard"
```

- [ ] **Step 3: Push + promote draft PR**

```bash
git push origin eval/summary-engine-v2-scoring-github
gh pr ready <PR_NUMBER>
```

Update PR body with iteration-complete + deploy-gate block:
```markdown

## Iteration loops complete (Plan 8)
- Final composite (held-out mean): <N>
- Final ragas_faithfulness (held-out min): <N>
- Prod-parity delta: <N>
- Zoro prod-parity verified: <yes/no + node count>
- Status: **production-grade** | **degraded** with <reason>

### Deploy gate
Merging this PR triggers a production deploy. Verify:
- [ ] CI green
- [ ] final_scorecard.md status is production-grade OR documented degraded
- [ ] Zoro KG writes verified
- [ ] Billing spend within spec §9.6 ceiling
- [ ] No secrets in diff

Do NOT merge Plan 9 (Newsletter) before this PR's deploy is verified healthy.
```

- [ ] **Step 4: STOP + handoff**

Report:
> Plan 8 complete. Draft PR #<N> promoted to ready. Final held-out composite: <N>. Zoro verification: <status>. Status: <production-grade | degraded>. Will not proceed to Plan 9 until you confirm deploy health.

---

## Self-review checklist
- [ ] All edge cases from "Critical edge cases" section observed (blind review, determinism, churn, off-limits, billing, quota, halt, server restart, composite math, disagreement, replay)
- [ ] Every loop has Phase A + manual_review + Phase B
- [ ] All manual_review.md files stamped `eval_json_hash_at_review: "NOT_CONSULTED"`
- [ ] Manual_review.md composite math matches spec §3.6 (0.60/0.20/0.10/0.10 weights + cap dominance)
- [ ] Measurement loops (1, 4, 6, 7) had no code edits
- [ ] Tuning loops (2, 3, 5, 8) have descriptive commits per CLAUDE.md tag rules
- [ ] Rubric edits (if any) are misspecification-only, committed with `docs: rubric fix github:`
- [ ] Churn flags addressed with new_angle.md where applicable
- [ ] Loop 7 attempted Zoro auth; verification or skip-reason recorded
- [ ] Billing spend monitored per loop; totals within spec ceiling
- [ ] final_scorecard.md acceptance table filled
- [ ] PR body updated with deploy-gate
- [ ] NO merge, NO push to master
