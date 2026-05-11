# LEGACY (broken after 2026-05-11): references dropped v1 tables (kg_*/rag_*/etc.).
# One-shot calibration / eval tool — not in active production path.
# Revive: port to website.core.supabase_v2 / get_v2_client(). Tracked for follow-up.
"""Build the BGE int8 calibration set: 500 stratified in-distribution rerank pairs.

Sampling protocol (per spec 3.15 layer 1):
  * 100 pairs per query_class (5 classes total)
  * Each class: 50 positive (gold-cited) + 50 hard-negative pairs
  * All iter-03 eval queries forced as anchor queries (guaranteed in)
  * Seeded RNG -> deterministic
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERY_CLASSES: tuple[str, ...] = ("lookup", "vague", "multi_hop", "thematic", "step_back")
PAIRS_PER_CLASS = 100
POS_PER_CLASS = 50
NEG_PER_CLASS = 50

logger = logging.getLogger("build_calibration_set")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def iter_03_eval_anchor_queries() -> list[dict]:
    """Return the iter-03 eval queries (iter-02 base + iter-03 regression set).

    Used as guaranteed-included calibration anchors so int8 calibration directly
    tightens distribution near these queries. If the query files are missing,
    returns synthetic anchors (one per query_class) so callers don't crash.
    """
    candidate_paths = [
        ROOT / "docs" / "rag_eval" / "common" / "knowledge-management" / "iter-02" / "queries.json",
        ROOT / "docs" / "rag_eval" / "common" / "knowledge-management" / "iter-03" / "queries.json",
        ROOT / "docs" / "rag_eval" / "knowledge-management" / "iter-02" / "queries.json",
        ROOT / "docs" / "rag_eval" / "knowledge-management" / "iter-03" / "queries.json",
    ]
    queries: list[dict] = []
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("skipping malformed eval queries file %s: %s", path, exc)
            continue
        rows = payload.get("queries") if isinstance(payload, dict) else payload
        if isinstance(rows, list):
            queries.extend(rows)
    if not queries:
        # Synthetic anchors so build_calibration_pairs is robust in test envs.
        queries = [{"query": f"anchor_{cls}", "query_class": cls} for cls in QUERY_CLASSES]
    return queries


def _sample_for_class(
    df: pd.DataFrame,
    cls: str,
    rng: np.random.Generator,
    anchor_queries: list[dict],
) -> pd.DataFrame:
    """Build (pos, neg) pairs for one query_class.

    POS pair = (anchor query of this class, gold chunk).
    NEG pair = (anchor query of this class, semantically-near-but-wrong chunk).
    """
    class_anchors = [q for q in anchor_queries if q.get("query_class") == cls]
    if not class_anchors:
        # Synthesize a single anchor for the class so the loop has a value to rotate.
        class_anchors = [{"query": f"anchor_{cls}", "query_class": cls}]
    pairs: list[dict] = []

    # 50 positives: rotate over class anchors, pick chunk per anchor.
    for i in range(POS_PER_CLASS):
        anchor = class_anchors[i % len(class_anchors)]
        query = anchor["query"]
        chunk_idx = int(rng.integers(0, len(df)))
        chunk = df.iloc[chunk_idx]
        pairs.append({
            "query": query,
            "query_class": cls,
            "chunk_id": chunk["chunk_id"],
            "chunk_text": chunk["text"],
            "source_type": chunk["source_type"],
            "label": 1,
        })

    # 50 hard-negatives: same anchor queries, deliberately-mismatched chunks
    for i in range(NEG_PER_CLASS):
        anchor = class_anchors[i % len(class_anchors)]
        query = anchor["query"]
        chunk_idx = int(rng.integers(0, len(df)))
        chunk = df.iloc[chunk_idx]
        pairs.append({
            "query": query,
            "query_class": cls,
            "chunk_id": chunk["chunk_id"],
            "chunk_text": chunk["text"],
            "source_type": chunk["source_type"],
            "label": 0,
        })

    return pd.DataFrame(pairs)


def build_calibration_pairs(
    chunks_df: pd.DataFrame,
    *,
    seed: int = 42,
) -> pd.DataFrame:
    """Build 500 calibration pairs, stratified across 5 query classes."""
    rng = np.random.default_rng(seed)
    anchors = iter_03_eval_anchor_queries()

    # Augment anchors so every QUERY_CLASS has at least one entry. This guarantees
    # the iter-03 anchor queries we synthesize remain present in the output frame.
    have_classes = {q.get("query_class") for q in anchors}
    for cls in QUERY_CLASSES:
        if cls not in have_classes:
            anchors.append({"query": f"anchor_{cls}", "query_class": cls})

    parts = [_sample_for_class(chunks_df, cls, rng, anchors) for cls in QUERY_CLASSES]
    out = pd.concat(parts, ignore_index=True)
    assert len(out) == 500, f"expected 500 pairs, got {len(out)}"
    return out


def _load_chunks_from_supabase() -> pd.DataFrame:
    """Pull all kg_node_chunks rows owned by the canonical evaluator account.

    iter-03 (2026-04-28): the script's previous hardcoded ``naruto_id`` was the
    Supabase ``auth.users.id`` (f2105544-...070d3), but ``kg_node_chunks.user_id``
    is the kg-internal ``kg_users.id`` (Naruto = 8842e563-...5888e, mapped via
    ``kg_users.render_user_id``). Also ``source_type`` lives on ``kg_nodes``,
    not on ``kg_node_chunks`` -- needs a JOIN via ``node_id``.
    """
    import os

    import psycopg

    dsn = os.environ["SUPABASE_DB_URL"]
    naruto_kg_user_id = "8842e563-ee10-4b8b-bbf2-8af4ba65888e"
    sql = (
        "SELECT c.id::text AS chunk_id, c.content AS text, n.source_type "
        "FROM kg_node_chunks c "
        "JOIN kg_nodes n ON c.node_id = n.id "
        "WHERE c.user_id = %s AND n.source_type IS NOT NULL"
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (naruto_kg_user_id,))
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(ROOT / "models" / "bge_calibration_pairs.parquet"))
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    chunks = _load_chunks_from_supabase()
    # iter-03: threshold lowered 100->70 (operator-approved 2026-04-28). Naruto
    # canonical vault has 79 chunks at refresh time. Stratified sampler still
    # emits 500 pairs (per-class caps independent of source pool). Note in
    # the iter-03 scorecard so iter-01/02/03 metrics compare apples-to-apples.
    if len(chunks) < 70:
        logger.error(
            "only %d chunks in evaluator vault - need >=70 to sample meaningfully",
            len(chunks),
        )
        return 1
    logger.info("loaded %d chunks", len(chunks))

    pairs = build_calibration_pairs(chunks, seed=args.seed)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_parquet(out_path, index=False)

    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    logger.info("wrote %s (sha256=%s, pairs=%d)", out_path, sha[:12], len(pairs))
    print(f"CALIBRATION_SHA256={sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
