"""Schema-drift guard: Supabase SQL schema vs Pydantic KG models.

Fails if columns exist in one side but not the other (beyond the curated
skip-lists below). The authoritative DB schema is the union of the base
`schema.sql` and the feature/engine migrations that ADD COLUMNs to KG tables.

When drift is intentional, acknowledge it by adding the column/field to the
appropriate skip-list with a short comment explaining why.
"""
from __future__ import annotations

import re
from pathlib import Path

from website.core.supabase_kg.models import KGLink, KGNode, KGUser

REPO_ROOT = Path(__file__).resolve().parents[4]

# SQL files that together define the authoritative KG schema.
SCHEMA_FILES = [
    REPO_ROOT / "supabase" / "website" / "kg_public" / "schema.sql",
    REPO_ROOT / "supabase" / "website" / "kg_features" / "001_intelligence.sql",
    REPO_ROOT / "supabase" / "website" / "summarization_engine" / "001_engine_v2.sql",
]

# Columns present in SQL but intentionally absent from the Pydantic model.
# These are DB-managed internals the application layer never reads/writes.
DB_ONLY_COLUMNS: dict[str, set[str]] = {
    "kg_users": set(),
    "kg_nodes": {
        "fts",          # generated tsvector column for full-text search; server-side only
        "aliases_flat", # GENERATED ALWAYS AS column for trgm index; Python never reads/writes it
    },
    "kg_links": {
        # iter-08 Phase 8: enum column for future edge-weighted PageRank.
        # No Python writer populates it yet; iter-09 will add ingestion logic
        # and the corresponding KGLink field.
        "relation_type",
    },
}

# Pydantic fields intentionally absent from SQL (e.g. computed/client-only).
# Empty today — add with rationale if ever needed.
MODEL_ONLY_FIELDS: dict[str, set[str]] = {
    "kg_users": set(),
    "kg_nodes": set(),
    "kg_links": set(),
}

TABLE_TO_MODEL = {
    "kg_users": KGUser,
    "kg_nodes": KGNode,
    "kg_links": KGLink,
}

# Non-column tokens that can appear in a column-definition list.
_NON_COLUMN_KEYWORDS = {
    "primary", "foreign", "unique", "check", "constraint", "exclude",
}


def _strip_sql_comments(sql: str) -> str:
    """Remove `-- line comments` and `/* block */` comments."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def _parse_create_table(sql: str, table: str) -> set[str]:
    """Extract column names from the first CREATE TABLE <table> (...) block."""
    pattern = re.compile(
        rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?{table}\s*\((?P<body>.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(sql)
    if not m:
        return set()
    body = m.group("body")
    cols: set[str] = set()
    # Split on commas at paren-depth 0.
    depth = 0
    buf: list[str] = []
    parts: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    for raw in parts:
        line = raw.strip()
        if not line:
            continue
        first = line.split()[0].strip('"').lower()
        if first in _NON_COLUMN_KEYWORDS:
            continue
        cols.add(first)
    return cols


def _parse_add_columns(sql: str, table: str) -> set[str]:
    """Extract columns added via `ALTER TABLE <table> ADD COLUMN ...`."""
    # Match the whole ALTER TABLE statement (up to the terminating ;) then
    # pull each ADD COLUMN column name out.
    alter_re = re.compile(
        rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?{table}\b(?P<body>.*?);",
        re.IGNORECASE | re.DOTALL,
    )
    add_re = re.compile(
        r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<col>[A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    cols: set[str] = set()
    for m in alter_re.finditer(sql):
        for c in add_re.finditer(m.group("body")):
            cols.add(c.group("col").lower())
    return cols


def _collect_sql_columns(table: str) -> set[str]:
    cols: set[str] = set()
    for path in SCHEMA_FILES:
        assert path.exists(), f"Expected SQL file not found: {path}"
        sql = _strip_sql_comments(path.read_text(encoding="utf-8"))
        cols |= _parse_create_table(sql, table)
        cols |= _parse_add_columns(sql, table)
    return cols


def _model_fields(table: str) -> set[str]:
    model = TABLE_TO_MODEL[table]
    return set(model.model_fields.keys())


def _format_drift(table: str, sql_cols: set[str], model_cols: set[str]) -> str:
    missing_in_model = sql_cols - model_cols - DB_ONLY_COLUMNS[table]
    missing_in_sql = model_cols - sql_cols - MODEL_ONLY_FIELDS[table]
    lines = [f"Schema drift detected for {table}:"]
    if missing_in_model:
        lines.append(
            f"  SQL columns missing from Pydantic model "
            f"{TABLE_TO_MODEL[table].__name__}: {sorted(missing_in_model)}"
        )
    if missing_in_sql:
        lines.append(
            f"  Pydantic fields on {TABLE_TO_MODEL[table].__name__} "
            f"missing from SQL schema: {sorted(missing_in_sql)}"
        )
    lines.append(
        "  Fix the mismatch or add the name to DB_ONLY_COLUMNS / "
        "MODEL_ONLY_FIELDS with rationale."
    )
    return "\n".join(lines)


def _assert_no_drift(table: str) -> None:
    sql_cols = _collect_sql_columns(table)
    assert sql_cols, f"Failed to parse any columns for {table} — regex broken?"
    model_cols = _model_fields(table)
    drift_sql_side = (sql_cols - model_cols) - DB_ONLY_COLUMNS[table]
    drift_model_side = (model_cols - sql_cols) - MODEL_ONLY_FIELDS[table]
    assert not drift_sql_side and not drift_model_side, _format_drift(
        table, sql_cols, model_cols
    )


def test_kg_users_schema_matches_model() -> None:
    _assert_no_drift("kg_users")


def test_kg_nodes_schema_matches_model() -> None:
    _assert_no_drift("kg_nodes")


def test_kg_links_schema_matches_model() -> None:
    _assert_no_drift("kg_links")
