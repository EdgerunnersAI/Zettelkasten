"""Phase 8.5.R4 — ACL-001 sunset fitness function.

Architectural ratchet: prevents NEW consumers of the legacy
`Candidate.node_id` alias / `candidate_to_legacy_dict` projector AND fails
when an existing consumer is fixed (forces the allowlist to stay honest).

Pattern: Thoughtworks Tech Radar Vol. 31–34 architectural fitness function +
pytest-archon-style AST walk. AWS Prescriptive Guidance ACL pattern requires
"decommission after all dependent services have been migrated"; this test
makes the decommission state machine-checkable.

Sunset trigger (per docs/db-v2/tech-debt-tracker.md ACL-001):
  When ALLOWED_VIOLATING_FILES is empty, also delete:
    * `node_id` property on each *Candidate subclass
    * `candidate_to_legacy_dict()`
    * `chunk_from_v2_row(... default_rrf_score=...)` knob
    * the ACL-001 entry from tech-debt-tracker.md
    * this test file
"""
from __future__ import annotations

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RAG_ROOT = REPO_ROOT / "website" / "features" / "rag_pipeline"

# Files permitted to define/expose the alias itself. Keep TIGHT.
ALIAS_DEFINITION_ALLOWLIST: set[str] = {
    "website/features/rag_pipeline/retrieval/candidate_model.py",
}

# RATCHET: known consumers as of 2026-05-10 per AST scan. Decrement entries as
# the cleanup lands per-file. When this set is empty, the alias + projector +
# this test file are all eligible for deletion.
ALLOWED_VIOLATING_FILES: set[str] = {
    "website/features/rag_pipeline/retrieval/graph_score.py",
    "website/features/rag_pipeline/orchestrator.py",
    "website/features/rag_pipeline/rerank/cascade.py",
}

BANNED_ATTRS = {"node_id"}
BANNED_CALLS = {"candidate_to_legacy_dict"}


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def _collect_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (lineno, symbol) for ACL-001 violations in `path`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # `something.node_id` on any expression — over-broad, but the alias
        # is intentionally short to make this fitness test cheap.
        if isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            out.append((node.lineno, f".{node.attr}"))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in BANNED_CALLS
        ):
            out.append((node.lineno, node.func.id + "()"))
    return out


def _files_with_violations() -> set[str]:
    found: set[str] = set()
    for py in RAG_ROOT.rglob("*.py"):
        rel = _rel(py)
        if rel in ALIAS_DEFINITION_ALLOWLIST:
            continue
        if _collect_violations(py):
            found.add(rel)
    return found


def test_acl_001_no_new_violations():
    """No new file may introduce Candidate.node_id / candidate_to_legacy_dict use.

    Combined with `test_acl_001_allowlist_stays_honest`, this is a *ratchet*:
    additions blocked, removals enforced.
    """
    found = _files_with_violations()
    new_violations = found - ALLOWED_VIOLATING_FILES
    assert not new_violations, (
        "NEW ACL-001 violations introduced (use typed Candidate fields instead):\n  "
        + "\n  ".join(sorted(new_violations))
        + "\n\nSee docs/db-v2/tech-debt-tracker.md ACL-001."
    )


def test_acl_001_allowlist_stays_honest():
    """Files removed from real codebase MUST be removed from the allowlist.

    Without this guard, the allowlist would silently grow stale. When this
    test fails because the allowlist names a file with no violations, that's
    success — celebrate the fix, then prune the allowlist.
    """
    found = _files_with_violations()
    stale = ALLOWED_VIOLATING_FILES - found
    assert not stale, (
        "ACL-001 violations resolved in:\n  "
        + "\n  ".join(sorted(stale))
        + "\n\nRemove these entries from ALLOWED_VIOLATING_FILES in "
        "tests/architecture/test_acl_001_sunset.py — and when the set is "
        "empty, delete the alias + this test file (see file docstring)."
    )
