# Summarization Engine Plan 9 — Newsletter Iteration Loops 1-7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **RUNBOOK:** Execute commands strictly from `docs/summary_eval/RUNBOOK_CODEX.md` — it is the single source of truth for the two-phase state machine, manual_review.md template, per-iter URL allocation, recovery procedures, and the halt switch. The plan below is the goal spec; the runbook is how you actually run it.

**Goal:** Drive Newsletter summarization quality to spec-§10.1 production-grade (composite ≥ 92 + ragas_faithfulness ≥ 0.95 on training URL; held-out mean ≥ 88; prod-parity delta ≤ 5) through the 7-loop runbook.

**Architecture:** Same two-phase loop runbook as Plans 6-8. Per-source focus: Newsletter's rubric_newsletter.yaml emphasizes `brief.main_topic_thesis` (publication identity + central thesis in one sentence), `brief.conclusions_distinct` (separate conclusions from descriptive background), `brief.stance_preserved` (no editorializing), `label.branded_source_rule` (C2 hybrid: branded sources require publication name, others thesis-only), and anti-patterns `stance_mismatch` (auto_cap 60), `invented_number` (auto_cap 60), `branded_source_missing_publication` (auto_cap 90). Plan 4 shipped site-specific DOM extractors (Substack/Beehiiv/Medium), preheader + CTA + conclusions detectors, and a Gemini-Flash stance classifier; this plan tunes the summarizer to USE those signals as rubric requires.

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md` §3.2, §3.6, §3.7, §3.8, §4.1, §8.2-§8.6, §10.1

**Branch:** `eval/summary-engine-v2-scoring-newsletter` — same branch Plan 4 opened. Appends iteration commits.

**Precondition:** Plan 8 PR merged to master + prod deploy verified healthy. Branch exists. `# Newsletter` section of links.txt has ≥ 3 URLs (user-added or auto-discovered).

**Deploy discipline:** Finishes with `gh pr ready` + human-review handoff. Codex does NOT merge. Plan 10 (polish) does not start until human confirms this PR's deploy health.

---

## Critical edge cases Codex MUST handle during every loop

Read this section before any loop. Every item is enforced by the CLI or required by the spec.

### 1. Blind-review enforcement (spec §3.7)
`manual_review.md` MUST start with `eval_json_hash_at_review: "NOT_CONSULTED"`. CLI Phase B halts with `status=blind_review_violation` otherwise. NEVER open `iter-NN/eval.json` while writing the review — only the prompt file.

### 2. Determinism check (spec §8.2 runbook step 3)
CLI re-runs evaluator on iter-(N-1)'s `summary.json` at every loop start (except iter-01). If composite drifts > 2 pts, halts with `status=evaluator_drift`. Investigate before resuming: usually means `evaluator/prompts.py` or `rubric_newsletter.yaml` was silently edited without bumping PROMPT_VERSION / rubric_version.

### 3. Churn protection (spec §8.4)
Edit ledger at `docs/summary_eval/newsletter/edit_ledger.json` tracks file edits per iteration. A file edited in ≥ 3 consecutive tuning iters whose targeted criterion moved < 1.0 pt combined is flagged `churning`. `next_actions.md` prints `CHURN ALERT`. Codex either:
- Skips the file this loop, OR
- Writes `docs/summary_eval/newsletter/iter-<N>/new_angle.md` explaining the structurally-different approach (e.g., "iter 02/03/05 tuned the CTA extraction regex; this iter replaces regex with a heading-distance heuristic"). Without new_angle.md, CLI refuses with `status=churn_unresolved`.

### 4. Rubric editing constraint (spec §8.3 step 4)
`docs/summary_eval/_config/rubric_newsletter.yaml` edits: misspecifications only, never grading softening. Commits must start `docs: rubric fix newsletter:` + 1-line rationale. Forbidden:
- Raising max_points / weights above spec baseline
- Lowering hallucination_cap (60), omission_cap (75), generic_cap (90)
- Removing criteria
- Relaxing criteria_fired requirements

### 5. Off-limits files (spec §8.3 step 3)
Never edit during iteration loops:
- `website/features/summarization_engine/evaluator/**`
- `telegram_bot/**`
- `website/api/routes.py`
- Other sources' summarizer/schema/prompts (only Newsletter surfaces touchable here)
- `website/features/api_key_switching/**`
- Own `manual_review.md` after Phase B commit

