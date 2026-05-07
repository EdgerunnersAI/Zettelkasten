"""iter-12 Task 18: SHA-256-lock the composite_weights.yaml file.

Computes hash of the `weights_by_iter` block and writes to the `hash` field
in-place. Intended to run before commit (manually or via pre-commit hook).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "docs" / "rag_eval" / "_config" / "composite_weights.yaml"


def main() -> int:
    cfg = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    payload = yaml.safe_dump(cfg["weights_by_iter"], sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    cfg["hash"] = digest
    PATH.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"hash={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
