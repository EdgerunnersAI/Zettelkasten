"""One-off driver: re-run iter-09's 5 held-out newsletter URLs on current HEAD.

Writes artifacts to docs/summary_eval/newsletter/iter-11/.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _load_new_envs() -> None:
    """Parse new_envs.txt KEY:VALUE pairs into os.environ (don't overwrite)."""
    path = REPO_ROOT / "new_envs.txt"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("==="):
            continue
        # accept K:V or K=V; only first separator splits
        sep_idx = -1
        for sep in (":", "="):
            i = line.find(sep)
            if i != -1 and (sep_idx == -1 or i < sep_idx):
                sep_idx = i
        if sep_idx <= 0:
            continue
        k = line[:sep_idx].strip()
        v = line[sep_idx + 1 :].strip()
        if k and v and k not in os.environ:
            os.environ[k] = v


def main() -> int:
    # W7: --force lets the operator bypass the phase_a-done idempotency
    # short-circuit (useful when intentionally re-evaluating on a new HEAD).
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run phase_a even if run.log already shows it completed.",
    )
    args = parser.parse_args()

    _load_new_envs()
    iter_dir_early = REPO_ROOT / "docs" / "summary_eval" / "newsletter" / "iter-11"
    from ops.scripts.lib.driver_logging import setup_driver_logging
    setup_driver_logging(iter_dir_early)
    # fail-fast tuning per eval_loop._apply_eval_key_pool_overrides;
    # role filter intentionally omitted — CSV-loaded keys are tagged 'free'
    # by gemini_factory.make_client and would otherwise be filtered out.
    # Operator has authorized billing spend, so we pool all available keys.
    os.environ.pop("GEMINI_KEY_ROLE_FILTER", None)
    os.environ.setdefault("GEMINI_MAX_RETRIES", "1")
    os.environ.setdefault("GEMINI_RATE_LIMIT_COOLDOWN_SECS", "75")
    os.environ.setdefault("GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS", "1")
    # Skip liveness in driver (we trust iter-09's URL set)
    os.environ.setdefault("EVAL_SKIP_LIVENESS", "1")

    from ops.scripts.lib.gemini_factory import make_client as make_gemini_client
    from ops.scripts.lib.phases import run_phase_a

    urls = [
        "https://www.platformer.news/substack-nazi-push-notification/",
        "https://organicsynthesis.beehiiv.com/p/organic-synthesis-beehiiv",
        "https://product.beehiiv.com/p/introducing-email-boosts",
        "https://newsletter.pragmaticengineer.com/p/the-product-minded-engineer",
        "https://product.beehiiv.com/p/beehiiv-mcp",
    ]
    iter_dir = REPO_ROOT / "docs" / "summary_eval" / "newsletter" / "iter-11"
    iter_dir.mkdir(parents=True, exist_ok=True)
    rubric_path = REPO_ROOT / "docs" / "summary_eval" / "_config" / "rubric_newsletter.yaml"
    cache_root = REPO_ROOT / "docs" / "summary_eval" / "_cache"

    payload = run_phase_a(
        source="newsletter",
        iter_num=11,
        urls=urls,
        iter_dir=iter_dir,
        rubric_path=rubric_path,
        cache_root=cache_root,
        gemini_client_factory=make_gemini_client,
        held_out=True,
        env="dev",
        force=args.force,
    )
    import json
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
