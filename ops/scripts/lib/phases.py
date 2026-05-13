"""Phase A (summary + eval) and Phase B (review consumption) runners."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

logger = logging.getLogger(__name__)

import yaml

from ops.scripts.lib.artifacts import (
    append_log,
    iter_artifact_paths,
    read_json,
    url_slug,
    write_json,
    write_text,
)
from ops.scripts.lib import churn_ledger, git_helper
from website.features.summarization_engine.core.orchestrator import summarize_url_bundle
from website.features.summarization_engine.evaluator import composite_score
from website.features.summarization_engine.summarization.newsletter.guards import (
    find_unverified_substring,
)
from website.features.summarization_engine.evaluator.manual_review_writer import (
    verify_manual_review,
)
from website.features.summarization_engine.evaluator.prompts import (
    MANUAL_REVIEW_PROMPT_TEMPLATE,
    PROMPT_VERSION,
)
from website.features.summarization_engine.evaluator.consolidated import (
    evaluator_implementation_fingerprint,
    rubric_sha256,
)


EVAL_USER_UUID = UUID("00000000-0000-0000-0000-000000000001")


# ── Phase A ──────────────────────────────────────────────────────────────────


_HALLUCINATION_AP_IDS = {"invented_number", "stance_mismatch", "fabricated_quote"}


def filter_judge_false_positives(eval_result, source_text: str):
    """Drop judge flags whose flagged span is actually present in source.

    Targets: invented_number anti-patterns and summac_lite.contradicted_sentences
    whose span/sentence the judge claims isn't in source but is (whitespace /
    digit-grouping tolerant verbatim match). Industry pattern: deterministic
    post-judge filter (vLLM HaluGate, Patronus, Datadog, Maxim, Lakera).

    Mutates ``eval_result`` (Pydantic ``EvalResult`` or dict) in place and
    returns it. Appends a ``judge_false_positives_dropped`` entry to
    ``evaluator_metadata`` for telemetry. Clears ``hallucination_cap`` when
    the only remaining hallucination anti-patterns have been filtered out.
    """
    dropped: list[dict] = []
    is_model = hasattr(eval_result, "summac_lite")

    # --- summac_lite.contradicted_sentences ---
    if is_model:
        summac_list = list(eval_result.summac_lite.contradicted_sentences)
    else:
        summac = (eval_result.get("summac_lite") or {}) if isinstance(eval_result, dict) else {}
        summac_list = list(summac.get("contradicted_sentences") or [])

    kept_summac = []
    for item in summac_list:
        if item is None:
            continue
        if hasattr(item, "sentence"):
            span = (item.sentence or "")
        else:
            span = (item.get("sentence") or item.get("span") or "")
        if span and not find_unverified_substring(span, source_text):
            dropped.append({"kind": "summac_contradicted_sentence", "span": span[:120]})
            continue
        kept_summac.append(item)

    if is_model:
        eval_result.summac_lite.contradicted_sentences = kept_summac
    else:
        if isinstance(eval_result, dict):
            summac = eval_result.setdefault("summac_lite", {})
            summac["contradicted_sentences"] = kept_summac

    # --- rubric.anti_patterns_triggered ---
    if is_model:
        ap_list = list(eval_result.rubric.anti_patterns_triggered)
    else:
        rubric_d = (eval_result.get("rubric") or {}) if isinstance(eval_result, dict) else {}
        ap_list = list(rubric_d.get("anti_patterns_triggered") or [])

    kept_aps = []
    for ap in ap_list:
        if ap is None:
            continue
        if hasattr(ap, "id"):
            pattern_id = ap.id or ""
            source_region = ap.source_region or ""
        else:
            pattern_id = ap.get("id", "") or ""
            source_region = ap.get("source_region", "") or ""
        if pattern_id == "invented_number" and source_region:
            if not find_unverified_substring(source_region, source_text):
                dropped.append({"kind": "invented_number_fp", "region": source_region[:120]})
                continue
        kept_aps.append(ap)

    if is_model:
        eval_result.rubric.anti_patterns_triggered = kept_aps
        caps = eval_result.rubric.caps_applied
        if caps.get("hallucination_cap") is not None:
            still_hallucinating = any(
                (getattr(ap, "id", None) or (ap.get("id") if isinstance(ap, dict) else None))
                in _HALLUCINATION_AP_IDS
                for ap in kept_aps
            )
            if not still_hallucinating:
                caps["hallucination_cap"] = None
    else:
        if isinstance(eval_result, dict):
            rubric_d = eval_result.setdefault("rubric", {})
            rubric_d["anti_patterns_triggered"] = kept_aps
            caps = rubric_d.get("caps_applied") or {}
            if caps.get("hallucination_cap") is not None:
                still_hallucinating = any(
                    (ap.get("id") if isinstance(ap, dict) else "")
                    in _HALLUCINATION_AP_IDS
                    for ap in kept_aps
                )
                if not still_hallucinating:
                    caps["hallucination_cap"] = None
                    rubric_d["caps_applied"] = caps

    if dropped:
        if is_model:
            meta = eval_result.evaluator_metadata
            meta.setdefault("judge_false_positives_dropped", []).extend(dropped)
        elif isinstance(eval_result, dict):
            eval_result.setdefault("judge_false_positives_dropped", []).extend(dropped)
        logger.info(
            "judge_false_positives_dropped count=%d kinds=%s",
            len(dropped),
            [d["kind"] for d in dropped][:5],
        )

    return eval_result


async def _summarize_and_evaluate(
    *,
    url: str,
    source_type: str,
    gemini_client: Any,
    rubric_path: Path,
    cache_root: Path,
) -> dict:
    """Produce summary + evaluator output for one URL.

    Returns a record with: url, summary, eval, source_text, atomic_facts,
    ingestor_version, composite, latency_ms.
    """
    from website.features.summarization_engine.evaluator.atomic_facts import (
        extract_atomic_facts,
    )
    from website.features.summarization_engine.evaluator.rubric_loader import (
        load_rubric,
    )
    from website.features.summarization_engine.evaluator.consolidated import (
        ConsolidatedEvaluator,
    )
    from website.features.summarization_engine.evaluator.ragas_bridge import RagasBridge

    t0 = time.perf_counter()
    bundle = await summarize_url_bundle(
        url,
        user_id=EVAL_USER_UUID,
        gemini_client=gemini_client,
    )
    ingest = bundle.ingest_result
    summary = bundle.summary_result
    summary_json = summary.model_dump(mode="json")

    rubric_yaml = load_rubric(rubric_path)
    atomic = await extract_atomic_facts(
        client=gemini_client,
        source_text=ingest.raw_text,
        cache_root=cache_root,
        url=url,
        ingestor_version=ingest.ingestor_version,
    )
    evaluator = ConsolidatedEvaluator(gemini_client)
    eval_result = await evaluator.evaluate(
        rubric_yaml=rubric_yaml,
        atomic_facts=atomic,
        source_text=ingest.raw_text,
        summary_json=summary_json,
    )
    # R3: post-judge deterministic FP filter (vLLM HaluGate pattern).
    filter_judge_false_positives(eval_result, ingest.raw_text)
    if eval_result.finesure.faithfulness.score < 0.9:
        bridge = RagasBridge(gemini_client)
        ragas = await bridge.faithfulness(str(summary_json), ingest.raw_text)
        eval_result.evaluator_metadata["ragas_faithfulness"] = ragas

    eval_json = eval_result.model_dump(mode="json")
    ingest_metadata = dict(ingest.metadata) if isinstance(ingest.metadata, dict) else {}
    return {
        "url": url,
        "source_type": source_type,
        "ingestor_version": ingest.ingestor_version,
        "raw_text": ingest.raw_text,
        "atomic_facts": atomic,
        "summary": summary_json,
        "eval": eval_json,
        "composite": composite_score(eval_result),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "extraction_confidence": ingest.extraction_confidence,
        "rubric_yaml": rubric_yaml,
        "ingest_metadata": ingest_metadata,
    }


def _build_manual_review_prompt(records: list[dict], rubric_yaml: dict, eval_json_hash: str) -> str:
    """Multi-URL aware manual-review prompt."""
    sections = []
    for idx, record in enumerate(records, start=1):
        sections.append(
            f"## URL {idx}: {record['url']}\n\n"
            f"### SUMMARY\n```yaml\n"
            f"{yaml.safe_dump(record['summary'], sort_keys=False)}\n```\n\n"
            f"### ATOMIC FACTS\n```yaml\n"
            f"{yaml.safe_dump(record['atomic_facts'], sort_keys=False)}\n```\n\n"
            f"### SOURCE\n```\n{record['raw_text'][:20000]}\n```\n"
        )
    body = MANUAL_REVIEW_PROMPT_TEMPLATE.format(
        rubric_yaml=yaml.safe_dump(rubric_yaml, sort_keys=False),
        summary_json="\n\n".join(sections),
        atomic_facts="(see per-URL sections above)",
        source_text="(see per-URL sections above)",
        eval_json_hash=eval_json_hash,
    )
    return body


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _phase_a_complete(log_path: Path) -> bool:
    """True iff run.log contains a phase_a-done marker.

    W7 idempotency: prior runs that produced artifacts emit
    ``phase_a done eval_hash=...`` to the log just before returning. If that
    line is present, all artifacts (input.json, summary/eval, source_text,
    atomic_facts, prompt) were written and re-running is wasted spend.
    """
    if not log_path.exists():
        return False
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "phase_a done eval_hash=" in text


def _load_existing_payload(paths: dict) -> dict:
    """Re-synthesize the run_phase_a return payload from existing artifacts.

    Used when ``_phase_a_complete`` short-circuits the re-run. Mirrors the
    shape returned at the end of run_phase_a (held_out vs single-URL).
    """
    input_path = paths.get("input")
    prompt_path = paths.get("prompt")
    held_out_dir = paths.get("held_out")
    input_payload: dict = {}
    if input_path is not None and input_path.exists():
        loaded = read_json(input_path)
        if isinstance(loaded, dict):
            input_payload = loaded
    urls = list(input_payload.get("urls") or [])
    records = input_payload.get("records") or []
    composites = [
        r.get("composite", 0.0)
        for r in records
        if isinstance(r, dict) and isinstance(r.get("composite"), (int, float))
    ]
    composite_mean = sum(composites) / len(composites) if composites else 0.0
    held_out_flag = bool(input_payload.get("held_out")) or (
        held_out_dir is not None and held_out_dir.exists()
    )
    return {
        "status": "awaiting_manual_review",
        "path": str(prompt_path) if prompt_path is not None else "",
        "composite_mean": composite_mean,
        "urls": urls,
        "held_out": held_out_flag,
        "cached": True,
    }


def run_phase_a(
    *,
    source: str,
    iter_num: int,
    urls: list[str],
    iter_dir: Path,
    rubric_path: Path,
    cache_root: Path,
    gemini_client_factory: Callable[[], Any],
    held_out: bool = False,
    env: str = "dev",
    force: bool = False,
) -> dict:
    """Run Phase A and write all artifacts.  Returns a status payload.

    When ``force=False`` (default) and the log already shows a phase_a-done
    marker, return the cached payload without re-running. Pass ``force=True``
    to always re-execute (operator override).
    """
    iter_dir.mkdir(parents=True, exist_ok=True)
    paths = iter_artifact_paths(iter_dir)

    # W7: idempotency short-circuit. The 3-restart pattern in iter-N drivers
    # repeatedly re-ran a completed phase_a, burning ~3× the Gemini spend.
    if not force and _phase_a_complete(paths["log"]):
        return _load_existing_payload(paths)

    started_at = datetime.now(timezone.utc).isoformat()
    append_log(paths["log"], f"[{started_at}] phase_a start source={source} iter={iter_num} urls={len(urls)} held_out={held_out}")

    if not urls:
        return {
            "status": "error",
            "message": "No URLs to evaluate — check links.txt",
        }

    client = gemini_client_factory()

    # Enable the client-level call journal so we can split prod vs eval
    # Gemini usage into ``telemetry.json``. The journal is opt-in and free
    # (one list allocation); eval sources that don't want it can skip the
    # drain at the end. ``enable_call_journal`` is idempotent so re-running
    # phase-A on the same client does not clobber an outer subscriber.
    try:
        client.enable_call_journal()
    except AttributeError:
        # Older client stub (e.g. a mock in tests) — telemetry emission
        # is best-effort. Silently degrade rather than break phase-A.
        pass

    async def _run_all() -> list[dict]:
        return [
            await _summarize_and_evaluate(
                url=url,
                source_type=source,
                gemini_client=client,
                rubric_path=rubric_path,
                cache_root=cache_root,
            )
            for url in urls
        ]

    records = asyncio.run(_run_all())

    # Persist summaries + evals
    if held_out:
        paths["held_out"].mkdir(parents=True, exist_ok=True)
        per_url: list[dict] = []
        for record in records:
            sub = paths["held_out"] / url_slug(record["url"])
            sub.mkdir(parents=True, exist_ok=True)
            write_json(sub / "summary.json", record["summary"])
            write_json(sub / "eval.json", record["eval"])
            entry = {
                "url": record["url"],
                "composite": record["composite"],
                "faithfulness": record["eval"]["finesure"]["faithfulness"]["score"],
                "extraction_confidence": record["extraction_confidence"],
            }
            # Reddit-only: surface pullpush_fetched per-item so the scorecard
            # (and the rollup below) can track how often the divergence recovery
            # path fired across the held-out sweep.
            if source == "reddit":
                pp_fetched = record.get("ingest_metadata", {}).get("pullpush_fetched", 0)
                try:
                    entry["pullpush_fetched_count"] = int(pp_fetched or 0)
                except (TypeError, ValueError):
                    entry["pullpush_fetched_count"] = 0
            per_url.append(entry)
        composites = [entry["composite"] for entry in per_url]
        mean_composite = sum(composites) / len(composites) if composites else 0.0
        min_faithfulness = min((entry["faithfulness"] for entry in per_url), default=0.0)
        aggregate_lines = [
            f"# iter-{iter_num:02d} — held-out aggregate",
            "",
            f"- urls: {len(per_url)}",
            f"- mean_composite: {mean_composite:.2f}",
            f"- min_faithfulness: {min_faithfulness:.3f}",
        ]
        pullpush_rollup: dict[str, float] | None = None
        if source == "reddit":
            counts = [entry.get("pullpush_fetched_count", 0) for entry in per_url]
            if counts:
                pullpush_rollup = {
                    "mean": sum(counts) / len(counts),
                    "median": float(statistics.median(counts)),
                    "total": float(sum(counts)),
                    "n": float(len(counts)),
                }
                aggregate_lines.append(
                    f"- pullpush_fetched_mean: {pullpush_rollup['mean']:.2f}"
                )
                aggregate_lines.append(
                    f"- pullpush_fetched_median: {pullpush_rollup['median']:.2f}"
                )
                aggregate_lines.append(
                    f"- pullpush_fetched_total: {int(pullpush_rollup['total'])}"
                )
        aggregate_lines.extend(["", "## Per-URL", ""])
        for entry in per_url:
            extras = ""
            if "pullpush_fetched_count" in entry:
                extras = f" pullpush_fetched={entry['pullpush_fetched_count']}"
            aggregate_lines.append(
                f"- {entry['url']}: composite={entry['composite']:.2f} "
                f"faithfulness={entry['faithfulness']:.3f} "
                f"confidence={entry['extraction_confidence']}{extras}"
            )
        write_text(paths["aggregate"], "\n".join(aggregate_lines) + "\n")
        # Emit a structured scorecard sibling so downstream tooling can read
        # the rollup without re-parsing the Markdown. Kept small and JSON-only.
        scorecard_payload: dict[str, Any] = {
            "source": source,
            "iter": iter_num,
            "urls": len(per_url),
            "mean_composite": round(mean_composite, 4),
            "min_faithfulness": round(min_faithfulness, 4),
            "per_url": per_url,
        }
        if pullpush_rollup is not None:
            scorecard_payload["pullpush_fetched"] = {
                "mean": round(pullpush_rollup["mean"], 4),
                "median": pullpush_rollup["median"],
                "total": int(pullpush_rollup["total"]),
                "n": int(pullpush_rollup["n"]),
            }
        write_json(iter_dir / "scorecard.json", scorecard_payload)
    else:
        if len(records) == 1:
            write_json(paths["summary"], records[0]["summary"])
            write_json(paths["eval"], records[0]["eval"])
        else:
            write_json(
                paths["summary"],
                [{"url": record["url"], "summary": record["summary"]} for record in records],
            )
            write_json(
                paths["eval"],
                [{"url": record["url"], "eval": record["eval"]} for record in records],
            )

    # source_text and atomic_facts (for replay)
    source_text_body = "\n\n".join(
        f"# URL: {record['url']}\n\n{record['raw_text']}" for record in records
    )
    write_text(paths["source_text"], source_text_body)
    write_json(
        paths["atomic_facts"],
        [{"url": record["url"], "facts": record["atomic_facts"]} for record in records],
    )

    # input.json — inputs + timing + key-pool stats
    pool = None
    try:
        pool = client._pool  # type: ignore[attr-defined]
    except Exception:
        pool = None

    input_payload = {
        "source": source,
        "iter": iter_num,
        "urls": urls,
        "started_at": started_at,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "held_out": held_out,
        "prompt_version": PROMPT_VERSION,
        "records": [
            {
                "url": record["url"],
                "composite": record["composite"],
                "extraction_confidence": record["extraction_confidence"],
                "latency_ms": record["latency_ms"],
                "ingestor_version": record["ingestor_version"],
            }
            for record in records
        ],
        "gemini_calls": {
            "role_breakdown": {
                "billing_calls": getattr(pool, "_billing_calls", 0),
                "free_calls": getattr(pool, "_free_calls", 0),
            },
            "quota_exhausted_events": list(getattr(pool, "_events", []) or []),
        },
    }
    write_json(paths["input"], input_payload)

    # telemetry.json — split every Gemini call into prod vs eval buckets
    # with by-model breakdown. Built from the client-level journal we
    # enabled at phase-A start. When the journal was never populated
    # (e.g. a stubbed client in tests) we still emit an empty-shell
    # telemetry.json so downstream tooling can rely on the file's
    # presence.
    try:
        from website.features.summarization_engine.core.telemetry import (
            build_telemetry,
        )

        journal: list[dict] = []
        drain = getattr(client, "drain_call_journal", None)
        if callable(drain):
            journal = drain()
        write_json(iter_dir / "telemetry.json", build_telemetry(journal))
    except Exception as exc:  # noqa: BLE001 — telemetry emission must never break phase-A
        logger.warning("telemetry emission skipped: %s", exc)

    # manual_review_prompt.md — hash is over the actual eval.json file we wrote
    rubric_yaml = records[0]["rubric_yaml"]
    if held_out:
        # hash the first per-URL eval.json so reviewer has something concrete
        sample = paths["held_out"] / url_slug(records[0]["url"]) / "eval.json"
        eval_hash = _sha256_of_file(sample)
    else:
        eval_hash = _sha256_of_file(paths["eval"])

    prompt_body = _build_manual_review_prompt(records, rubric_yaml, eval_hash)
    write_text(paths["prompt"], prompt_body)

    append_log(paths["log"], f"[{datetime.now(timezone.utc).isoformat()}] phase_a done eval_hash={eval_hash[:12]}")
    return {
        "status": "awaiting_manual_review",
        "path": str(paths["prompt"]),
        "composite_mean": sum(record["composite"] for record in records) / len(records),
        "urls": urls,
        "held_out": held_out,
    }


# ── Phase B ──────────────────────────────────────────────────────────────────

def _composite_from_eval_file(path: Path) -> float:
    """Recompute composite from a stored eval.json."""
    from website.features.summarization_engine.evaluator.models import EvalResult

    payload = read_json(path)
    if isinstance(payload, list):
        composites = []
        for entry in payload:
            payload_inner = entry.get("eval") if isinstance(entry, dict) else None
            if not payload_inner:
                continue
            composites.append(composite_score(EvalResult(**payload_inner)))
        if not composites:
            return 0.0
        return sum(composites) / len(composites)
    return composite_score(EvalResult(**payload))


def _composite_from_iter_dir(iter_dir: Path) -> float:
    paths = iter_artifact_paths(iter_dir)
    if paths["eval"].exists():
        return _composite_from_eval_file(paths["eval"])
    if paths["held_out"].exists():
        composites = []
        for sub in sorted(paths["held_out"].iterdir()):
            file = sub / "eval.json"
            if file.exists():
                composites.append(_composite_from_eval_file(file))
        return sum(composites) / len(composites) if composites else 0.0
    return 0.0


def _first_eval_payload(iter_dir: Path) -> dict | None:
    paths = iter_artifact_paths(iter_dir)
    if paths["eval"].exists():
        payload = read_json(paths["eval"])
        if isinstance(payload, list):
            first = payload[0] if payload else {}
            return first.get("eval", first) if isinstance(first, dict) else None
        return payload if isinstance(payload, dict) else None
    if paths["held_out"].exists():
        for sub in sorted(paths["held_out"].iterdir()):
            eval_file = sub / "eval.json"
            if eval_file.exists():
                payload = read_json(eval_file)
                return payload if isinstance(payload, dict) else None
    return None


def _collect_calibration_pairs(source_dir: Path) -> list[tuple[float, float]]:
    """Walk ``source_dir/iter-*`` and return (computed, estimated) pairs.

    Drops iters that have no manual_review.md or no computable composite. The
    returned list is ordered by iter name so downstream Spearman sees a stable
    pairing across runs.
    """
    pairs: list[tuple[float, float]] = []
    if not source_dir.exists():
        return pairs
    for iter_path in sorted(source_dir.glob("iter-*")):
        review_path = iter_path / "manual_review.md"
        if not review_path.exists():
            continue
        ok, estimated = verify_manual_review(review_path)
        if not ok or estimated is None:
            continue
        computed = _composite_from_iter_dir(iter_path)
        if computed is None or computed == 0.0:
            # 0.0 means no eval.json was written; skip to avoid poisoning the
            # rank correlation with zero-variance points.
            continue
        pairs.append((float(computed), float(estimated)))
    return pairs


def _run_calibration_check(source: str, source_dir: Path) -> dict | None:
    """Compute Spearman rho between computed and manual composites for ``source``.

    Returns a dict with ``rho``, ``n``, ``threshold``, ``below_threshold`` when
    computable, else ``None``. WARN-only: callers must not treat this as a
    gate. The underlying helper logs a WARNING when rho < threshold so the
    calibration drift shows up in CI output.
    """
    from ops.scripts.lib.calibration_spearman import (
        DEFAULT_WARN_THRESHOLD,
        check_calibration,
    )

    pairs = _collect_calibration_pairs(source_dir)
    if len(pairs) < 2:
        return None
    auto_scores = [pair[0] for pair in pairs]
    manual_scores = [pair[1] for pair in pairs]
    result = check_calibration(
        auto_scores, manual_scores, source=source,
        threshold=DEFAULT_WARN_THRESHOLD,
    )
    if result is None:
        return {
            "rho": None,
            "n": len(pairs),
            "threshold": DEFAULT_WARN_THRESHOLD,
            "below_threshold": False,
            "note": "insufficient_variance",
        }
    return {
        "rho": round(result.rho, 4),
        "n": result.n,
        "threshold": DEFAULT_WARN_THRESHOLD,
        "below_threshold": result.rho < DEFAULT_WARN_THRESHOLD,
        "used_scipy": result.used_scipy,
    }


def _divergence_stamp(diff: float) -> str:
    if diff <= 5.0:
        return "AGREEMENT"
    if diff <= 10.0:
        return "MINOR_DISAGREEMENT"
    return "MAJOR_DISAGREEMENT"


def _write_diff(
    *,
    iter_dir: Path,
    prev_dir: Path | None,
    computed_composite: float,
    estimated_composite: float | None,
    stamp: str,
) -> None:
    paths = iter_artifact_paths(iter_dir)
    prev_composite = _composite_from_iter_dir(prev_dir) if prev_dir and prev_dir.exists() else None
    delta_vs_prev = (
        round(computed_composite - prev_composite, 2) if prev_composite is not None else None
    )
    lines = [
        f"# {iter_dir.name} — diff",
        "",
        f"- computed_composite: {computed_composite:.2f}",
        "- estimated_composite: "
        + (f"{estimated_composite:.2f}" if estimated_composite is not None else "n/a"),
        f"- divergence: {stamp}",
        f"- score_delta_vs_prev: {delta_vs_prev if delta_vs_prev is not None else 'n/a'}",
    ]
    write_text(paths["diff"], "\n".join(lines) + "\n")


def _write_next_actions(
    *,
    iter_dir: Path,
    status: str,
    computed_composite: float,
    eval_path: Path,
) -> None:
    """Write a heuristic next_actions.md based on the evaluator output.

    Kept deterministic and offline to save API quota; Plan 7+ can swap this for
    a Gemini-synthesized variant via synthesize_next_actions().
    """
    paths = iter_artifact_paths(iter_dir)
    lines = [f"status: {status}", "", f"computed_composite: {computed_composite:.2f}", ""]
    if eval_path.exists():
        payload = read_json(eval_path)
        entries = payload if isinstance(payload, list) else [{"eval": payload}]
        for entry in entries:
            evaluator_output = entry.get("eval", entry) if isinstance(entry, dict) else entry
            rubric = evaluator_output.get("rubric", {})
            components = rubric.get("components", []) or []
            lowest = sorted(components, key=lambda component: component.get("score", 0))[:3]
            missed = []
            for component in components:
                missed.extend(component.get("criteria_missed", []) or [])
            caps = rubric.get("caps_applied", {}) or {}
            triggered_caps = [name for name, value in caps.items() if value]
            anti_patterns = [entry.get("id") for entry in rubric.get("anti_patterns_triggered", []) or []]

            lines.append(f"## URL: {entry.get('url', 'single')}")
            lines.append("")
            lines.append("### Lowest components")
            for component in lowest:
                lines.append(
                    f"- {component.get('id')}: {component.get('score')}/{component.get('max_points')}"
                )
            if missed:
                lines.append("")
                lines.append("### Missed criteria")
                for criterion in missed[:12]:
                    lines.append(f"- {criterion}")
            if triggered_caps:
                lines.append("")
                lines.append("### Caps applied")
                for cap in triggered_caps:
                    lines.append(f"- {cap}: {caps[cap]}")
            if anti_patterns:
                lines.append("")
                lines.append("### Anti-patterns")
                for pattern in anti_patterns:
                    lines.append(f"- {pattern}")
            lines.append("")
    write_text(paths["next_actions"], "\n".join(lines).rstrip() + "\n")


def _infer_targeted_criterion(prev_dir: Path | None) -> str | None:
    if prev_dir is None:
        return None
    path = prev_dir / "next_actions.md"
    if not path.exists():
        return None
    in_missed = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "### Missed criteria":
            in_missed = True
            continue
        if in_missed and line.startswith("### "):
            break
        if in_missed and line.startswith("- "):
            return line[2:].strip() or None
    return None


def _filter_editable_paths(paths: list[str], source: str) -> list[str]:
    allowed_prefixes = (
        "website/features/summarization_engine/",
        "ops/scripts/",
        "tests/",
        "docs/summary_eval/_config/",
    )
    source_prefix = f"docs/summary_eval/{source}/"
    ignored_prefixes = (
        f"{source_prefix}iter-",
        "docs/summary_eval/_cache/",
    )
    changed = []
    for path in paths:
        normalized = path.replace("\\", "/")
        if normalized == f"{source_prefix}edit_ledger.json":
            continue
        if normalized.startswith(ignored_prefixes):
            continue
        if normalized.startswith(allowed_prefixes):
            changed.append(normalized)
    return sorted(set(changed))


def _infer_changed_files(repo_root: Path, source: str, iter_num: int) -> list[str]:
    changed = _filter_editable_paths(
        git_helper.worktree_changed_paths(repo_root), source
    )
    if changed:
        return changed

    if iter_num <= 1:
        return []

    marker = f"test: {source} iter-{iter_num - 1:02d} score"
    base_sha = git_helper.last_commit_matching_subject(repo_root, marker)
    if not base_sha:
        return []
    return _filter_editable_paths(git_helper.changed_paths_since(repo_root, base_sha), source)


def run_phase_b(
    *,
    source: str,
    iter_num: int,
    iter_dir: Path,
    prev_dir: Path | None,
    repo_root: Path,
    allow_commit: bool = True,
) -> dict:
    """Consume manual_review.md, write diff + next_actions, commit."""
    paths = iter_artifact_paths(iter_dir)

    if not paths["review"].exists():
        return {"status": "missing_manual_review", "path": str(paths["review"])}

    ok, estimated = verify_manual_review(paths["review"])
    if not ok:
        return {
            "status": "blind_review_violation",
            "message": "manual_review.md missing NOT_CONSULTED stamp",
        }

    computed = _composite_from_iter_dir(iter_dir)
    diff_value = abs((estimated or computed) - computed)
    stamp = _divergence_stamp(diff_value)

    status = "continue"
    if stamp == "MAJOR_DISAGREEMENT":
        # Pessimistic rule per spec §3.7: next tuning must beat the lower value.
        status = "continue_pessimistic"

    _write_diff(
        iter_dir=iter_dir,
        prev_dir=prev_dir,
        computed_composite=computed,
        estimated_composite=estimated,
        stamp=stamp,
    )
    eval_path = paths["eval"]
    if not eval_path.exists() and paths["held_out"].exists():
        # Pick any per-URL eval to drive next_actions
        candidates = sorted(paths["held_out"].glob("*/eval.json"))
        eval_path = candidates[0] if candidates else eval_path
    _write_next_actions(
        iter_dir=iter_dir,
        status=status,
        computed_composite=computed,
        eval_path=eval_path,
    )

    # Commit artifacts for this iter
    commit_sha = ""
    if allow_commit:
        prev_composite = _composite_from_iter_dir(prev_dir) if prev_dir else None
        subject = (
            f"test: {source} iter-{iter_num:02d} score "
            f"{prev_composite:.1f}→{computed:.1f}"
            if prev_composite is not None
            else f"test: {source} iter-{iter_num:02d} score {computed:.1f}"
        )
        git_helper.add_paths(repo_root, [iter_dir])
        source_dir = iter_dir.parent
        ledger_path = source_dir / "edit_ledger.json"
        if ledger_path.exists():
            git_helper.add_paths(repo_root, [ledger_path])
        try:
            commit_sha = git_helper.commit(repo_root, subject)
        except git_helper.GitError as exc:
            append_log(paths["log"], f"commit_error {exc}")

    # Update churn ledger with 0-delta tuning entry (callers can refine via --targeted)
    source_dir = iter_dir.parent
    try:
        prev_composite = _composite_from_iter_dir(prev_dir) if prev_dir else None
        composite_delta = (
            computed - prev_composite if prev_composite is not None else 0.0
        )
        changed_files = _infer_changed_files(repo_root, source, iter_num)
        targeted_criterion = _infer_targeted_criterion(prev_dir)
        churn_ledger.record(
            source_dir,
            iter_num=iter_num,
            files=changed_files,
            targeted_criterion=targeted_criterion,
            criterion_delta=0.0,
            composite_delta=composite_delta,
        )
    except Exception as exc:
        append_log(paths["log"], f"churn_ledger_error {exc}")

    # Calibration drift probe (WARN-only; never fails Phase B).
    calibration: dict | None = None
    try:
        calibration = _run_calibration_check(source, iter_dir.parent)
    except Exception as exc:  # pragma: no cover - defensive, calibration must never break Phase B
        logger.warning("calibration_spearman_error source=%s err=%s", source, exc)
        calibration = None
    if calibration is not None:
        try:
            write_json(iter_dir / "calibration.json", calibration)
        except Exception as exc:  # pragma: no cover
            logger.warning("calibration_write_error source=%s err=%s", source, exc)

    append_log(paths["log"], f"phase_b done stamp={stamp} commit={commit_sha[:12] if commit_sha else 'none'}")
    return {
        "status": status,
        "computed_composite": computed,
        "estimated_composite": estimated,
        "divergence": stamp,
        "commit": commit_sha,
        "calibration_spearman": calibration,
    }


# ── Determinism check ────────────────────────────────────────────────────────

async def _re_evaluate_from_summary(
    *,
    source: str,
    url: str,
    summary_json: dict,
    source_text: str,
    atomic_facts: list[dict],
    rubric_path: Path,
    gemini_client: Any,
) -> float:
    from website.features.summarization_engine.evaluator.consolidated import (
        ConsolidatedEvaluator,
    )
    from website.features.summarization_engine.evaluator.rubric_loader import load_rubric

    rubric_yaml = load_rubric(rubric_path)
    evaluator = ConsolidatedEvaluator(gemini_client)
    eval_result = await evaluator.evaluate(
        rubric_yaml=rubric_yaml,
        atomic_facts=atomic_facts,
        source_text=source_text,
        summary_json=summary_json,
    )
    filter_judge_false_positives(eval_result, source_text)
    return composite_score(eval_result)


def run_determinism_check(
    *,
    source: str,
    prev_iter_dir: Path,
    rubric_path: Path,
    gemini_client_factory: Callable[[], Any],
    tolerance: float = 2.0,
) -> dict:
    """Re-run the evaluator on the prior iter's stored summary + source text.

    Halt if the new composite differs from the stored one by > tolerance points.
    """
    paths = iter_artifact_paths(prev_iter_dir)
    if not paths["summary"].exists() or not paths["source_text"].exists():
        return {"status": "skipped", "reason": "prior iter missing summary/source_text"}

    rubric_yaml = yaml.safe_load(rubric_path.read_text(encoding="utf-8")) or {}
    stored_eval_payload = _first_eval_payload(prev_iter_dir) or {}
    stored_meta = stored_eval_payload.get("evaluator_metadata", {}) or {}
    current_fingerprint = evaluator_implementation_fingerprint()
    current_rubric_hash = rubric_sha256(rubric_yaml)
    if (
        stored_meta.get("prompt_version") == PROMPT_VERSION
        and stored_meta.get("implementation_fingerprint") == current_fingerprint
        and stored_meta.get("rubric_sha256") == current_rubric_hash
    ):
        return {
            "status": "stable_same_fingerprint",
            "stored_composite": _composite_from_iter_dir(prev_iter_dir),
            "new_composite": _composite_from_iter_dir(prev_iter_dir),
            "drift": 0.0,
            "tolerance": tolerance,
        }

    stored_composite = _composite_from_iter_dir(prev_iter_dir)
    summary_payload = read_json(paths["summary"])
    atomic_payload = read_json(paths["atomic_facts"]) if paths["atomic_facts"].exists() else []
    source_text = paths["source_text"].read_text(encoding="utf-8")

    # Use the first URL if multi-URL
    if isinstance(summary_payload, list):
        record = summary_payload[0]
        url = record.get("url", "")
        summary_json = record.get("summary", {})
        facts = next(
            (entry.get("facts", []) for entry in atomic_payload if entry.get("url") == url),
            [],
        )
        # Trim source_text to just that URL's section
        marker = f"# URL: {url}"
        if marker in source_text:
            section = source_text.split(marker, 1)[1]
            source_text = section.split("# URL: ", 1)[0]
    else:
        url = ""
        summary_json = summary_payload
        facts = atomic_payload[0].get("facts", []) if atomic_payload else []

    client = gemini_client_factory()
    new_composite = asyncio.run(
        _re_evaluate_from_summary(
            source=source,
            url=url,
            summary_json=summary_json,
            source_text=source_text,
            atomic_facts=facts,
            rubric_path=rubric_path,
            gemini_client=client,
        )
    )
    drift = abs(new_composite - stored_composite)
    return {
        "status": "evaluator_drift" if drift > tolerance else "stable",
        "stored_composite": stored_composite,
        "new_composite": new_composite,
        "drift": drift,
        "tolerance": tolerance,
    }


# ── Replay ───────────────────────────────────────────────────────────────────

def run_replay(
    *,
    source: str,
    iter_dir: Path,
    rubric_path: Path,
    gemini_client_factory: Callable[[], Any],
    tolerance: float = 1.0,
) -> dict:
    """Re-run Phase A's evaluator from stored summary/source_text and compare."""
    return run_determinism_check(
        source=source,
        prev_iter_dir=iter_dir,
        rubric_path=rubric_path,
        gemini_client_factory=gemini_client_factory,
        tolerance=tolerance,
    )
