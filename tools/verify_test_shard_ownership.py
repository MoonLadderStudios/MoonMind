#!/usr/bin/env python3
"""Verify exclusive provider-free backend pytest ownership (GitHub #3324)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


EXCLUDED_MARKERS = {"provider_verification", "requires_credentials"}


@dataclass(frozen=True)
class CollectedTest:
    nodeid: str
    path: str
    markers: frozenset[str]


def shard_owners(test: CollectedTest) -> tuple[str, ...]:
    markers = test.markers
    unit_test = test.path.startswith("tests/unit/")
    component_test = test.path.startswith("tests/component/") or unit_test
    owners: list[str] = []

    if unit_test and "slow" in markers and "integration" not in markers:
        owners.append("unit-slow")
    if (
        unit_test
        and "temporal_boundary" in markers
        and "slow" not in markers
    ):
        owners.append("temporal-boundary")
    if (
        component_test
        and "component" in markers
        and "temporal_boundary" not in markers
        and "slow" not in markers
    ):
        owners.append("api-component")
    if unit_test and "unit_fast" in markers:
        owners.append("unit-fast")
    if "reliability_journey" in markers:
        owners.append("reliability-journey-checkpoint-resume")
    if "integration_ci" in markers:
        owners.append("integration-ci")
    return tuple(owners)


def is_eligible(test: CollectedTest) -> bool:
    if test.markers & EXCLUDED_MARKERS:
        return False
    return (
        test.path.startswith(("tests/unit/", "tests/component/"))
        or "integration_ci" in test.markers
        or "reliability_journey" in test.markers
    )


def ownership_errors(tests: list[CollectedTest]) -> list[str]:
    errors: list[str] = []
    for test in tests:
        markers = test.markers
        if "unit_fast" in markers and markers & {
            "component", "temporal_boundary", "slow"
        }:
            errors.append(f"{test.nodeid}: unit_fast has an exclusive marker")
        if {"integration_ci", "reliability_journey"} <= markers:
            errors.append(
                f"{test.nodeid}: integration_ci and reliability_journey overlap"
            )
        if not is_eligible(test):
            continue
        owners = shard_owners(test)
        if not owners:
            errors.append(f"{test.nodeid}: no CI owner")
        elif len(owners) > 1:
            errors.append(f"{test.nodeid}: multiple CI owners: {', '.join(owners)}")
    return errors


class _CollectionPlugin:
    def __init__(self) -> None:
        self.tests: list[CollectedTest] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            path = Path(str(item.fspath)).resolve().relative_to(Path.cwd().resolve())
            self.tests.append(
                CollectedTest(
                    nodeid=item.nodeid,
                    path=path.as_posix(),
                    markers=frozenset(marker.name for marker in item.iter_markers()),
                )
            )


def main() -> int:
    plugin = _CollectionPlugin()
    exit_code = pytest.main(
        [
            "tests/unit",
            "tests/component",
            "tests/integration",
            "--collect-only",
            "-p",
            "no:terminal",
        ],
        plugins=[plugin],
    )
    if exit_code not in {pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED}:
        return int(exit_code)

    errors = ownership_errors(plugin.tests)
    if errors:
        print("Backend test shard ownership verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Verified exclusive CI ownership for {sum(map(is_eligible, plugin.tests))} tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
