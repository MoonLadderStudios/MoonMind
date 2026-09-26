"""Bounded legacy tools-volume retirement (MoonLadderStudios/MoonMind#4558).

Only exact, positively identified legacy tool volumes are ever removed, after
checking running and stopped container references, current deployment
configuration, and rollback retention. Unsafe candidates are retained with a
concise reason; cleanup errors never block unrelated operation.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.host_services.legacy_tools_cleanup import (
    apply_legacy_cleanup,
    classify_legacy_tools_volume,
    plan_legacy_cleanup,
)


def test_unused_owned_bundle_is_removed() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.74.2-1",
        labels={"omnigent-tools": "true"},
        running_refs=[],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=[],
    )
    assert (decision, reason) == ("remove", "unused-owned-legacy-bundle")


@pytest.mark.parametrize(
    "name",
    [
        "postgres-data",
        "temporal-data",
        "minio-data",
        "agent_workspaces",
        "moonmind_secrets",
        "some-cache",
        "moonmind-omnigent-tools",  # unversioned: not positively identified
        "other-project-omnigent-tools-gh-2.76.2",
    ],
)
def test_unrelated_volumes_are_never_touched(name: str) -> None:
    decision, reason = classify_legacy_tools_volume(
        name=name,
        labels={},
        running_refs=[],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=[],
    )
    assert (decision, reason) == ("retain", "not-a-legacy-tools-volume")


def test_running_reference_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.74.2-1",
        labels={},
        running_refs=["moonmind-omnigent-tools-gh-2.74.2-1"],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=[],
    )
    assert (decision, reason) == ("retain", "referenced-by-running-container")


def test_stopped_reference_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.74.2-1",
        labels={},
        running_refs=[],
        stopped_refs=["moonmind-omnigent-tools-gh-2.74.2-1"],
        config_refs=[],
        rollback_retained=[],
    )
    assert (decision, reason) == ("retain", "referenced-by-stopped-container")


def test_current_config_reference_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.76.2",
        labels={},
        running_refs=[],
        stopped_refs=[],
        config_refs=["moonmind-omnigent-tools-gh-2.76.2"],
        rollback_retained=[],
    )
    assert (decision, reason) == ("retain", "referenced-by-current-config")


def test_rollback_retention_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.76.2",
        labels={},
        running_refs=[],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=["moonmind-omnigent-tools-gh-2.76.2"],
    )
    assert (decision, reason) == ("retain", "retained-for-rollback")


def test_foreign_project_volume_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.76.2",
        labels={"com.docker.compose.project": "other-project"},
        running_refs=[],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=[],
        deployment_project="moonmind",
    )
    assert (decision, reason) == ("retain", "foreign-project-volume")


def test_ambiguous_ownership_is_retained() -> None:
    decision, reason = classify_legacy_tools_volume(
        name="moonmind-omnigent-tools-gh-2.76.2",
        labels=None,
        running_refs=[],
        stopped_refs=[],
        config_refs=[],
        rollback_retained=[],
    )
    assert (decision, reason) == ("retain", "ambiguous-ownership")


def test_plan_summarizes_a_mixed_daemon() -> None:
    plan = plan_legacy_cleanup(
        [
            {
                "name": "moonmind-omnigent-tools-gh-2.74.2-1",
                "labels": {},
                "running_refs": [],
                "stopped_refs": [],
                "config_refs": [],
                "rollback_retained": [],
            },
            {
                "name": "postgres-data",
                "labels": {},
                "running_refs": [],
                "stopped_refs": [],
                "config_refs": [],
                "rollback_retained": [],
            },
            {
                "name": "moonmind-omnigent-tools-gh-2.76.2",
                "labels": {},
                "running_refs": ["moonmind-omnigent-tools-gh-2.76.2"],
                "stopped_refs": [],
                "config_refs": [],
                "rollback_retained": [],
            },
        ]
    )
    assert plan["remove"] == ["moonmind-omnigent-tools-gh-2.74.2-1"]
    assert plan["retained"]["postgres-data"] == "not-a-legacy-tools-volume"
    assert plan["retained"]["moonmind-omnigent-tools-gh-2.76.2"] == (
        "referenced-by-running-container"
    )


@pytest.mark.asyncio
async def test_apply_removes_exact_name_without_force_and_is_idempotent() -> None:
    commands: list[list[str]] = []

    async def runner(argv: list[str]) -> tuple[int, str, str]:
        commands.append(list(argv))
        if commands.count(list(argv)) > 1:
            return 1, "", "Error: No such volume: moonmind-omnigent-tools-gh-2.74.2-1"
        return 0, "moonmind-omnigent-tools-gh-2.74.2-1", ""

    first = await apply_legacy_cleanup(
        ["moonmind-omnigent-tools-gh-2.74.2-1"], runner=runner
    )
    second = await apply_legacy_cleanup(
        ["moonmind-omnigent-tools-gh-2.74.2-1"], runner=runner
    )
    assert first == {
        "moonmind-omnigent-tools-gh-2.74.2-1": ("removed", "removed"),
    }
    assert second == {
        "moonmind-omnigent-tools-gh-2.74.2-1": ("retained", "already-removed"),
    }
    assert commands == [
        ["docker", "volume", "rm", "moonmind-omnigent-tools-gh-2.74.2-1"],
        ["docker", "volume", "rm", "moonmind-omnigent-tools-gh-2.74.2-1"],
    ]


@pytest.mark.asyncio
async def test_apply_treats_mid_deletion_reference_as_retained() -> None:
    async def runner(argv: list[str]) -> tuple[int, str, str]:
        return 1, "", "Error response from daemon: remove {}: volume is in use".format(
            argv[-1]
        )

    result = await apply_legacy_cleanup(
        ["moonmind-omnigent-tools-gh-2.74.2-1"], runner=runner
    )
    assert result == {
        "moonmind-omnigent-tools-gh-2.74.2-1": (
            "retained",
            "became-in-use-during-deletion",
        ),
    }


@pytest.mark.asyncio
async def test_apply_failure_never_blocks_unrelated_volumes() -> None:
    async def runner(argv: list[str]) -> tuple[int, str, str]:
        if argv[-1].endswith("2.74.2-1"):
            raise RuntimeError("daemon unreachable for one candidate")
        return 0, argv[-1], ""

    result = await apply_legacy_cleanup(
        [
            "moonmind-omnigent-tools-gh-2.74.2-1",
            "moonmind-omnigent-tools-gh-2.75.0",
        ],
        runner=runner,
    )
    assert result["moonmind-omnigent-tools-gh-2.74.2-1"][0] == "retained"
    assert result["moonmind-omnigent-tools-gh-2.75.0"] == ("removed", "removed")
