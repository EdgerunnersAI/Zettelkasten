# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_*/rag_*/etc.).
# One-shot / eval / backfill tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""iter-12 Task 30 / R3 Tier-1 — pre-eval gold expectation groundedness check.

Usage:
    python ops/scripts/audit_gold_expectations.py \
        --queries-file docs/rag_eval/common/<kasten>/<iter>/queries.json \
        --iter iter-12 [--dry-run] [--auto-exclude]

Reads queries.json, NLI-checks each gold expectation against node chunks,
and writes coverage_blind_queries.json to the _audit/ dir.

Default: advisory-only (--auto-exclude=False).
"""
# LEGACY (broken after 2026-05-11): imports website.core.supabase_kg which was retired
# in Phase 8.0.6. To revive, port get_supabase_client calls to get_v2_client() from
# website.core.supabase_v2.client. Tracked for follow-up iteration.
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("rag.audit_gold")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Supabase chunk fetch (re-uses the same table pattern as score_rag_eval.py)
# ---------------------------------------------------------------------------

async def _fetch_chunks_for_node(node_id: str, client) -> list[str]:
    """Return chunk text list for a single node_id from kg_node_chunks.

    Uses direct table query — no RPC needed.
    # TODO operator: confirm RPC name if a dedicated rag_chunks_for_node RPC
    # exists; for now we use the table direct-query path proven in score_rag_eval.py.
    """
    try:
        resp = client.table("kg_node_chunks").select(
            "chunk_idx,content"
        ).eq("node_id", node_id).order("chunk_idx").execute()
        return [r["content"] for r in (resp.data or []) if r.get("content")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("chunk fetch failed for %s: %s", node_id, exc)
        return []


# ---------------------------------------------------------------------------
# NLI via Gemini flash-lite
# ---------------------------------------------------------------------------

_NLI_PROMPT = """\
Does the following source content support the answer hypothesis?
Answer with a JSON object {"verdict": "yes"|"no", "confidence": 0.0-1.0}.

SOURCE:
{content}

HYPOTHESIS: {hypothesis}
"""


async def _nli_check(content: str, hypothesis: str, key_pool) -> float:
    """Return 0..1 confidence that content supports hypothesis. 0.0 on failure."""
    prompt = _NLI_PROMPT.format(content=content[:4000], hypothesis=hypothesis[:500])
    try:
        raw = await key_pool.generate_content(
            prompt,
            model_preference="flash-lite",
            max_output_tokens=64,
        )
        text = (raw or "").strip()
        # Parse JSON from response (may be wrapped in ```json blocks)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            verdict = str(parsed.get("verdict", "")).lower()
            confidence = float(parsed.get("confidence", 0.5))
            return confidence if verdict == "yes" else (1.0 - confidence)
    except Exception as exc:  # noqa: BLE001
        logger.warning("NLI call failed: %s", exc)
    return 0.0


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------

async def _audit(args: argparse.Namespace) -> int:
    queries_path = Path(args.queries_file)
    if not queries_path.exists():
        logger.error("queries file not found: %s", queries_path)
        return 1

    with queries_path.open() as fh:
        data = json.load(fh)

    queries = data.get("queries") or []
    meta = data.get("_meta") or {}
    kasten = meta.get("kasten_slug") or queries_path.parts[-3]
    iter_name = args.iter or meta.get("iter") or queries_path.parts[-2]

    out_dir = queries_path.parent / "_audit"
    out_path = out_dir / "coverage_blind_queries.json"

    # Load deps lazily so the script can be imported without full env
    try:
        from website.core.supabase_kg.client import get_supabase_client
        client = get_supabase_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Supabase client unavailable: %s", exc)
        return 1

    try:
        from website.features.api_key_switching import get_key_pool
        key_pool = get_key_pool()
    except Exception as exc:  # noqa: BLE001
        logger.error("Key pool unavailable: %s", exc)
        return 1

    flagged: list[dict] = []
    for q in queries:
        expected = q.get("expected_primary_citation") or q.get("expected_node_ids") or []
        if isinstance(expected, str):
            expected = [expected]
        if not expected:
            logger.debug("qid=%s: expected=[] — skipping (E1 N/A)", q.get("qid"))
            continue

        hypothesis = q.get("ground_truth") or q.get("text") or ""
        all_chunks: list[str] = []
        for node_id in expected:
            chunks = await _fetch_chunks_for_node(node_id, client)
            all_chunks.extend(chunks)

        if not all_chunks:
            logger.warning("qid=%s: no chunks found for %s — flagging coverage_blind", q.get("qid"), expected)
            flagged.append({**q, "coverage_blind": True, "reason": "no_chunks_found"})
            continue

        content = "\n\n".join(all_chunks[:8])  # cap at 8 chunks per NLI call
        score = await _nli_check(content, hypothesis, key_pool)
        logger.info("qid=%s score=%.3f", q.get("qid"), score)
        if score < 0.5:
            flagged.append({**q, "coverage_blind": True, "nli_score": round(score, 4)})

    logger.info("audit complete: %d/%d queries flagged coverage_blind", len(flagged), len(queries))

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as fh:
            json.dump({"iter": iter_name, "kasten": kasten, "flagged": flagged}, fh, indent=2)
        logger.info("wrote %s", out_path)

        if args.auto_exclude and flagged:
            # Write coverage_blind flag back into queries.json (advisory gate)
            flagged_qids = {f["qid"] for f in flagged if f.get("qid")}
            for q in queries:
                if q.get("qid") in flagged_qids:
                    q["coverage_blind"] = True
            with queries_path.open("w") as fh:
                json.dump(data, fh, indent=2)
            logger.info("--auto-exclude: flagged %d queries in %s", len(flagged_qids), queries_path)
    else:
        logger.info("--dry-run: no files written; %d would be flagged", len(flagged))

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Audit gold expectation groundedness for a RAG eval iter.")
    p.add_argument("--queries-file", required=True, help="Path to queries.json")
    p.add_argument("--iter", default=None, help="Iter label (e.g. iter-12); inferred from path if omitted")
    p.add_argument("--auto-exclude", action="store_true", default=False,
                   help="Write coverage_blind flag back into queries.json (default: advisory only)")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Skip all file writes; log findings only")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_audit(_parse_args())))
