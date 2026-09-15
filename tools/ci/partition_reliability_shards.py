#!/usr/bin/env python3
"""Print the reliability test files owned by one duration-balanced shard.

MoonLadderStudios/MoonMind#4367: the backend-matrix reliability step calls
this instead of file-count round-robin (`awk 'NR % 4 == ...'`) so CI executes
each file in the same shard that tools/verify_test_shard_ownership.py owns
it in. Timing hints are an optimization hint only; unknown files still print
exactly once via the default weight. Pure stdlib so it runs before/after any
dependency install.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.ci.reliability_shard_partition import (  # noqa: E402
    SHARD_COUNT,
    SHARD_NAMES,
    partition,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard",
        required=True,
        help="Shard index 0..3 (matches the matrix `shard` value).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=SHARD_COUNT,
        help="Expected shard count (must match the matrix).",
    )
    args = parser.parse_args(argv)
    try:
        shard = int(args.shard)
    except ValueError:
        print(f"error: shard must be an integer, got {args.shard!r}", file=sys.stderr)
        return 2
    if args.count != SHARD_COUNT or shard not in range(SHARD_COUNT):
        print(
            f"error: unknown reliability shard {args.shard!r} "
            f"(expected 0..{SHARD_COUNT - 1})",
            file=sys.stderr,
        )
        return 2
    assignment = partition()
    wanted = SHARD_NAMES[shard]
    for filename in sorted(assignment):
        if assignment[filename] == wanted:
            print(f"tests/integration/reliability/{filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
