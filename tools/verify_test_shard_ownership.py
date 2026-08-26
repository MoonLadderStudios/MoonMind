#!/usr/bin/env python3
"""Verify exclusive CI ownership for provider-free backend pytest nodes.

MoonLadderStudios/MoonMind#3324 defines the shard commands mirrored here.
Pytest remains the classification authority: this tool collects nodes through
the repository conftest hooks and evaluates their final marker sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDER_MARKERS = {"provider_verification", "requires_credentials"}


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
    if (
        node.path.startswith("tests/integration/reliability/")
        and "reliability_journey" in markers
    ):
        result.add("reliability-journey-checkpoint-resume")
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

    eligible_count = sum(_eligible(node) for node in collector.nodes)
    print(f"Verified exactly one CI owner for {eligible_count} provider-free nodes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
