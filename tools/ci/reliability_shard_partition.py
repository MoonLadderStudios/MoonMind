"""Duration-balanced reliability shard partition (pure stdlib).

MoonLadderStudios/MoonMind#4367: single source of truth for assigning
tests/integration/reliability/test_*.py files to the four isolated backend
matrix shards. Both the CI partition CLI
(tools/ci/partition_reliability_shards.py) and the ownership verifier
(tools/verify_test_shard_ownership.py) consume this module so local checks
and CI execute each file in the same shard.

Timing hints (tools/ci/reliability_shard_timings.json) are an optimization
hint only: unknown, new, renamed, or stale entries fall back to the default
weight, every discovered file is assigned to exactly one shard, and no exact
test-count, timing freshness, filename, or preferred-wording gate is added.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SHARD_COUNT = 4
SHARD_NAMES = tuple(f"reliability-shard-{index + 1}" for index in range(SHARD_COUNT))

REPO_ROOT = Path(__file__).resolve().parents[2]
TIMINGS_PATH = REPO_ROOT / "tools" / "ci" / "reliability_shard_timings.json"
RELIABILITY_DIR = REPO_ROOT / "tests" / "integration" / "reliability"


def load_weights() -> tuple[dict[str, float], float]:
    try:
        payload = json.loads(TIMINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, 15.0
    raw_weights = payload.get("weights", {})
    try:
        default_weight = float(payload.get("default_weight_seconds", 15.0))
    except (TypeError, ValueError):
        default_weight = 15.0
    weights: dict[str, float] = {}
    if isinstance(raw_weights, dict):
        for name, value in raw_weights.items():
            try:
                weights[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
    return weights, default_weight


def discover_files() -> list[str]:
    try:
        return sorted(p.name for p in RELIABILITY_DIR.glob("test_*.py"))
    except OSError:
        return []


def partition(
    candidates: list[str] | None = None,
    weights: dict[str, float] | None = None,
    default_weight: float | None = None,
) -> dict[str, str]:
    """Greedy longest-processing-time assignment (deterministic)."""
    if candidates is None:
        candidates = discover_files()
    if weights is None or default_weight is None:
        loaded_weights, loaded_default = load_weights()
        weights = loaded_weights if weights is None else weights
        default_weight = loaded_default if default_weight is None else default_weight
    loads = [0.0] * SHARD_COUNT
    assignment: dict[str, str] = {}
    for filename in sorted(
        candidates, key=lambda n: (-weights.get(n, default_weight), n)
    ):
        index = min(range(SHARD_COUNT), key=lambda i: (loads[i], i))
        loads[index] += weights.get(filename, default_weight)
        assignment[filename] = SHARD_NAMES[index]
    return assignment


def shard_loads(
    assignment: dict[str, str], weights: dict[str, float], default_weight: float
) -> dict[str, float]:
    loads = {name: 0.0 for name in SHARD_NAMES}
    for filename, shard in assignment.items():
        loads[shard] += weights.get(filename, default_weight)
    return loads


def shard_for_path(path: str, assignment: dict[str, str] | None = None) -> str:
    filename = path.rsplit("/", 1)[-1]
    if assignment is None:
        assignment = partition()
    if filename in assignment:
        return assignment[filename]
    digest = int(hashlib.md5(path.encode("utf-8")).hexdigest(), 16)
    return SHARD_NAMES[digest % SHARD_COUNT]