### 6. Billing-spillover monitoring
Check `iter-NN/input.json → gemini_calls.role_breakdown.billing_calls` after every loop. Pause + request human approval if any loop fires > 10 billing calls. Program total budget is ~50 Pro calls worst case per spec §9.6.

### 7. Quota total-exhaustion (`status=quota_all_keys_exhausted`)
All 3 keys 429'd. Wait for UTC midnight quota reset or pause for human to add another key.

### 8. `.halt` kill switch
`docs/summary_eval/.halt` file present = CLI exits with `status=halted`. Honor it.

### 9. Server restart after config changes
`--manage-server` (default on) restarts FastAPI each loop. Required for `config.yaml` changes (lru_cached loader) and per-source prompt/schema edits. Never omit.

### 10. Manual-review composite math (spec §3.6)
Final line of manual_review.md: `estimated_composite: NN.N` computed as:
```
base = 0.60 * rubric_total_of_100 + 0.20 * finesure.faithfulness * 100
     + 0.10 * finesure.completeness * 100
     + 0.10 * mean(g_eval_4) * 20
composite = apply_caps(base, rubric.caps_applied)  # hallucination 60 | omission 75 | generic 90
```
Newsletter-specific caps to watch:
- `stance_mismatch` fires → hallucination_cap=60 (summary's implied stance differs from source stance)
- `invented_number` fires → hallucination_cap=60 (summary cites a number/date not in source)
- `branded_source_missing_publication` fires → generic_cap=90 (branded-source label omits publication name)

### 11. Cross-model disagreement (spec §3.7)
After Phase B: divergence = |gemini − codex|. Stamp `AGREEMENT` (≤5), `MINOR_DISAGREEMENT` (5-10), or `MAJOR_DISAGREEMENT` (>10). Pessimistic rule: LOWER of the two is the score to beat. After 2 consecutive MAJORs, write `iter-<N>/disagreement_analysis.md` (one paragraph).

### 12. Reproducibility (`--replay`)
Sudden composite jumps/drops (> 10 pts without large edit) → `python ops/scripts/eval_loop.py --source newsletter --iter <N> --replay` must reproduce within ±1 pt.

### 13. Newsletter-specific: stance consistency across iterations
The stance classifier in `source_ingest/newsletter/stance.py` caches per-URL for 30 days (`stance_cache_ttl_days` in config.yaml). If loop-to-loop a URL's `detected_stance` changes, the cache was invalidated — check if `stance.py PROMPT_VERSION` constant was bumped or the cache TTL expired. Unexpected changes can shift `stance_preserved` rubric scores.

### 14. Newsletter-specific: branded sources YAML
`docs/summary_eval/_config/branded_newsletter_sources.yaml` may be extended during iteration loops. Adding a publication name to the branded list is a behavior change — Codex commits with `docs: branded sources add <name>` + rationale. Shrinking the list is forbidden during iteration (would soften `branded_source_missing_publication` anti-pattern triggering).

---

## URL allocation

Newsletter URLs must be in links.txt `# Newsletter` section before loop 1. Assume 3+ URLs minimum.

| Role | URL index |
|---|---|
| Training (#1) — loops 1, 2, 3, 5 | links.txt Newsletter URL 1 (ideally a branded source like Stratechery/Platformer to exercise C2 hybrid label) |
| Cross-URL (#2) — loops 4, 5 | links.txt Newsletter URL 2 (ideally a non-branded source to exercise thesis-only label) |
| Cross-URL (#3) — loop 5 | links.txt Newsletter URL 3 (ideally one with paywall to exercise paywall-fallback chain) |
| Held-out (#4+) — loops 6, 7 | links.txt Newsletter URL 4+ |

Diversity heuristic: training set should cover both branded + non-branded + paywalled to exercise all Plan-4 signal paths. If the user's chosen URLs are uniform, URL discovery can supplement: `python ops/scripts/lib/url_discovery.py newsletter` proposes 3 URLs balanced across archetypes.

---

## Codex-allowed edit surfaces (Newsletter-specific)
- `website/features/summarization_engine/summarization/newsletter/{prompts,schema,summarizer}.py`
- `website/features/summarization_engine/summarization/common/*.py` (cross-cutting; flag in commit)
- `website/features/summarization_engine/source_ingest/newsletter/{ingest,site_extractors,preheader,cta,conclusions,stance}.py`
- `website/features/summarization_engine/config.yaml` (`sources.newsletter.*`, `structured_extract.*`)
- `docs/summary_eval/_config/rubric_newsletter.yaml` (misspecification fixes only, per edge case 4)
- `docs/summary_eval/_config/branded_newsletter_sources.yaml` (additions only, per edge case 14)

---

## Task 0: Preflight

- [ ] **Step 1: Checkout + pull**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git fetch origin
git checkout eval/summary-engine-v2-scoring-newsletter
git pull
```

- [ ] **Step 2: Confirm prerequisites**

```bash
test -f docs/summary_eval/_config/rubric_newsletter.yaml && echo "rubric OK"
test -f docs/summary_eval/_config/branded_newsletter_sources.yaml && echo "branded OK"
test -f docs/summary_eval/newsletter/phase0.5-ingest/decision.md && echo "phase0.5 OK"
python -c "from website.features.summarization_engine.summarization.newsletter.summarizer import NewsletterSummarizer; print('summarizer OK')"
python -c "from website.features.summarization_engine.source_ingest.newsletter.stance import classify_stance; print('stance OK')"
python -c "from website.features.summarization_engine.source_ingest.newsletter.site_extractors import extract_structured; print('site OK')"
```
Expected: 6 OK lines.

- [ ] **Step 3: URL inventory check**

```bash
count=$(python ops/scripts/eval_loop.py --source newsletter --list-urls | python -c "import sys,json; print(len(json.load(sys.stdin)))")
echo "Newsletter URL count: $count"
```
If `< 3`: either user-adds URLs under `# Newsletter` in `docs/testing/links.txt`, or auto-discover:
```bash
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from ops.scripts.lib.url_discovery import discover_urls, write_discovery_report
from website.features.summarization_engine.api.routes import _gemini_client
from pathlib import Path
async def main():
    client = _gemini_client()
    urls = await discover_urls('newsletter', client, count=3)
    out = Path('docs/summary_eval/newsletter/auto_discovered_urls.md')
    out.parent.mkdir(parents=True, exist_ok=True)
    write_discovery_report('newsletter', urls, out)
    import re
    p = Path('docs/testing/links.txt'); c = p.read_text(encoding='utf-8')
    new = '\n'.join(u.get('url','') for u in urls if u.get('url'))
    c = re.sub(r'(^# Newsletter\s*\n(?:.*\n)*?)(?=^#|\Z)', lambda m: m.group(1).rstrip() + '\n' + new + '\n', c, count=1, flags=re.MULTILINE)
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
Expected: 200.

- [ ] **Step 5: Clean prior iteration artifacts**

```bash
rm -rf docs/summary_eval/newsletter/iter-*
```

---

## Task 0.6: Pre-loop correctness gate (schema-routing smoke)

Prove Newsletter summarizer routes through `NewsletterStructuredPayload`. If this gate fails, STOP.

- [ ] **Step 1: In-process route smoke + shape assertions**

```bash
python - <<'PY'
import asyncio, sys
from uuid import UUID
from pathlib import Path
from website.features.summarization_engine.core.orchestrator import summarize_url
from website.features.summarization_engine.api.routes import _gemini_client
from ops.scripts.lib.links_parser import parse_links_file

by_source = parse_links_file(Path("docs/testing/links.txt"))
urls = by_source.get("newsletter") or []
assert urls, "no newsletter URLs in links.txt"
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
ds = sp.get("detailed_summary") or {}
for req in ("publication_identity","issue_thesis","sections","stance"):
    if req not in ds: errs.append(f"missing Newsletter field: detailed_summary.{req}")
if not (ds.get("sections") or []): errs.append("sections empty")
tags = r.get("tags") or []
if not (7 <= len(tags) <= 10): errs.append(f"tags out of range 7-10: len={len(tags)}")
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
python ops/scripts/eval_loop.py --source newsletter --iter 1 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-01/manual_review.md**

Read `iter-01/manual_review_prompt.md`. Do NOT open eval.json.

```
eval_json_hash_at_review: "NOT_CONSULTED"

# Manual Review — Newsletter iter-01 (baseline, URL #1)

## brief_summary (/25)
- brief.main_topic_thesis (/6): <prose, score>
- brief.argument_structure (/5): <prose, score>
- brief.key_evidence (/5): <prose, score>
- brief.conclusions_distinct (/4): <prose, score> — separates conclusions from background?
- brief.caveats_addressed (/3): <prose, score>
- brief.stance_preserved (/2): <prose, score> — does brief match the source's apparent stance without editorializing?
Subtotal: <N>/25

## detailed_summary (/45)
- detailed.sections_ordered (/8): <prose, score>
- detailed.claims_source_grounded (/8): <prose, score>
- detailed.examples_captured (/7): <prose, score>
- detailed.action_items (/6): <prose, score>
- detailed.multiple_scenarios (/6): <prose, score>
- detailed.no_footer_padding (/5): <prose, score>
- detailed.bullets_specific (/5): <prose, score>
Subtotal: <N>/45

## tags (/15)
- tags.count_7_to_10 (/2): <score>
- tags.domain_subdomain (/3): <score>
- tags.key_concepts (/3): <score>
- tags.type_intent (/3): <score>
- tags.no_stance_misrepresentation (/4): <score>
Subtotal: <N>/15

## label (/15)
- label.compact_declarative (/6): <score>
- label.branded_source_rule (/5): <score> — if publication is branded (Stratechery/Platformer/...), label MUST contain publication name
- label.informative_not_catchy (/4): <score>
Subtotal: <N>/15

## Anti-patterns explicit check
- `stance_mismatch` (auto_cap=60): summary's implied stance differs from source's detected_stance (ingest metadata)? <y/n>
- `invented_number` (auto_cap=60): summary cites a number/date absent from source? <y/n>
- `branded_source_missing_publication` (auto_cap=90): branded publication, label missing publication name? <y/n>

## Editorialization check (global rule)
Count sentences introducing stance/judgment/framing absent from source. If ≥ 3, hallucination_cap=60 applies.
Count: <N>

## FineSurE dimension estimates (subjective, 0-1)
- faithfulness: <N>
- completeness: <N> — fraction of atomic_facts (importance ≥ 3) present
- conciseness: <N>

## G-Eval dimension estimates (0-5)
- coherence, consistency, fluency, relevance

## Composite computation
base = 0.60 * <rubric_total> + 0.20 * <faith> * 100 + 0.10 * <comp> * 100 + 0.10 * <mean_g_eval> * 20
cap_applied = <which if any>
composite = min(base, cap) if cap else base

## Plan-4 signal utilization check
Examine IngestResult.metadata (via manual_review_prompt.md context):
- `site`: substack / beehiiv / medium / unknown — did summarizer adapt to site? <y/n>
- `detected_stance`: used in detailed_summary.stance field? <y/n>
- `cta_count > 0`: represented in detailed_summary.cta? <y/n>
- `conclusions_count > 0`: summarizer populated detailed_summary.conclusions_or_recommendations? <y/n>
- `publication_identity`: used in label (if branded)? <y/n>

## Most impactful improvement for iter-02
<one paragraph>

estimated_composite: NN.N
```

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 1 --phase iter
```

- [ ] **Step 4: Record baseline + billing check**

```bash
python -c "
import json
e = json.loads(open('docs/summary_eval/newsletter/iter-01/eval.json').read())
i = json.loads(open('docs/summary_eval/newsletter/iter-01/input.json').read())
print('billing_calls:', i.get('gemini_calls', {}).get('role_breakdown', {}).get('billing_calls', 0))
"
```

---

## Task 2: Loop 2 — First tune on URL #1

Typical iter-02 Newsletter edits:
- `brief.conclusions_distinct` low → tune `newsletter/prompts.py` SOURCE_CONTEXT: "Your brief must explicitly distinguish descriptive background from the author's conclusions/recommendations. Use a sentence structure that labels them ('The author concludes...', 'As background...')."
- `brief.stance_preserved` low / `stance_mismatch` anti-pattern fired → tune prompt to force match with `IngestResult.metadata.detected_stance`: "The source's detected stance is <X>. Your brief's tone MUST reflect that stance; never introduce framing words absent from the source."
- `label.branded_source_rule` low / `branded_source_missing_publication` fired → add post-validation in `newsletter/schema.py` that when `publication_identity` matches branded_newsletter_sources.yaml, `mini_title.lower()` must contain publication name. (Plan 4 Task 7 Step 1 already has this validator; if still failing, tighten prompt with an exact example: "For Stratechery pieces, label as 'Stratechery: <thesis>'.")
- `detailed.action_items` low → wire `IngestResult.metadata.conclusions_candidates` (from Plan 4 conclusions.py) into summarizer prompt as an explicit input; reject schema payloads that omit `conclusions_or_recommendations` when the field is populated in metadata.

- [ ] **Step 1-8: Standard tune-loop flow**

```bash
# Step 1: Read iter-01/next_actions.md
# Step 2: Apply edits (unbounded surface)
# Step 3: pytest
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
# Step 4: Commit
git commit -m "feat: newsletter prompt enforce stance preservation"
# Step 5: Phase A
python ops/scripts/eval_loop.py --source newsletter --iter 2 --phase iter --manage-server
# Step 6: Write iter-02/manual_review.md
# Step 7: Phase B
python ops/scripts/eval_loop.py --source newsletter --iter 2 --phase iter
# Step 8: Delta check + banner check (CHURN, evaluator_drift, blind_review_violation)
```

---

## Task 3: Loop 3 — Second tune on URL #1

Focus on mid-range criteria after iter-02. Likely:
- `detailed.examples_captured` — prompt to preserve case studies/data anchors as named bullets
- `tags.type_intent` — auto-inject type tag from heuristic: if `cta_count > 2`, mark `promotional`; if conclusions present, mark `operator-advice`; etc.
- Anti-pattern `invented_number` lingering → add post-validator: any number/date in summary must appear in `ingest.raw_text` (exact-match via regex)

- [ ] **Step 1-8: Standard tune-loop flow**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source newsletter --iter 3 --phase iter --manage-server
# Codex writes iter-03/manual_review.md
python ops/scripts/eval_loop.py --source newsletter --iter 3 --phase iter
```

---

## Task 4: Loop 4 — Cross-URL probe (URLs #1 + #2)

URL #1 (branded) vs URL #2 (non-branded) should exercise C2 hybrid label behavior in opposite directions. Watch both.

- [ ] **Step 1: Phase A**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 4 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-04/manual_review.md**

Two URL sections. Explicitly call out whether URL #1's label contains publication name (required) and URL #2's doesn't (discouraged-if-non-branded, acceptable either way per C2 rule).

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 4 --phase iter
```

- [ ] **Step 4: Overfitting gap measurement**

If URL #2 (non-branded) scores markedly lower on `label.branded_source_rule` because Codex's tune over-enforces publication-in-label, that's the C2 hybrid regressing. Record gap.

If URL #2 triggers `stance_mismatch` but URL #1 didn't: stance classifier may be failing on URL #2's site archetype (non-Substack/Beehiiv/Medium). Record — consider Phase 0.5 extension in loop 8 if it lingers.

---

## Task 5: Loop 5 — Joint tune (URLs #1 + #2 + #3, convergence gate)

URL #3 (paywalled) exercises the paywall-fallback chain. If URL #3's `raw_text` is materially shorter than URLs #1/#2, the summarizer's CoD densifier may compress too aggressively. Tune by making CoD iterations source-length-adaptive (config.yaml `sources.newsletter.cod_iterations_short_text: 1` vs regular 2).

- [ ] **Step 1: Read iter-04/next_actions.md + diff.md**

- [ ] **Step 2: Apply joint-broadening edits targeting all-3-URL failures**

Focus on making the newsletter summarizer handle:
- Branded vs non-branded label without over-triggering `branded_source_missing_publication`
- Substack/Beehiiv/Medium vs `site=unknown` without sacrificing structural extraction
- Paywall-shortened text vs full text without compression collapse

- [ ] **Step 3-7: Standard flow**

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source newsletter --iter 5 --phase iter --manage-server
# Codex writes iter-05/manual_review.md (3 URL sections)
python ops/scripts/eval_loop.py --source newsletter --iter 5 --phase iter
```

- [ ] **Step 8: Early-stop check (spec §3.8)**

All three: URL #1 composite ≥ 92 AND ragas ≥ 0.95; URLs #2, #3 each ≥ 88; ≥ 3 of last 5 iters had URL #1 composite ≥ 92 AND ragas ≥ 0.95. If all yes, CLI sets `status=converged`; proceed to loop 6 (validation still runs).

- [ ] **Step 9: Churn ledger review**

```bash
cat docs/summary_eval/newsletter/edit_ledger.json | python -m json.tool | tail -40
```
Any `churn_flags.<file>.status=churning` becomes relevant in Task 8 (extension) if that file remains the critical path.

---

## Task 6: Loop 6 — Held-out validation (no edits)

- [ ] **Step 1: Phase A**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 6 --phase iter --manage-server
```

- [ ] **Step 2: Write iter-06/manual_review.md**

One section per held-out URL. Explicitly note per-URL:
- site (substack/beehiiv/medium/unknown)
- detected_stance
- whether branded (publication in branded_sources.yaml)
- label conforms to C2 hybrid rule

- [ ] **Step 3: Phase B**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 6 --phase iter
```

- [ ] **Step 4: Thresholds (spec §10.1)**

Held-out mean ≥ 88 AND min ragas_faithfulness ≥ 0.95. Both yes → status=continue. Either no → status=extension_required → Task 8.

---

## Task 7: Loop 7 — Prod-parity + Zoro (SUMMARIZE_ENV=prod-parity)

- [ ] **Step 1: Env + server restart**

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
python ops/scripts/eval_loop.py --source newsletter --iter 7 --phase iter --env prod-parity --manage-server
```
CLI fetches Zoro bearer, POSTs with `write_to_supabase=true`, writes `iter-07/prod_parity_auth.txt`.

- [ ] **Step 3: Write iter-07/manual_review.md**

- [ ] **Step 4: Phase B**

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 7 --phase iter --env prod-parity
```

- [ ] **Step 5: Prod-parity delta (spec §10.1)**

`|iter-07 composite − iter-06 composite| ≤ 5`. Record.

- [ ] **Step 6: Verify Zoro KG + RAG**

```bash
curl -s "https://icmnskseuoteyirljswd.supabase.co/rest/v1/kg_nodes?user_id=eq.a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e&source_type=eq.newsletter&select=id,mini_title,created_at&order=created_at.desc&limit=5" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY"
```
Expected: Newsletter nodes under Zoro matching held-out mini_titles. Record in `docs/summary_eval/newsletter/iter-07/zoro_kg_verification.md`.

RAG check: log in as Zoro on `https://zettelkasten.in/chat`, query a question referencing one of the new newsletter zettels. Verify RAG response cites the node.

- [ ] **Step 7: Reset env**

```bash
unset SUMMARIZE_ENV
kill %1
```

---

## Task 8: Loop 8 — Extension (conditional)

```bash
grep -q "^status: extension_required" docs/summary_eval/newsletter/iter-06/next_actions.md && echo "EXTEND" || echo "SKIP"
```

If EXTEND:
- Read iter-06 aggregate + failed held-out URLs' evaluations.
- Likely Newsletter-specific failures: stance_mismatch on non-Substack/Beehiiv/Medium sites (stance classifier returning neutral by default → rubric `brief.stance_preserved` scored low); or `conclusions_or_recommendations` empty on URLs where Plan 4's conclusions.py didn't detect any (may need to loosen extraction heuristics).
- Apply joint re-tune. CHURN ALERT on previously-edited files → write new_angle.md.
- Retroactive Phase 0.5 extension: if stance classifier is systematically failing on a particular site, consider adding site-specific stance hints to `stance.py` prompt.

```bash
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q
git commit -m "<descriptive>"
python ops/scripts/eval_loop.py --source newsletter --iter 8 --phase iter --manage-server
# Codex writes iter-08/manual_review.md
python ops/scripts/eval_loop.py --source newsletter --iter 8 --phase iter
```

---

## Task 9: Loop 9 — Extension final (conditional)

```bash
python ops/scripts/eval_loop.py --source newsletter --iter 9 --phase iter --manage-server
# Codex writes iter-09/manual_review.md (no edits)
python ops/scripts/eval_loop.py --source newsletter --iter 9 --phase iter
```

If still failing: Newsletter marked `degraded` in final_scorecard.md.

---

## Task 10: Final scorecard + PR promotion

- [ ] **Step 1: Write `docs/summary_eval/newsletter/final_scorecard.md`**

```markdown
# Newsletter — Final Scorecard

## Per-loop progression
| Loop | URLs | Composite (mean) | Faithfulness (min) | Status | Edits |
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
- [ ] Held-out mean ≥ 88 + min ragas ≥ 0.95 (loop 6)
- [ ] Prod-parity delta ≤ 5 (loop 7)
- [ ] No hallucination cap triggered in loops 5-7
- [ ] Anti-pattern `stance_mismatch` never fired in loops 5-7
- [ ] Anti-pattern `invented_number` never fired in loops 5-7
- [ ] Anti-pattern `branded_source_missing_publication` never fired for branded URLs in loops 5-7
- [ ] C2 hybrid label rule applied correctly in 100% of loop-6 runs

**Overall: PRODUCTION-GRADE | DEGRADED with <reason>**

## Plan-4 signal utilization
- Site-specific extractors (substack/beehiiv/medium): fired on <N>/<total> URLs
- Preheader extracted: <count>
- CTA extracted: <count>
- Conclusions extracted: <count>
- Stance classifier fired: <count>; distribution: optimistic/skeptical/cautionary/neutral/mixed = <N/N/N/N/N>
- Branded source URLs: <count>; C2 hybrid label rule enforcement: <N>/<N> compliant

## Cross-model disagreement summary
- AGREEMENT loops: <N>
- MAJOR_DISAGREEMENT loops: <N>
- disagreement_analysis.md written: <y/n, which iters>

## Billing spend
- Total billing_calls iter-01 → iter-07: <N>
- Within spec §9.6 ceiling: <y/n>

## Zoro prod-parity verification (loop 7)
- Newsletter KG writes: <N> new nodes under user_id a57e1f2f-...
- RAG retrieval: <y/n>
- Reference: docs/summary_eval/newsletter/iter-07/zoro_kg_verification.md

## Lessons (for Plan 10 cross_source_lessons.md)
- <3-5 bullets>

## Known risks / follow-ups
- <bullets>

## Branded sources YAML final state
- Publications added during Plan 9: <list with commit shas>
- Current count: <N>
```

- [ ] **Step 2: Commit**

```bash
git add docs/summary_eval/newsletter/final_scorecard.md
git commit -m "docs: newsletter final scorecard"
```

- [ ] **Step 3: Push + promote PR**

```bash
git push origin eval/summary-engine-v2-scoring-newsletter
gh pr ready <PR_NUMBER>
```

Update PR body:
```markdown

## Iteration loops complete (Plan 9)
- Final held-out mean composite: <N>
- Final held-out min ragas_faithfulness: <N>
- Prod-parity delta: <N>
- Zoro prod-parity verified: <yes/no + node count>
- Status: **production-grade** | **degraded** with <reason>

### Deploy gate
Merging this PR triggers production deploy. Verify before merge:
- [ ] CI green
- [ ] final_scorecard.md acceptance table checked
- [ ] Zoro KG writes verified
- [ ] Billing spend within spec §9.6 ceiling
- [ ] No secrets in diff
- [ ] branded_newsletter_sources.yaml additions reviewed

Do NOT merge Plan 10 (polish sources) until this PR's deploy is verified healthy.
```

- [ ] **Step 4: STOP + handoff**

Report:
> Plan 9 complete. Draft PR #<N> promoted to ready. Final composite: <N>. Zoro: <status>. Status: <production-grade|degraded>. Awaiting human review + merge before Plan 10 (polish).

---

## Self-review checklist
- [ ] All 14 edge cases in "Critical edge cases" section observed
- [ ] Every loop: Phase A + manual_review + Phase B
- [ ] All manual_review.md stamped `eval_json_hash_at_review: "NOT_CONSULTED"`
- [ ] Composite math matches spec §3.6 in every manual_review.md
- [ ] Measurement loops (1, 4, 6, 7) had no code edits
- [ ] Tuning loops (2, 3, 5, 8) commits use CLAUDE.md tag rules
- [ ] Rubric edits (if any) are misspecification-only with `docs: rubric fix newsletter:` tag
- [ ] Branded sources YAML additions documented
- [ ] Stance classifier cache consistency verified across iters
- [ ] Plan-4 signal utilization checked per URL in every manual_review.md
- [ ] Cross-model divergence tracked; disagreement_analysis.md written after 2 consecutive MAJORs
- [ ] Billing spend monitored; total within spec ceiling
- [ ] Loop 7 Zoro auth verification or skip-reason recorded
- [ ] final_scorecard.md filled with acceptance table + lessons
- [ ] PR body updated with deploy gate
- [ ] NO merge, NO push to master
