#!/usr/bin/env python3
"""Duration-balanced reliability shard partition (MoonLadderStudios/MoonMind#4367).

Files under ``tests/integration/reliability/test_*.py`` are assigned to four
isolated shards by greedy longest-processing-time balancing over advisory
duration hints from ``reliability_shard_weights.json``: the heaviest file
goes to the currently least-loaded shard, ties broken lexicographically so
the partition is deterministic.

This module is the single partition authority shared by CI (the
``backend-matrix`` reliability rows call it as a CLI) and by
``tools/verify_test_shard_ownership.py`` (which imports
:func:`reliability_shard_for_path`). Timing history is an optimization hint
only: files absent from the weights file -- including newly added tests --
receive ``DEFAULT_WEIGHT_SECONDS`` and are always selected, never skipped.

Guardrails: no exact test-count, timing-file freshness, test-filename, or
preferred-wording gates. A missing or unreadable weights file degrades to a
uniform default weight, never to an empty shard selection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RELIABILITY_DIR = REPO_ROOT / "tests" / "integration" / "reliability"
WEIGHTS_PATH = Path(__file__).resolve().parent / "reliability_shard_weights.json"

SHARD_COUNT = 4
SHARD_NAMES = tuple(f"reliability-shard-{index + 1}" for index in range(SHARD_COUNT))


def _load_weights() -> tuple[dict[str, float], float]:
    """Return (per-filename weights, default weight). Never raises."""
    try:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, 20.0
    weights: dict[str, float] = {}
    raw = payload.get("weights", {})
    if isinstance(raw, dict):
        for name, value in raw.items():
            try:
                weights[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
    try:
        default = float(payload.get("DEFAULT_WEIGHT_SECONDS", 20.0))
    except (TypeError, ValueError):
        default = 20.0
    return weights, default


def _reliability_files() -> list[str]:
    try:
        return sorted(p.name for p in RELIABILITY_DIR.glob("test_*.py"))
    except OSError:
        return []


def partition() -> dict[str, list[str]]:
    """Assign every reliability file to exactly one shard, balanced by weight."""
    weights, default = _load_weights()
    files = _reliability_files()
    totals = [0.0] * SHARD_COUNT
    assignment: dict[str, list[str]] = {name: [] for name in SHARD_NAMES}
    # Heaviest first, lexicographic tiebreak for determinism. Pure greedy
    # longest-processing-time: each file joins the currently least-loaded
    # shard, so the heaviest files naturally spread across distinct shards.
    ordered = sorted(files, key=lambda name: (-weights.get(name, default), name))
    for name in ordered:
        shard = min(range(SHARD_COUNT), key=lambda i: (totals[i], i))
        assignment[SHARD_NAMES[shard]].append(name)
        totals[shard] += weights.get(name, default)
    for members in assignment.values():
        members.sort()
    return assignment


def shard_index_for_file(filename: str) -> int:
    """Return the 0-based shard index owning a reliability filename."""
    assignment = partition()
    for index, name in enumerate(SHARD_NAMES):
        if filename in assignment[name]:
            return index
    # File appeared after the partition snapshot (e.g. mid-run listing
    # skew): place it deterministically without dropping it.
    weights, default = _load_weights()
    totals = {
        name: sum(weights.get(member, default) for member in members)
        for name, members in assignment.items()
    }
    return min(range(SHARD_COUNT), key=lambda i: (totals[SHARD_NAMES[i]], i))


def reliability_shard_for_file(filename: str) -> str:
    """Return the deterministic shard name owning a reliability filename."""
    return SHARD_NAMES[shard_index_for_file(filename)]


def files_for_shard(shard: int) -> list[str]:
    """Return the sorted filenames owned by a 0-based shard index."""
    if shard not in range(SHARD_COUNT):
        raise ValueError(f"unknown reliability shard {shard!r}")
    return partition()[SHARD_NAMES[shard]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard",
        required=True,
        help="0-based shard index (0-3); prints one file per line",
    )
    parser.add_argument(
        "--names",
        action="store_true",
        help="print shard names instead of shard files",
    )
    args = parser.parse_args(argv)
    if args.names:
        print("\n".join(SHARD_NAMES))
        return 0
    try:
        shard = int(args.shard)
    except ValueError:
        print(f"error: unknown reliability shard {args.shard!r}", file=sys.stderr)
        return 2
    try:
        files = files_for_shard(shard)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for name in files:
        print(f"tests/integration/reliability/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
