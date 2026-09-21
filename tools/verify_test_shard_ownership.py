#!/usr/bin/env python3
"""Verify exclusive CI ownership for provider-free backend pytest nodes.

MoonLadderStudios/MoonMind#3324 defines the shard commands mirrored here.
Pytest remains the classification authority: this tool collects nodes through
the repository conftest hooks and evaluates their final marker sets.

Reliability coverage has two layers (MoonLadderStudios/MoonMind#4366):

- Logical ownership: every reliability journey node is owned by the single
  ``reliability-matrix`` aggregate (the backend-matrix job fans out to four
  isolated shards). Duration history only balances load; it never moves a
  case off the corpus or skips it.
- Physical partitioning: the union of the four pytest-split ``--group``
  collections (the actual plugin CLI, same flags as CI) must equal the
  collected unsharded reliability universe, with pairwise intersections
  empty. This exercises the real collection behavior rather than copying the
  splitting algorithm into a second scheduler.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from tools.ci.refresh_reliability_durations import (
    RELIABILITY_MARKER_EXPR,
    durations_pytest_args,
)

PROVIDER_MARKERS = {"provider_verification", "requires_credentials"}

# Logical owner for every reliability journey node: the backend-matrix
# aggregate fans out to the four physical groups below.
RELIABILITY_MATRIX_OWNER = "reliability-matrix"
# pytest-split groups are 1-based; suite reliability-shard-N runs --group N.
RELIABILITY_SHARD_COUNT = 4
RELIABILITY_SHARD_NAMES = tuple(
    f"reliability-shard-{index}" for index in range(1, RELIABILITY_SHARD_COUNT + 1)
)
SPLIT_GROUP_IDS = tuple(range(1, RELIABILITY_SHARD_COUNT + 1))

RELIABILITY_PREFIX = "tests/integration/reliability/"


class PluginUnavailable(RuntimeError):
    """pytest-split is not installed, so the physical check cannot run."""


def _run_collection_command(
    argv: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a collection subprocess. Thin wrapper so tests can fake the CLI."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(cwd or REPO_ROOT),
    )


@dataclass(frozen=True)
class CollectedNode:
    nodeid: str
    path: str
    markers: frozenset[str]


class _Collector:
    def __init__(self) -> None:
        self.nodes: list[CollectedNode] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.nodes = [
            CollectedNode(
                nodeid=item.nodeid,
                path=Path(str(item.fspath)).resolve().relative_to(REPO_ROOT).as_posix(),
                markers=frozenset(marker.name for marker in item.iter_markers()),
            )
            for item in session.items
        ]


def _eligible(node: CollectedNode) -> bool:
    if node.markers & PROVIDER_MARKERS:
        return False
    if node.path.startswith("tests/unit/"):
        return True
    if node.path.startswith("tests/component/api/"):
        return True
    return bool(
        node.path.startswith("tests/integration/")
        and node.markers & {"integration_ci", "reliability_journey"}
    )


def owners(node: CollectedNode) -> set[str]:
    """Return the CI shard commands that select a collected node."""

    markers = node.markers
    result: set[str] = set()

    if (
        node.path.startswith("tests/unit/")
        and not node.path.startswith(
            (
                "tests/unit/workflows/temporal/",
                "tests/unit/api/",
                "tests/unit/api_service/",
            )
        )
        and "unit_fast" in markers
    ):
        result.add("unit-fast")
    if (
        node.path.startswith("tests/unit/")
        and "slow" in markers
        and "integration" not in markers
    ):
        result.add("unit-slow")
    if (
        node.path.startswith(
            ("tests/unit/api/", "tests/unit/api_service/", "tests/component/api/")
        )
        and "component" in markers
        and not markers & {"temporal_boundary", "slow"}
    ):
        result.add("api-component")
    if (
        node.path.startswith("tests/unit/workflows/temporal/")
        and "temporal_boundary" in markers
        and "slow" not in markers
    ):
        result.add("temporal-boundary")
    if node.path.startswith(RELIABILITY_PREFIX) and "reliability_journey" in markers:
        result.add(RELIABILITY_MATRIX_OWNER)
    if "integration_ci" in markers:
        result.add("integration-ci")
    return result


def verify(nodes: list[CollectedNode]) -> list[str]:
    errors: list[str] = []
    for node in nodes:
        markers = node.markers
        if "unit_fast" in markers and markers & {
            "component",
            "temporal_boundary",
            "slow",
            "provider_verification",
            "requires_credentials",
        }:
            errors.append(
                f"{node.nodeid}: unit_fast conflicts with ownership marker(s)"
            )
        if "integration_ci" in markers and "reliability_journey" in markers:
            errors.append(
                f"{node.nodeid}: integration_ci conflicts with reliability_journey"
            )
        if not _eligible(node):
            continue
        node_owners = owners(node)
        if not node_owners:
            errors.append(f"{node.nodeid}: no CI owner")
        elif len(node_owners) > 1:
            errors.append(
                f"{node.nodeid}: multiple CI owners: {', '.join(sorted(node_owners))}"
            )
    return errors


def collect_group_node_ids(group: int, *, durations_args: list[str]) -> set[str]:
    """Collect one pytest-split group through the actual plugin CLI.

    Uses the same flags as the CI reliability rows. Raises
    PluginUnavailable when pytest-split is not installed; raises
    RuntimeError when collection itself fails (a collection error must never
    silently shrink the corpus).
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        RELIABILITY_PREFIX,
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "-m",
        RELIABILITY_MARKER_EXPR,
        "--splits",
        str(RELIABILITY_SHARD_COUNT),
        "--group",
        str(group),
        "--splitting-algorithm",
        "least_duration",
        *durations_args,
    ]
    proc = _run_collection_command(argv)
    if proc.returncode in (0, 5):
        return {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    combined = proc.stdout + proc.stderr
    if "--splits" in combined and "unrecognized arguments" in combined:
        raise PluginUnavailable(
            "pytest-split is not installed; install the tests extra to run "
            "the physical reliability partition check"
        )
    raise RuntimeError(
        f"reliability group {group} collection failed with exit "
        f"{proc.returncode}:\n{combined[-2000:]}"
    )


def verify_physical_partitions(
    universe: set[str], groups: dict[int, set[str]]
) -> list[str]:
    """Check that group node IDs partition the universe exactly once."""
    errors: list[str] = []
    covered: set[str] = set()
    for group in sorted(groups):
        for nodeid in sorted(groups[group]):
            if nodeid in covered:
                errors.append(
                    f"{nodeid}: selected by multiple reliability groups"
                )
            covered.add(nodeid)
    for nodeid in sorted(universe - covered):
        errors.append(
            f"{nodeid}: missing from every reliability group; "
            "the shard would silently drop this test"
        )
    for nodeid in sorted(covered - universe):
        errors.append(
            f"{nodeid}: selected by a reliability group but absent from the "
            "collected reliability universe"
        )
    return errors


def verify_physical_reliability_partitions(
    nodes: list[CollectedNode],
) -> tuple[list[str], bool]:
    """Collect groups 1-4 via the plugin CLI and check exact-once coverage.

    Returns (errors, skipped): skipped is True only when pytest-split is not
    installed, which warns instead of producing a silently green proof.
    Shares the warn-and-fallback durations behavior with CI through
    durations_pytest_args, so an unusable hint file never skips tests here
    either.
    """
    universe = {
        node.nodeid
        for node in nodes
        if node.path.startswith(RELIABILITY_PREFIX)
        and "reliability_journey" in node.markers
        and not node.markers & PROVIDER_MARKERS
    }
    if not universe:
        return ([], False)
    durations_args = durations_pytest_args()
    try:
        groups = {
            group: collect_group_node_ids(group, durations_args=durations_args)
            for group in SPLIT_GROUP_IDS
        }
    except PluginUnavailable as exc:
        print(f"warning: physical reliability partition check skipped: {exc}")
        return ([], True)
    return (verify_physical_partitions(universe, groups), False)


def main() -> int:
    collector = _Collector()
    result = pytest.main(
        [
            "tests/unit",
            "tests/component/api",
            "tests/integration",
            "--collect-only",
            "-q",
        ],
        plugins=[collector],
    )
    if result != pytest.ExitCode.OK:
        print(f"pytest collection failed with exit code {int(result)}")
        return 2

    errors = verify(collector.nodes)
    if errors:
        print("Backend test shard ownership verification failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    physical_errors, skipped = verify_physical_reliability_partitions(collector.nodes)
    if physical_errors:
        print("Reliability physical partition verification failed:")
        for error in physical_errors:
            print(f"  - {error}")
        return 1

    eligible_count = sum(_eligible(node) for node in collector.nodes)
    print(f"Verified exactly one CI owner for {eligible_count} provider-free nodes.")
    if skipped:
        print("Physical reliability partition check skipped (pytest-split unavailable).")
    else:
        print(
            "Verified the four pytest-split reliability groups partition "
            "the collected reliability universe exactly once."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
