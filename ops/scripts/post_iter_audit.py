"""iter-12 Task 36: post-iter audit aggregator.

Reads scores.md + verification_results.json + optional live env capture file
and writes a single ``post_iter_audit.md`` report:

  1. Scores summary (composite + trust-first metrics + burst rates)
  2. Per-stage runtime + memory (t_db_wait_ms / t_rerank_ms / t_synth_ms / p_user_complete_ms)
  3. Failed gold@1 queries with plain-English forensic diagnosis
  4. Monitor status (Tasks 14, 25, 30, 31, 35 telemetry presence heuristic)
  5. Live env-during-eval monitor (user-mandated): asserts key RAG knobs matched
     expected values when the eval was captured.

Usage:
    python ops/scripts/post_iter_audit.py --iter iter-12
    python ops/scripts/post_iter_audit.py --iter-dir docs/rag_eval/.../iter-12

Idempotent — re-runs overwrite post_iter_audit.md.
Read-only — zero runtime impact on production code paths.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Expected live-env knob values for iter-12 (user-mandated verification set)
# ---------------------------------------------------------------------------
_EXPECTED_ENV: dict[str, str] = {
    "RAG_ANCHOR_BOOST_ENABLED": "true",
    "RAG_ANCHOR_SEED_INJECTION_ENABLED": "true",
    "RAG_EXECUTOR_MAX_WORKERS": "8",
    "RAG_RPC_GLOBAL_SEMAPHORE": "8",
    "RAG_HTTPX_MAX_CONNECTIONS": "16",
    "RAG_HTTPX_MAX_KEEPALIVE": "8",
    "RAG_ENTITY_GATHER_SEMAPHORE": "3",
    "RAG_SCORE_RANK_GAP_BYPASS": "1.5",
    "RAG_RETRY_GAP_BYPASS": "1.5",
    "RAG_TITLE_OVERLAP_PERCENTILE": "75",
    "RAG_TITLE_OVERLAP_FLOOR_FALLBACK": "0.10",
    "RAG_ROUTER_VERSION": "v4",
    "RAG_SCORE_RANK_DEMOTE_SLOPE": "0.20",
    "RAG_ANCHOR_BANDIT_ENABLED": "true",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AuditFindings:
    # Section 1 — scores
    composite: float | None = None
    accuracy_user_visible: float | None = None
    over_refusal_rate: float | None = None
    under_refusal_rate: float | None = None
    within_budget_rate: float | None = None
    burst_502_rate: float | None = None
    burst_503_rate: float | None = None
    # Section 2 — per-stage timing
    per_stage_timing: dict[str, dict] = field(default_factory=dict)
    # Section 3 — forensic failures
    failed_gold_at_1: list[dict] = field(default_factory=list)
    refused_queries: list[dict] = field(default_factory=list)
    over_budget_queries: list[dict] = field(default_factory=list)
    # Section 4 — monitor status
    monitor_status: dict[str, str] = field(default_factory=dict)
    # Section 5 — live env
    live_env_state: dict[str, str] = field(default_factory=dict)
    live_env_drift: list[str] = field(default_factory=list)
    live_env_source: str = "not captured"
    notes: list[str] = field(default_factory=list)

    def write_report(self, path: Path) -> None:
        lines = ["# Post-iter audit report", ""]

        # ── Section 1: Scores ──────────────────────────────────────────────
        lines += ["## 1. Scores summary", ""]
        if self.composite is not None:
            lines.append(f"- **Composite:** {self.composite:.2f}")
        if self.accuracy_user_visible is not None:
            lines.append(f"- **accuracy_user_visible:** {self.accuracy_user_visible:.4f}")
        if self.over_refusal_rate is not None:
            lines.append(f"- **over_refusal_rate:** {self.over_refusal_rate:.4f}")
        if self.under_refusal_rate is not None:
            lines.append(f"- **under_refusal_rate:** {self.under_refusal_rate:.4f}")
        if self.within_budget_rate is not None:
            lines.append(f"- **within_budget_rate:** {self.within_budget_rate:.4f}")
        if self.burst_502_rate is not None:
            lines.append(f"- **burst_502_rate:** {self.burst_502_rate:.4f}  (target 0.0)")
        if self.burst_503_rate is not None:
            lines.append(f"- **burst_503_rate:** {self.burst_503_rate:.4f}  (target ≥0.08)")
        if self.composite is None and self.accuracy_user_visible is None:
            lines.append("_scores.md not found or unparseable._")
        lines.append("")

        # ── Section 2: Per-stage runtime ───────────────────────────────────
        lines += ["## 2. Per-stage runtime + memory", ""]
        if self.per_stage_timing:
            lines += [
                "| qid | t_db_wait_ms | t_rerank_ms | t_synth_ms | p_user_complete_ms |",
                "|---|---:|---:|---:|---:|",
            ]
            for qid, t in self.per_stage_timing.items():
                lines.append(
                    f"| {qid} | {t.get('t_db_wait_ms', '—')} | {t.get('t_rerank_ms', '—')} | "
                    f"{t.get('t_synth_ms', '—')} | {t.get('p_user_complete_ms', '—')} |"
                )
        else:
            lines.append(
                "_No per-stage timing in verification_results.json — "
                "check Class P log artifact._"
            )
        lines.append("")

        # ── Section 3: Failed gold@1 forensic ─────────────────────────────
        lines += [f"## 3. Failed gold@1 queries ({len(self.failed_gold_at_1)} total)", ""]
        if not self.failed_gold_at_1:
            lines.append("_None._")
        else:
            for q in self.failed_gold_at_1:
                lines.append(f"### {q['qid']}")
                lines.append(f"- **expected:** `{q.get('expected')}`")
                lines.append(f"- **primary_citation:** `{q.get('primary_citation')}`")
                lines.append(
                    f"- **refused:** {q.get('refused')},  "
                    f"**over_refusal:** {q.get('over_refusal')}"
                )
                lines.append(f"- **retry_outcome_class:** `{q.get('retry_outcome_class')}`")
                lines.append(
                    f"- **per-stage:** db={q.get('t_db_wait_ms')}ms  "
                    f"rerank={q.get('t_rerank_ms')}ms  "
                    f"synth={q.get('t_synth_ms')}ms  "
                    f"total={q.get('p_user_complete_ms')}ms"
                )
                lines.append(f"- **diagnosis:** {_diagnose(q)}")
                lines.append("")

        # ── Section 4: Monitor status ──────────────────────────────────────
        lines += ["## 4. Monitor status (Tasks 14, 25, 30, 31, 35)", ""]
        for monitor, status in self.monitor_status.items():
            lines.append(f"- **{monitor}:** {status}")
        if not self.monitor_status:
            lines.append("_No monitor status — verify telemetry tasks landed._")
        lines.append("")

        # ── Section 5: Live env-during-eval (user-mandated) ────────────────
        drift_count = len(self.live_env_drift)
        header_flag = f"  [DRIFT: {drift_count} knob(s)]" if drift_count else ""
        lines += [
            f"## 5. Live env-during-eval monitor (user-mandated){header_flag}",
            "",
            f"**Source:** {self.live_env_source}",
            "",
        ]
        if not self.live_env_state and not self.live_env_drift:
            lines += [
                "_Live env not captured.  Pre-populate:_",
                "```",
                "ssh deploy@<droplet> \"docker exec zettelkasten-$(cat /opt/zettelkasten/deploy/active_color) env\" \\",
                "    | grep -E '^RAG_' > <iter_dir>/_audit/live_env_capture.txt",
                "```",
                "",
            ]
        else:
            lines += [
                "| knob | expected | actual | MATCH |",
                "|---|---|---|:---:|",
            ]
            for knob, exp in _EXPECTED_ENV.items():
                actual = self.live_env_state.get(knob, "_(not set)_")
                match_cell = "✓" if actual == exp else "**✗ DRIFT**"
                lines.append(f"| `{knob}` | `{exp}` | `{actual}` | {match_cell} |")
            lines.append("")
            if drift_count:
                lines.append(
                    f"**DRIFT detected on {drift_count} knob(s): "
                    f"{', '.join(f'`{k}`' for k in self.live_env_drift)}**"
                )
                lines.append(
                    "_Iter-12 results may not reflect the intended config. "
                    "Investigate before promoting scores._"
                )
            else:
                lines.append("_All expected knobs matched. Live env verified._")
        lines.append("")

        if self.notes:
            lines += ["## Operator notes", ""]
            for n in self.notes:
                lines.append(f"- {n}")

        path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diagnose(q: dict) -> str:
    """Plain-English diagnosis from per-query fields."""
    if q.get("retry_outcome_class") == "empty_pool":
        return (
            "Retrieval returned empty pool — check Class P PATH_F deploy state "
            "and entity-resolve telemetry"
        )
    if q.get("retry_outcome_class") in ("timeout", "retry_budget_exceeded"):
        return (
            "Retry timed out / budget exceeded — check t_db_wait_ms and "
            "Class P thread-pool saturation"
        )
    if q.get("over_refusal"):
        return "Synth refused with gold retrieved — Q3 gate or floor check needed"
    primary = q.get("primary_citation")
    expected = q.get("expected") or []
    if primary and expected and primary not in expected:
        return (
            "Wrong primary picked — check Q5 percentile demote margin "
            "and slot-1 anchor pin"
        )
    if q.get("refused"):
        return "Refused — check coverage_blind flag from Task 30 audit"
    return "Unknown — manual triage required"


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignores blanks and comments."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _try_gh_log_env(iter_dir: Path) -> tuple[dict[str, str], str]:
    """Best-effort: query the most recent successful deploy workflow log for RAG_ vars."""
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--workflow=deploy-droplet.yml",
                "--limit", "5",
                "--json", "databaseId,headSha,conclusion",
            ],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return {}, "gh CLI unavailable"
        runs = json.loads(result.stdout or "[]")
        successful = [r for r in runs if r.get("conclusion") == "success"]
        if not successful:
            return {}, "no successful deploy runs found in last 5"
        run_id = successful[0]["databaseId"]
        log_result = subprocess.run(
            ["gh", "run", "view", str(run_id), "--log"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if log_result.returncode != 0:
            return {}, f"gh run view {run_id} failed"
        env_vars: dict[str, str] = {}
        for line in log_result.stdout.splitlines():
            m = re.search(r"(RAG_[A-Z0-9_]+)=([^\s]+)", line)
            if m:
                env_vars[m.group(1)] = m.group(2)
        if env_vars:
            return env_vars, f"gh workflow run {run_id} log"
        return {}, f"no RAG_ vars found in deploy run {run_id} log"
    except Exception:  # noqa: BLE001
        return {}, "gh CLI error"


# ---------------------------------------------------------------------------
# Core audit function
# ---------------------------------------------------------------------------


def run_audit(iter_dir: Path) -> AuditFindings:
    findings = AuditFindings()

    # ── Section 1: parse scores.md ─────────────────────────────────────────
    scores_path = iter_dir / "scores.md"
    scores_text = ""
    if scores_path.exists():
        scores_text = scores_path.read_text(encoding="utf-8")
        m = re.search(r"\*\*Composite:\*\*\s+([\d.]+)", scores_text)
        if m:
            findings.composite = float(m.group(1))
        for key in ("accuracy_user_visible", "over_refusal_rate",
                    "under_refusal_rate", "within_budget_rate"):
            m = re.search(rf"{key}:\s+([\d.]+)", scores_text)
            if m:
                setattr(findings, key, float(m.group(1)))
        m = re.search(r"502 rate[^:]*:\s+([\d.]+)", scores_text)
        if m:
            findings.burst_502_rate = float(m.group(1))
        m = re.search(r"503 rate[^:]*:\s+([\d.]+)", scores_text)
        if m:
            findings.burst_503_rate = float(m.group(1))

    # ── Sections 2 + 3: parse verification_results.json ──────────────────
    verif_path = iter_dir / "verification_results.json"
    if verif_path.exists():
        data = json.loads(verif_path.read_text(encoding="utf-8"))
        for phase in data.get("phases", []):
            if phase.get("phase") != "rag_qa_chain":
                continue
            for check in phase.get("checks", []):
                d = check.get("detail") or {}
                qid = d.get("qid")
                if not qid:
                    continue
                # Section 2: timing
                if d.get("t_db_wait_ms") is not None or d.get("t_synth_ms") is not None:
                    findings.per_stage_timing[qid] = {
                        "t_db_wait_ms": d.get("t_db_wait_ms"),
                        "t_rerank_ms": d.get("t_rerank_ms"),
                        "t_synth_ms": d.get("t_synth_ms"),
                        "p_user_complete_ms": d.get("p_user_complete_ms"),
                    }
                expected = d.get("expected") or []
                # Section 3: failed gold@1 (has expected, but gold_at_1 falsy)
                if expected and not d.get("gold_at_1"):
                    findings.failed_gold_at_1.append({
                        "qid": qid,
                        "expected": expected,
                        "primary_citation": d.get("primary_citation"),
                        "refused": d.get("refused"),
                        "over_refusal": d.get("over_refusal"),
                        "retry_outcome_class": d.get("retry_outcome_class"),
                        "t_db_wait_ms": d.get("t_db_wait_ms"),
                        "t_rerank_ms": d.get("t_rerank_ms"),
                        "t_synth_ms": d.get("t_synth_ms"),
                        "p_user_complete_ms": d.get("p_user_complete_ms"),
                    })
                if d.get("refused"):
                    findings.refused_queries.append({"qid": qid, "expected": expected})
                puc = d.get("p_user_complete_ms")
                bgt = d.get("budget_ms")
                if puc and bgt and puc > bgt:
                    findings.over_budget_queries.append(
                        {"qid": qid, "p_user_complete_ms": puc, "budget_ms": bgt}
                    )

    # ── Section 4: monitor heuristics ─────────────────────────────────────
    findings.monitor_status["Task 14 — accuracy_user_visible (Class S)"] = (
        "OK" if findings.accuracy_user_visible is not None
        else "MISSING — Class S not surfaced in scores.md"
    )
    findings.monitor_status["Task 25 — primary_citation headline (I9)"] = (
        "OK" if "primary_citation" in scores_text
        else "MISSING — I9 retrieval_recall split not in scores.md"
    )
    findings.monitor_status["Task 30 — coverage_blind audit (R3 Tier-1)"] = (
        "OK" if (iter_dir / "_audit" / "coverage_blind_queries.json").exists()
        else "NOT RUN — operator must invoke audit_gold_expectations.py"
    )
    findings.monitor_status["Task 31 — anchor_seed_bandit telemetry (R4)"] = (
        "OK if log line `anchor_seed_bandit qid=...` present in droplet logs (manual check)"
    )
    retry_classes_present = any(
        q.get("retry_outcome_class") for q in findings.failed_gold_at_1
    )
    findings.monitor_status["Task 35 — retry_outcome_class + per-stage timing (R1)"] = (
        "OK" if (retry_classes_present or findings.per_stage_timing)
        else (
            "MISSING — verify orchestrator emits the log line and "
            "verification_results includes the field"
        )
    )

    # ── Section 5: live env capture ────────────────────────────────────────
    capture_path = iter_dir / "_audit" / "live_env_capture.txt"
    if capture_path.exists():
        raw_env = _parse_env_file(capture_path.read_text(encoding="utf-8"))
        findings.live_env_state = raw_env
        findings.live_env_source = str(capture_path)
    else:
        # Best-effort fallback: query gh workflow run log
        gh_env, gh_source = _try_gh_log_env(iter_dir)
        if gh_env:
            findings.live_env_state = gh_env
            findings.live_env_source = gh_source
        else:
            findings.live_env_source = (
                f"not captured — {gh_source}. "
                "Pre-populate: ssh deploy@<droplet> "
                "\"docker exec zettelkasten-$(cat /opt/zettelkasten/deploy/active_color) env\" "
                f"| grep -E '^RAG_' > {iter_dir}/_audit/live_env_capture.txt"
            )

    # Compute drift against expected knobs
    if findings.live_env_state:
        for knob, expected_val in _EXPECTED_ENV.items():
            actual = findings.live_env_state.get(knob)
            if actual != expected_val:
                findings.live_env_drift.append(knob)

    return findings


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Post-iter audit aggregator: combines scores.md + "
        "verification_results.json + live env into post_iter_audit.md.",
    )
    ap.add_argument("--iter", help="iter name, e.g. iter-12")
    ap.add_argument("--iter-dir", help="full path to iter directory")
    args = ap.parse_args()

    if args.iter_dir:
        iter_dir = Path(args.iter_dir)
    elif args.iter:
        iter_dir = (
            Path("docs/rag_eval/common/knowledge-management") / args.iter
        )
    else:
        raise SystemExit("Provide --iter or --iter-dir")

    findings = run_audit(iter_dir)
    out_path = iter_dir / "post_iter_audit.md"
    findings.write_report(out_path)
    print(f"Wrote {out_path}")
    print(
        f"composite={findings.composite}  "
        f"accuracy_user_visible={findings.accuracy_user_visible}  "
        f"failed_gold_at_1={len(findings.failed_gold_at_1)}  "
        f"refused={len(findings.refused_queries)}  "
        f"live_env_drift={len(findings.live_env_drift)}"
    )


if __name__ == "__main__":
    main()
