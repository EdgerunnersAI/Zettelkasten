"""Persist one eval URL through the canonical Add Zettel backend runner."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docs.summary_eval_v2.scripts._common import iter_dir, write_json  # noqa: E402


async def _run(args: argparse.Namespace) -> dict:
    from website.api.module_runners.summarization import run_add_zettel_pipeline

    return await run_add_zettel_pipeline(
        url=args.url,
        client_action_id=args.client_action_id
        or f"summary-eval-v2-{args.source}-iter-{args.iter_num:02d}",
        persist=True,
        user={"sub": str(args.user_id)},
        effective_user_id=UUID(str(args.user_id)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--iter", required=True, type=int, dest="iter_num")
    parser.add_argument("--url", required=True)
    parser.add_argument("--user-id", required=True, help="Naruto Supabase Auth UUID")
    parser.add_argument("--client-action-id")
    args = parser.parse_args()

    result = asyncio.run(_run(args))
    out_dir = iter_dir(args.source, args.iter_num)
    write_json(out_dir / "naruto_cli_response.json", result)
    print(json.dumps({"artifact": str(out_dir / "naruto_cli_response.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
