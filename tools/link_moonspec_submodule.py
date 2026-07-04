#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

import sync_moonspec_submodule


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compatibility entrypoint for MoonSpec projection checks."
    )
    parser.add_argument("--source")
    parser.add_argument("--projection", default="moonmind")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--replace-generated", action="store_true")
    args = parser.parse_args(argv)

    forwarded = ["--projection", args.projection]
    if args.source:
        forwarded.extend(["--source", args.source])
    forwarded.append("--write" if args.write else "--check")
    return sync_moonspec_submodule.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
