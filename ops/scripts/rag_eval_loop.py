"""rag_eval iteration CLI - two-phase auto-resume.

Mirrors ops/scripts/eval_loop.py shape; sources: youtube|reddit|github|newsletter.

Phase A: build Kasten -> ingest -> snapshot KG -> run queries (with-graph and
ablated) -> score -> generate KG recommendations -> emit manual_review_prompt.md.

Phase B: verify the cross-LLM blind review stamp -> determinism gate ->
change-breadth gate -> write improvement_delta.json -> apply KG recommendations
-> write next_actions.md and diff.md -> git commit.
"""
# LEGACY (broken after 2026-05-11): imports website.core.supabase_kg which was retired
# in Phase 8.0.6. To revive, port get_supabase_client calls to get_v2_client() from
# website.core.supabase_v2.client. Tracked for follow-up iteration.
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.scripts.lib.rag_eval_state import detect_state, IterState

ARTIFACT_ROOT = Path("docs/rag_eval")
HALT_FILE = ARTIFACT_ROOT / ".halt"


# Per-source seed node IDs (Phase A iter-01 Kasten). Mirrors the header
# comments in docs/rag_eval/_config/queries/<source>/seed.yaml. The youtube
# list matches Naruto's frozen 5-Zettel Kasten for iters 01-04, with the
# probe Zettel added at iter-04 and the held-out Zettel at iter-05.
_YOUTUBE_SEED = [
    "yt-andrej-karpathy-s-llm-in",
    "yt-software-1-0-vs-software",
    "yt-transformer-architecture",
    "yt-lecun-s-vision-human-lev",
    "yt-programming-workflow-is",
]
_YOUTUBE_PROBE = "yt-effective-public-speakin"
_YOUTUBE_HELDOUT = "yt-zero-day-market-covert-exploits"


def _resolve_seed_node_ids(source: str, iter_num: int) -> list[str]:
    """Return the Kasten node-id list for a given (source, iter_num)."""
    if source != "youtube":
        # Per Task 4.4 deferral, non-youtube sources have no seed list yet.
        return []
    if iter_num <= 3:
        return list(_YOUTUBE_SEED)
    if iter_num == 4:
        return [*_YOUTUBE_SEED, _YOUTUBE_PROBE]
    return [*_YOUTUBE_SEED, _YOUTUBE_PROBE, _YOUTUBE_HELDOUT]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    # iter-03 Phase 4C hard-gate mode is mutually exclusive with the
    # source/iter ingestion flow; --enforce-gates skips Phase A/B and
    # exits with the gate result code.
    p.add_argument(
        "--enforce-gates",
        action="store_true",
        help="iter-03 Task 4C.1: compare an answers.json against a "
             "baseline.json and exit non-zero on hard-gate failure.",
    )
    p.add_argument("--answers", type=str, default=None,
                   help="Path to answers.json for --enforce-gates.")
    p.add_argument("--queries", type=str, default=None,
                   help="Path to queries.json for --enforce-gates.")
    p.add_argument("--baseline", type=str, default=None,
                   help="Path to baseline.json for --enforce-gates.")
    p.add_argument(
        "--source",
        choices=["youtube", "reddit", "github", "newsletter"],
        required=False,
    )
    p.add_argument("--iter", type=int, required=False, dest="iter_num")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-determinism", action="store_true")
    p.add_argument("--skip-breadth", action="store_true")
    p.add_argument(
        "--unseal-heldout",
        action="store_true",
        help="Allow loading heldout.yaml (final iter only).",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="Run Phase A + dispatch reviewer + Phase B without pausing.",
    )
    return p.parse_args(argv)


