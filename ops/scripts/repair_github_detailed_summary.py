# LEGACY (broken after 2026-05-11): references dropped public.kg_nodes. To revive,
# port to website.core.supabase_v2 / content_nodes.summary. Tracked for follow-up.
"""Repair script for github rows whose ``summary.detailed_summary`` is a
Python repr of a list-of-dict (iter-23 regression) rather than either a
JSON array or a markdown string.

Root cause: ``register_iter23_github_naruto.py`` stringified a list via
``"\n".join(f"- {item}" for item in detailed_raw)``, producing
``"- {'heading': 'Overview', ...}"`` — Python's ``str(dict)`` output. The
frontend renderer only accepts a true JavaScript array, so this fell
through to the markdown renderer and displayed raw Python dict syntax.

This script is idempotent: rows whose ``detailed_summary`` is already
JSON-parseable (list/dict) or a plain string are skipped.

Usage:
    python ops/scripts/repair_github_detailed_summary.py

Requires ``supabase/.env`` with ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY``.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

MAIN_ROOT = Path(r"C:\Users\LENOVO\Documents\Claude_Code\Projects\Obsidian_Vault")
load_dotenv(MAIN_ROOT / "supabase" / ".env", override=False)

WORKTREE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKTREE))


def _coerce_detailed_to_markdown(value) -> str:
    """Mirror of website.core.persist._coerce_detailed_to_markdown.

    Imported inline rather than via the live module to keep the repair
    script self-contained and avoid the settings validation that
    importing website.core triggers.
    """
    if value is None or isinstance(value, str):
        return value or ""
    if isinstance(value, list):
        sections = value
    elif isinstance(value, dict):
        sections = [value]
    else:
        return ""
    lines: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        if lines:
            lines.append("")
        if heading:
            lines.append(f"## {heading}")
        bullets = section.get("bullets") or []
        if isinstance(bullets, list):
            for bullet in bullets:
                text = str(bullet).strip()
                if text:
                    lines.append(f"- {text}")
        sub = section.get("sub_sections") or section.get("subSections") or {}
        if isinstance(sub, dict):
            for sub_heading, sub_bullets in sub.items():
                if not isinstance(sub_bullets, list) or not sub_bullets:
                    continue
                lines.append("")
                lines.append(f"### {str(sub_heading).strip()}")
                for bullet in sub_bullets:
                    text = str(bullet).strip()
                    if text:
                        lines.append(f"- {text}")
    return "\n".join(lines).strip()


def _needs_repair(detailed) -> bool:
    """True only when detailed is a string starting with '[{' and CANNOT be
    parsed as JSON (i.e. it's a Python repr, not a JSON array)."""
    if not isinstance(detailed, str):
        return False
    stripped = detailed.lstrip()
    # Also handle the leading "- " prefix the bad register script added.
    if stripped.startswith("- "):
        stripped = stripped[2:].lstrip()
    if not stripped.startswith("[{") and not stripped.startswith("{'"):
        return False
    # Is it already valid JSON? If so, don't touch.
    try:
        json.loads(stripped)
        return False
    except Exception:
        pass
    return "'" in stripped


def _extract_python_literal(detailed: str):
    """Parse the Python-repr form back into a real list/dict."""
    # Strip any "- " list prefix that the bad register script injected before
    # the dict. The bad form can also have one "- {" per section separated by
    # newlines, which ast.literal_eval cannot parse directly. Detect and
    # reconstruct a list when we see that shape.
    text = detailed.strip()
    if "\n- " in text or text.startswith("- "):
        # One dict per line prefixed with "- "; rebuild into a list.
        parts: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("- "):
                line = line[2:].strip()
            parts.append(line)
        text = "[" + ", ".join(parts) + "]"
    return ast.literal_eval(text)


def _repair_row(row: dict) -> tuple[str, dict | None]:
    """Return (bucket, updated_summary_dict_or_None)."""
    summary_raw = row.get("summary")
    if not isinstance(summary_raw, str):
        return "UNPARSEABLE", None
    try:
        summary = json.loads(summary_raw)
    except Exception:
        return "UNPARSEABLE", None
    if not isinstance(summary, dict):
        return "UNPARSEABLE", None

    detailed = summary.get("detailed_summary")
    if not _needs_repair(detailed):
        return "ALREADY_JSON", None

    try:
        structured = _extract_python_literal(detailed)
    except Exception as exc:
        print(f"  ast.literal_eval failed: {exc}")
        return "UNPARSEABLE", None

    markdown = _coerce_detailed_to_markdown(structured)
    if not markdown:
        return "UNPARSEABLE", None

    summary["detailed_summary"] = markdown
    return "REPAIRED", summary


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing", file=sys.stderr)
        return 2

    from supabase import create_client

    client = create_client(url, key)
    rows = (
        client.table("kg_nodes")
        .select("id,name,source_type,summary,user_id")
        .eq("source_type", "github")
        .execute()
        .data
    )
    print(f"Scanning {len(rows)} github rows...")

    counts = {"REPAIRED": 0, "ALREADY_JSON": 0, "UNPARSEABLE": 0}
    for row in rows:
        bucket, updated = _repair_row(row)
        counts[bucket] += 1
        print(f"  {row['id']}: {bucket}")
        if bucket == "REPAIRED" and updated is not None:
            new_summary = json.dumps(updated, ensure_ascii=False)
            resp = (
                client.table("kg_nodes")
                .update({"summary": new_summary})
                .eq("id", row["id"])
                .eq("user_id", row["user_id"])
                .execute()
            )
            if not resp.data:
                print(f"    WARN: update returned no rows for {row['id']}")

    print()
    print("--- Summary ---")
    for bucket, count in counts.items():
        print(f"  {bucket}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
