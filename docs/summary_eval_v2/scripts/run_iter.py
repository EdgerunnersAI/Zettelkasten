"""Run summary_eval_v2 Phase A against the current summarization pipeline."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docs.summary_eval_v2.scripts._common import EVAL_ROOT, iter_dir, source_urls  # noqa: E402


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("==="):
            continue
        sep_idx = -1
        for sep in (":", "="):
            idx = line.find(sep)
            if idx != -1 and (sep_idx == -1 or idx < sep_idx):
                sep_idx = idx
        if sep_idx <= 0:
            continue
        key = line[:sep_idx].strip()
        value = line[sep_idx + 1:].strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


def _load_eval_env(repo_root: Path) -> None:
    for candidate in (
        repo_root / ".env",
        repo_root / ".env.v2",
        repo_root / "supabase" / ".env",
        repo_root / "new_envs.txt",
    ):
        _load_env_file(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--iter", required=True, type=int, dest="iter_num")
    parser.add_argument("--phase", choices=["a"], default="a")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-liveness", action="store_true", default=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    _load_eval_env(repo_root)
    if args.skip_liveness:
        os.environ.setdefault("EVAL_SKIP_LIVENESS", "1")

    urls = args.url or source_urls(args.source)
    if not urls:
        raise SystemExit(f"No URLs configured for {args.source}")

    from ops.scripts.lib.gemini_factory import make_client as make_gemini_client
    from ops.scripts.lib.phases import run_phase_a

    out_dir = iter_dir(args.source, args.iter_num)
    out_dir.mkdir(parents=True, exist_ok=True)
    rubric_yaml = repo_root / "docs" / "summary_eval" / "_config" / f"rubric_{args.source}.yaml"
    if not rubric_yaml.exists():
        rubric_yaml = repo_root / "docs" / "summary_eval" / "_config" / "rubric_universal.yaml"
    cache_root = EVAL_ROOT / "_cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    payload = run_phase_a(
        source=args.source,
        iter_num=args.iter_num,
        urls=urls,
        iter_dir=out_dir,
        rubric_path=rubric_yaml,
        cache_root=cache_root,
        gemini_client_factory=make_gemini_client,
        held_out=len(urls) > 1,
        env=args.env,
        force=args.force,
    )
    from docs.summary_eval_v2.scripts._common import write_json

    write_json(out_dir / "run_result.json", payload)
    print(str(out_dir / "run_result.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