def _read_weights(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _serialize_turn(turn, query) -> dict:
    """Flatten an AnswerTurn + GoldQuery into the dict shape EvalRunner expects.

    iter-03 (Task 4A.1): also attaches a `per_stage` sub-dict so the CI
    gate (Phase 4C) and downstream analysis can grade retrieval recall,
    reranker margin, synthesizer grounding, critic verdict, query class,
    model chain, and latency without re-running the orchestrator. The
    derivation reads only AnswerTurn-level data (no orchestrator
    instrumentation needed); see ops/scripts/eval/per_stage.py.
    """
    from ops.scripts.eval.per_stage import build_per_stage

    citations = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in (turn.citations or [])]
    gold_node_ids = list(getattr(query, "gold_node_ids", []) or [])
    return {
        "query_id": query.id,
        "answer": turn.content if hasattr(turn, "content") else (turn.get("content") or ""),
        "citations": citations,
        "retrieved_node_ids": [c["node_id"] for c in citations],
        "reranked_node_ids": [c["node_id"] for c in citations],
        "contexts": [c.get("snippet") or c.get("content") or "" for c in citations],
        "per_stage": build_per_stage(turn=turn, gold_node_ids=gold_node_ids),
    }


def _build_chunks_map(ingest_report: dict, *, user_id: str | None = None) -> dict[str, list[dict]]:
    """Chunks-per-node map for chunking_score.

    iter-02 fix: fetch real chunk text + token_count from kg_node_chunks so
    chunking_score can compute boundary integrity, dedup, and budget
    compliance against actual content. Falls back to stubs if Supabase is
    not configured (offline tests).
    """
    out: dict[str, list[dict]] = {}
    if user_id is None:
        # Offline / test path: stub by count.
        for entry in ingest_report.get("per_zettel", []):
            if entry.get("ok"):
                out[entry["node_id"]] = [{"text": ""} for _ in range(entry.get("chunk_count", 0))]
        return out

    try:
        from website.core.supabase_kg.client import get_supabase_client
        sb = get_supabase_client()
        if sb is None:
            raise RuntimeError("supabase not configured")
        for entry in ingest_report.get("per_zettel", []):
            if not entry.get("ok"):
                continue
            node_id = entry["node_id"]
            response = sb.table("kg_node_chunks").select(
                "chunk_idx, content, token_count"
            ).eq("user_id", user_id).eq("node_id", node_id).order("chunk_idx").execute()
            rows = response.data or []
            out[node_id] = [
                {
                    "text": r.get("content") or "",
                    "token_count": r.get("token_count") or 0,
                }
                for r in rows
            ]
    except Exception as exc:  # noqa: BLE001
        # Safety: surface the failure but don't break the eval — fall back to stubs.
        logging.getLogger(__name__).warning("chunks_map: real fetch failed (%s); using stubs", exc)
        for entry in ingest_report.get("per_zettel", []):
            if entry.get("ok"):
                out[entry["node_id"]] = [{"text": ""} for _ in range(entry.get("chunk_count", 0))]
    return out


def _prev_eval_path(args) -> Path | None:
    if args.iter_num <= 1:
        return None
    return ARTIFACT_ROOT / args.source / f"iter-{args.iter_num-1:02d}" / "eval.json"


def _rerun_prev_eval(prev_dir: Path) -> float:
    """Re-run the eval on prev iter's stored answers using the current evaluator.

    Used by the determinism gate. Returns the recomputed composite.
    """
    from website.features.rag_pipeline.evaluation.composite import hash_weights_file
    from website.features.rag_pipeline.evaluation.eval_runner import EvalRunner
    from website.features.rag_pipeline.evaluation.gold_loader import load_seed_queries

    weights_path = ARTIFACT_ROOT / "_config" / "composite_weights.yaml"
    weights = _read_weights(weights_path)
    weights_hash = hash_weights_file(weights_path)
    # Resolve source + iter from prev_dir path (...rag_eval/<source>/iter-NN)
    source = prev_dir.parent.name
    queries = load_seed_queries(ARTIFACT_ROOT / "_config" / "queries" / source / "seed.yaml")
    answers = json.loads((prev_dir / "answers.json").read_text(encoding="utf-8"))
    ingest_report = json.loads((prev_dir / "ingest.json").read_text(encoding="utf-8"))
    chunks_map = _build_chunks_map(ingest_report)
    runner = EvalRunner(weights=weights, weights_hash=weights_hash)
    result = runner.evaluate(
        iter_id=f"{source}/{prev_dir.name}_rerun",
        queries=queries,
        answers=answers,
        chunks_per_node=chunks_map,
    )
    return result.composite


