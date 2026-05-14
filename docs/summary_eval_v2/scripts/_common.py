"""Shared helpers for summary_eval_v2 scripts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = REPO_ROOT / "docs" / "summary_eval_v2"
SOURCE_CONFIG = EVAL_ROOT / "_config" / "sources_all.json"
SOURCES = (
    "github",
    "newsletter",
    "reddit",
    "youtube",
    "hackernews",
    "linkedin",
    "arxiv",
    "podcast",
    "twitter",
    "web",
)


def iter_dir(source: str, iter_num: int) -> Path:
    return EVAL_ROOT / source / f"iter-{iter_num:02d}"


def load_source_config() -> dict[str, Any]:
    return json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))


def source_urls(source: str) -> list[str]:
    raw = load_source_config()["sources"][source].get("urls") or []
    return [str(url) for url in raw if str(url).strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_last_legacy_iter(source: str) -> Path | None:
    source_root = REPO_ROOT / "docs" / "summary_eval" / source
    if not source_root.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for child in source_root.iterdir():
        match = re.fullmatch(r"iter-(\d+)(?:\.local)?", child.name)
        if match and child.is_dir():
            candidates.append((int(match.group(1)), child))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def extract_composite(eval_payload: Any) -> float | None:
    if not isinstance(eval_payload, dict):
        return None
    paths = (
        ("composite_score",),
        ("scores", "composite_score"),
        ("metric_summary", "composite_score"),
        ("rubric", "composite_score"),
    )
    for path in paths:
        cursor: Any = eval_payload
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor:
                cursor = None
                break
            cursor = cursor[key]
        if isinstance(cursor, (int, float)):
            return float(cursor)
    return None


def extract_rubric_total(eval_payload: Any) -> float | None:
    if not isinstance(eval_payload, dict):
        return None
    components = ((eval_payload.get("rubric") or {}).get("components") or [])
    if not isinstance(components, list) or not components:
        return None
    score = 0.0
    max_points = 0.0
    for component in components:
        if not isinstance(component, dict):
            continue
        if isinstance(component.get("score"), (int, float)):
            score += float(component["score"])
        if isinstance(component.get("max_points"), (int, float)):
            max_points += float(component["max_points"])
    if max_points <= 0:
        return None
    return round((score / max_points) * 100.0, 3)


def extract_final_scorecard_composite(path: Path) -> float | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    matches = re.findall(r"\|\s*\d+[^|]*\|\s*[^|]*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|", text)
    if matches:
        return float(matches[-1])
    root_match = re.search(r"final [^.\n]*?([0-9]+(?:\.[0-9]+)?)\s*mean", text, re.IGNORECASE)
    if root_match:
        return float(root_match.group(1))
    return None


def latest_baseline(source: str, iter_num: int) -> Path | None:
    if iter_num <= 1:
        return find_last_legacy_iter(source)
    for previous in range(iter_num - 1, 0, -1):
        candidate = iter_dir(source, previous)
        if (candidate / "scorecard.json").exists() or (candidate / "eval.json").exists():
            return candidate
    return find_last_legacy_iter(source)
