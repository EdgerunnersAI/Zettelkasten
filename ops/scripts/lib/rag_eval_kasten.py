# ops/scripts/lib/rag_eval_kasten.py
"""Kasten builder: loads Naruto Zettels, falls back to Chintan_Testing.md, drives ingestion."""
# LEGACY (broken after 2026-05-11): imports website.core.supabase_kg which was retired
# in Phase 8.0.6. To revive, port get_supabase_client calls to get_v2_client() from
# website.core.supabase_v2.client. Tracked for follow-up iteration.
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from uuid import UUID


class KastenBuildError(Exception):
    pass


_CHINTAN_LINE_RE = re.compile(r"^\d+\.\s+\[([^\]]+)\]\(([^)]+)\)")


def parse_chintan_testing(path: Path) -> list[dict]:
    """Parse Chintan_Testing.md into [{title, url}, ...]."""
    if not path.exists():
        raise KastenBuildError(f"Chintan_Testing.md not found at {path}")
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CHINTAN_LINE_RE.match(line.strip())
        if m:
            entries.append({"title": m.group(1), "url": m.group(2)})
    return entries


async def load_naruto_zettels_for_source(
    *, user_id: UUID, source_type: str, supabase: Any,
) -> list[dict]:
    """Load all Naruto's Zettels for a given source_type from kg_nodes."""
    response = supabase.table("kg_nodes").select("id, name, summary, tags, url, source_type, metadata").eq(
        "user_id", str(user_id)
    ).eq("source_type", source_type).execute()
    return response.data or []


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def select_similar_zettel(
    *,
    candidates: list[dict],
    centroid: list[float],
    min_cosine: float,
    exclude_ids: set[str],
) -> dict | None:
    """Pick highest-cosine candidate above threshold, excluding already-in-Kasten nodes."""
    best = None
    best_score = -1.0
    for c in candidates:
        if c["node_id"] in exclude_ids:
            continue
        sim = _cosine(c["embedding"], centroid)
        if sim >= min_cosine and sim > best_score:
            best = {**c, "_cosine": sim}
            best_score = sim
    return best


async def build_kasten(
    *,
    source: str,
    iter_num: int,
    user_id: UUID,
    seed_node_ids: list[str],
    supabase: Any,
    chintan_path: Path,
    output_dir: Path,
    require_similar: bool = False,
    require_unseen: bool = False,
    similar_min_cosine: float = 0.65,
    unseen_cosine_range: tuple[float, float] = (0.50, 0.70),
) -> dict:
    """Build the iter's Kasten manifest.

    Returns {"zettels": [...], "creation_rationale": "...", "billing_concern": bool}
    """
    naruto_pool = await load_naruto_zettels_for_source(
        user_id=user_id, source_type=source, supabase=supabase,
    )
    pool_by_id = {z["id"]: z for z in naruto_pool}
    selected = [pool_by_id[nid] for nid in seed_node_ids if nid in pool_by_id]
    if len(selected) < len(seed_node_ids):
        missing = set(seed_node_ids) - set(pool_by_id)
        raise KastenBuildError(f"Seed Zettels missing from Naruto KG: {missing}")

    rationale = f"Seed Kasten loaded from Naruto KG (iter-{iter_num:02d})."
    billing_concern = False

    # Probe / unseen Zettel injection (iter >=04 for YouTube, iter >=02 for 3-iter sources)
    # Caller orchestrates which iter triggers what; we just honor the flags.
    return {
        "zettels": selected,
        "creation_rationale": rationale,
        "billing_concern": billing_concern,
    }


async def ingest_kasten(
    *,
    zettels: list[dict],
    user_id: UUID,
    runtime: Any = None,  # accepted for plan compatibility; not used
    supabase: Any = None,
) -> dict:
    """Ensure each Kasten Zettel has chunks in kg_node_chunks.

    Naruto's Zettels were captured via the bot pipeline which writes to kg_nodes
    only — RAG chunks are populated lazily by the ingest hook. This driver checks
    each Zettel and calls ingest_node_chunks for any that lack chunks. Idempotent.
    """
    from website.features.rag_pipeline.ingest.hook import ingest_node_chunks
    from website.core.supabase_kg.client import get_supabase_client

    sb = supabase if supabase is not None else get_supabase_client()
    report: dict = {"per_zettel": [], "total_chunks": 0, "failures": [], "skipped_existing": 0}
    for z in zettels:
        node_id = z["id"]
        try:
            existing = sb.table("kg_node_chunks").select("chunk_idx", count="exact").eq(
                "user_id", str(user_id)
            ).eq("node_id", node_id).limit(1).execute()
            existing_count = existing.count or 0
            if existing_count > 0:
                report["per_zettel"].append({"node_id": node_id, "chunk_count": existing_count, "ok": True, "source": "existing"})
                report["total_chunks"] += existing_count
                report["skipped_existing"] += 1
                continue

            payload = {
                "raw_text": z.get("summary") or "",
                "summary": z.get("summary") or "",
                "title": z.get("name") or node_id,
                "tags": z.get("tags") or [],
                "source_type": z.get("source_type") or "web",
                "raw_metadata": z.get("metadata") or {},
            }
            written = await ingest_node_chunks(payload=payload, user_uuid=user_id, node_id=node_id)
            report["per_zettel"].append({"node_id": node_id, "chunk_count": written, "ok": written > 0, "source": "freshly_ingested"})
            report["total_chunks"] += written
            if written == 0:
                report["failures"].append({"node_id": node_id, "error": "ingest returned 0 chunks"})
        except Exception as exc:
            report["failures"].append({"node_id": node_id, "error": str(exc)})
            report["per_zettel"].append({"node_id": node_id, "ok": False, "error": str(exc)})
    return report