def _write_next_actions(iter_dir: Path, eval_curr: dict, review: dict) -> None:
    composite = eval_curr.get("composite", 0.0)
    review_est = review.get("estimated_composite")
    lines = [
        f"# Next actions - {iter_dir.name}",
        "",
        f"Composite (auto): {composite:.2f}",
    ]
    if review_est is not None:
        lines.append(f"Composite (blind review): {review_est:.2f}")
    lines.extend([
        "",
        "## Component scores",
    ])
    for k, v in (eval_curr.get("component_scores") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.extend([
        "",
        "## Suggested follow-ups",
        "- Inspect failed queries (see scores.md / qa_pairs.md).",
        "- Review per-stage observations from manual_review.md.",
        "",
    ])
    (iter_dir / "next_actions.md").write_text("\n".join(lines), encoding="utf-8")


def _write_diff(iter_dir: Path, args) -> None:
    """Emit a short diff.md sentinel. Phase B uses presence of diff.md as the
    'already committed' signal in the state machine.
    """
    content = (
        f"# diff for {args.source}/iter-{args.iter_num:02d}\n\n"
        "See git log for the corresponding commit.\n"
    )
    (iter_dir / "diff.md").write_text(content, encoding="utf-8")


def _render_qa_pairs(iter_dir: Path, queries, answers, per_query) -> None:
    lines = [f"# QA pairs - {iter_dir.name}", ""]
    pq_by_id = {pq.query_id: pq for pq in per_query}
    for q, a in zip(queries, answers):
        lines.append(f"## {q.id}: {q.question}")
        lines.append("")
        lines.append(f"**Answer:** {a.get('answer','')}")
        pq = pq_by_id.get(q.id)
        if pq is not None:
            lines.append("")
            lines.append(f"- retrieved: {pq.retrieved_node_ids}")
            lines.append(f"- reranked: {pq.reranked_node_ids}")
            lines.append(f"- cited: {pq.cited_node_ids}")
        lines.append("")
    (iter_dir / "qa_pairs.md").write_text("\n".join(lines), encoding="utf-8")


def _render_scores(iter_dir: Path, eval_result) -> None:
    cs = eval_result.component_scores
    gl = eval_result.graph_lift
    lines = [
        f"# Scores - {iter_dir.name}",
        "",
        f"Composite: {eval_result.composite:.2f}",
        "",
        "## Components",
        f"- chunking: {cs.chunking}",
        f"- retrieval: {cs.retrieval}",
        f"- reranking: {cs.reranking}",
        f"- synthesis: {cs.synthesis}",
        "",
        "## Graph lift",
        f"- composite: {gl.composite}",
        f"- retrieval: {gl.retrieval}",
        f"- reranking: {gl.reranking}",
        "",
    ]
    (iter_dir / "scores.md").write_text("\n".join(lines), encoding="utf-8")


def _render_kg_changes(iter_dir: Path, recs, snap) -> None:
    lines = [f"# KG changes - {iter_dir.name}", "", f"Recommendations: {len(recs)}", ""]
    for r in recs:
        d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
        lines.append(f"- {d.get('type','?')}: {d.get('rationale','')}")
    (iter_dir / "kg_changes.md").write_text("\n".join(lines), encoding="utf-8")


async def _claude_subagent_runner(*, prompt: str, allowed_files: list[Path]) -> str:
    """Placeholder Agent runner. Real dispatch is wired in by Phase 5 smoke run.

    Emits a stub transcript that documents the prompt + allowed file whitelist
    so that the parent harness can replace this with a real subagent call.
    """
    return json.dumps(
        {
            "stub": True,
            "prompt_excerpt": prompt[:200],
            "allowed_files": [str(p) for p in allowed_files],
            "note": "Replace with real Agent runner before running --auto.",
        },
        indent=2,
    )


async def _run_phase_a(args: argparse.Namespace) -> dict:
    iter_dir = ARTIFACT_ROOT / args.source / f"iter-{args.iter_num:02d}"
    config_dir = ARTIFACT_ROOT / "_config"
    iter_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load + lock weights
    from website.features.rag_pipeline.evaluation.composite import (
        hash_weights_file,
        verify_weights_unchanged,
    )

    weights_path = config_dir / "composite_weights.yaml"
    weights_hash = hash_weights_file(weights_path)
    lock_path = ARTIFACT_ROOT / args.source / ".weights_lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if args.iter_num == 1:
        lock_path.write_text(weights_hash, encoding="utf-8")
    elif lock_path.exists():
        verify_weights_unchanged(weights_path, lock_path.read_text(encoding="utf-8").strip())

    # 2. Load gold queries
    from website.features.rag_pipeline.evaluation.gold_loader import (
        load_heldout_queries,
        load_seed_queries,
    )

    is_final = (args.iter_num == 5 and args.source == "youtube") or (
        args.iter_num == 3 and args.source != "youtube"
    )
    if is_final:
        queries = load_heldout_queries(
            config_dir / "queries" / args.source / "heldout.yaml",
            allow_sealed=args.unseal_heldout,
        )
    else:
        queries = load_seed_queries(config_dir / "queries" / args.source / "seed.yaml")

    # 3. Build Kasten + ingest
    from ops.scripts.lib.rag_eval_kasten import build_kasten, ingest_kasten
    from website.core.supabase_kg.client import get_supabase_client
    from website.features.rag_pipeline.service import _build_runtime

    supabase = get_supabase_client()
    naruto_id = json.loads(
        (config_dir / "_naruto_baseline.json").read_text(encoding="utf-8")
    )["user_id"]
    seed_node_ids = _resolve_seed_node_ids(args.source, args.iter_num)
    kasten = await build_kasten(
        source=args.source,
        iter_num=args.iter_num,
        user_id=naruto_id,
        seed_node_ids=seed_node_ids,
        supabase=supabase,
        chintan_path=Path("docs/research/Chintan_Testing.md"),
        output_dir=iter_dir,
    )
    runtime = _build_runtime(naruto_id)
    ingest_report = await ingest_kasten(
        zettels=kasten["zettels"], user_id=naruto_id, runtime=runtime
    )
    (iter_dir / "kasten.json").write_text(
        json.dumps(kasten, indent=2, default=str), encoding="utf-8"
    )
    (iter_dir / "ingest.json").write_text(
        json.dumps(ingest_report, indent=2), encoding="utf-8"
    )

    # 4. KG snapshot pre-iter
    from website.features.rag_pipeline.evaluation.kg_snapshot import snapshot_kasten

    all_nodes = (
        supabase.table("kg_nodes")
        .select("id, tags")
        .eq("user_id", naruto_id)
        .execute()
        .data
        or []
    )
    all_edges = (
        supabase.table("kg_links")
        .select("source_node_id, target_node_id, relation")
        .eq("user_id", naruto_id)
        .execute()
        .data
        or []
    )
    snap = snapshot_kasten(
        kasten_node_ids=[z["id"] for z in kasten["zettels"]],
        all_nodes=all_nodes,
        all_edges=all_edges,
    )
    (iter_dir / "kg_snapshot.json").write_text(
        snap.model_dump_json(indent=2), encoding="utf-8"
    )

    # 5. Run queries through orchestrator (with-graph + ablated)
    from website.features.rag_pipeline.types import ChatQuery

    answers: list[dict] = []
    answers_ablated: list[dict] = []
    per_query_latencies: list[float] = []
    for q in queries:
        chat_q = ChatQuery(content=q.question)
        turn = await runtime.orchestrator.answer(query=chat_q, user_id=naruto_id)
        answers.append(_serialize_turn(turn, q))
        # Capture orchestrator-reported latency for the with-graph path so the
        # eval result publishes p50/p95 alongside composite/component scores.
        per_query_latencies.append(float(getattr(turn, "latency_ms", 0) or 0))
        ablated_turn = await runtime.orchestrator.answer(
            query=chat_q, user_id=naruto_id, graph_weight_override=0.0
        )
        answers_ablated.append(_serialize_turn(ablated_turn, q))

    (iter_dir / "queries.json").write_text(
        json.dumps([q.model_dump() for q in queries], indent=2, default=str),
        encoding="utf-8",
    )
    (iter_dir / "answers.json").write_text(
        json.dumps(answers, indent=2, default=str), encoding="utf-8"
    )

    # 6. Run eval (with graph) + ablation eval -> graph_lift
    from website.features.rag_pipeline.evaluation.ablation import compute_graph_lift
    from website.features.rag_pipeline.evaluation.eval_runner import EvalRunner

    chunks_per_node = _build_chunks_map(ingest_report, user_id=str(naruto_id))
    weights = _read_weights(weights_path)
    runner = EvalRunner(weights=weights, weights_hash=weights_hash)
    result_with = runner.evaluate(
        iter_id=f"{args.source}/iter-{args.iter_num:02d}",
        queries=queries,
        answers=answers,
        chunks_per_node=chunks_per_node,
        per_query_latencies=per_query_latencies,
    )
    result_ablated = runner.evaluate(
        iter_id=f"{args.source}/iter-{args.iter_num:02d}_ablated",
        queries=queries,
        answers=answers_ablated,
        chunks_per_node=chunks_per_node,
    )
    lift = compute_graph_lift(
        with_graph=result_with.component_scores,
        ablated=result_ablated.component_scores,
        weights=weights,
    )
    result_with = result_with.model_copy(update={"graph_lift": lift})
    (iter_dir / "eval.json").write_text(
        result_with.model_dump_json(indent=2), encoding="utf-8"
    )
    (iter_dir / "ablation_eval.json").write_text(
        result_ablated.model_dump_json(indent=2), encoding="utf-8"
    )

    # 7. atomic_facts.json
    atomic = {q.id: q.atomic_facts for q in queries}
    (iter_dir / "atomic_facts.json").write_text(
        json.dumps(atomic, indent=2), encoding="utf-8"
    )

    # 8. KG recommendations
    from website.features.rag_pipeline.evaluation.kg_recommender import (
        generate_recommendations,
    )

    recs = generate_recommendations(
        queries=[q.model_dump() for q in queries],
        answers=answers,
        kasten_edges=all_edges,
        ragas_per_query={pq.query_id: pq.ragas for pq in result_with.per_query},
        atomic_facts_per_query=atomic,
        kasten_nodes=kasten["zettels"],
    )
    (iter_dir / "kg_recommendations.json").write_text(
        json.dumps([r.model_dump() for r in recs], indent=2), encoding="utf-8"
    )

    # 9. Render human artifacts
    _render_qa_pairs(iter_dir, queries=queries, answers=answers, per_query=result_with.per_query)
    _render_scores(iter_dir, eval_result=result_with)
    _render_kg_changes(iter_dir, recs=recs, snap=snap)

    # 10. Build manual_review_prompt.md
    from ops.scripts.lib.rag_eval_review import build_review_prompt

    (iter_dir / "manual_review_prompt.md").write_text(
        build_review_prompt(iter_dir, source=args.source, iter_num=args.iter_num),
        encoding="utf-8",
    )

    return {
        "status": "phase_a_complete",
        "composite": result_with.composite,
        "graph_lift_composite": lift.composite,
    }


async def _run_phase_b(args: argparse.Namespace) -> dict:
    iter_dir = ARTIFACT_ROOT / args.source / f"iter-{args.iter_num:02d}"
    from ops.scripts.lib.rag_eval_breadth import (
        breadth_gate,
        extract_changed_components,
    )
    from ops.scripts.lib.rag_eval_diff import (
        determinism_gate,
        write_improvement_delta,
    )
    from ops.scripts.lib.rag_eval_review import verify_review_stamp

    # 1. Verify review stamp
    review = verify_review_stamp(iter_dir / "manual_review.md")

    # 2. Determinism gate
    if args.iter_num > 1 and not args.skip_determinism:
        prev_dir = ARTIFACT_ROOT / args.source / f"iter-{args.iter_num-1:02d}"
        prev_eval = json.loads((prev_dir / "eval.json").read_text(encoding="utf-8"))
        rerun_composite = _rerun_prev_eval(prev_dir)
        determinism_gate(
            prev_composite=prev_eval["composite"], current_composite=rerun_composite
        )

    # 3. Change-breadth gate
    if args.iter_num > 1 and not args.skip_breadth:
        diff_stat = subprocess.check_output(
            ["git", "diff", f"iter-{args.iter_num-1:02d}_committed..HEAD", "--stat"],
            text=True,
        )
        components, configs = extract_changed_components(diff_stat)
        breadth_gate(components=components, config_or_weight_changed=bool(configs))

    # 4. Improvement delta
    eval_curr = json.loads((iter_dir / "eval.json").read_text(encoding="utf-8"))
    if args.iter_num > 1:
        prev_eval = json.loads(
            (
                ARTIFACT_ROOT
                / args.source
                / f"iter-{args.iter_num-1:02d}"
                / "eval.json"
            ).read_text(encoding="utf-8")
        )
        write_improvement_delta(
            iter_dir=iter_dir,
            prev_composite=prev_eval["composite"],
            curr_composite=eval_curr["composite"],
            prev_components=prev_eval["component_scores"],
            curr_components=eval_curr["component_scores"],
            graph_lift_prev=prev_eval.get("graph_lift", {}),
            graph_lift_curr=eval_curr.get("graph_lift", {}),
            review_estimate=review["estimated_composite"],
        )

    # 5. Apply KG recommendations autonomously
    naruto_id = json.loads(
        (ARTIFACT_ROOT / "_config" / "_naruto_baseline.json").read_text(encoding="utf-8")
    )["user_id"]
    subprocess.run(
        [
            "python",
            "ops/scripts/apply_kg_recommendations.py",
            "--iter",
            f"{args.source}/iter-{args.iter_num:02d}",
            "--user-id",
            naruto_id,
        ],
        check=True,
    )

    # 6. next_actions.md + diff.md
    _write_next_actions(iter_dir, eval_curr, review)
    _write_diff(iter_dir, args)

    # 7. Commit
    if not args.dry_run:
        subprocess.run(
            ["git", "add", str(iter_dir), str(ARTIFACT_ROOT / "_kg_changelog.md")],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"feat: rag_eval {args.source} iter-{args.iter_num:02d}",
            ],
            check=True,
        )

    return {"status": "phase_b_complete"}


