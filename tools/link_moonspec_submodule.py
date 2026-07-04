#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sync_moonspec_submodule import DEFAULT_SOURCE, main as sync_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Legacy MoonSpec projection entrypoint")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projection", default="moonmind")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Accepted for the base-branch CI workflow; pruning is handled by sync.",
    )
    args = parser.parse_args(argv)

    sync_args = ["--source", str(args.source), "--projection", args.projection]
    sync_args.append("--check" if args.check else "--write")
    return sync_main(sync_args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