def _cli_dispatch(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.enforce_gates:
        # iter-03 Phase 4C.1: hard CI gate. Skips the Kasten build /
        # ingest / orchestrator-run flow entirely — this mode is the
        # only thing the iter-03 PR-gated CI workflow runs.
        from ops.scripts.eval.ci_gate import run_gate_from_paths

        missing = [
            name for name, val in (
                ("--answers", args.answers),
                ("--queries", args.queries),
                ("--baseline", args.baseline),
            ) if not val
        ]
        if missing:
            print(f"ERROR: --enforce-gates requires {', '.join(missing)}", file=sys.stderr)
            return 2
        return run_gate_from_paths(
            answers_path=Path(args.answers),
            queries_path=Path(args.queries),
            baseline_path=Path(args.baseline),
        )
    if not args.source or args.iter_num is None:
        print(
            "ERROR: --source and --iter are required unless --enforce-gates is passed",
            file=sys.stderr,
        )
        return 2
    if HALT_FILE.exists():
        print(f"HALTED: {HALT_FILE.read_text(encoding='utf-8')}")
        return 1
    iter_dir = ARTIFACT_ROOT / args.source / f"iter-{args.iter_num:02d}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    state = detect_state(iter_dir)

    if args.dry_run:
        print(
            json.dumps(
                {"status": "dry_run", "state": state.value, "iter_dir": str(iter_dir)},
                indent=2,
            )
        )
        return 0

    if state == IterState.PHASE_A_REQUIRED:
        result = asyncio.run(_run_phase_a(args))
    elif state == IterState.AWAITING_MANUAL_REVIEW:
        if not args.auto:
            print(f"AWAITING_MANUAL_REVIEW - write {iter_dir}/manual_review.md")
            return 0
        from ops.scripts.lib.rag_eval_review import dispatch_blind_reviewer

        asyncio.run(
            dispatch_blind_reviewer(
                iter_dir=iter_dir,
                source=args.source,
                iter_num=args.iter_num,
                agent_runner=_claude_subagent_runner,
            )
        )
        result = asyncio.run(_run_phase_b(args))
    elif state == IterState.PHASE_B_REQUIRED:
        result = asyncio.run(_run_phase_b(args))
    else:
        result = {"status": "already_committed"}

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli_dispatch())
