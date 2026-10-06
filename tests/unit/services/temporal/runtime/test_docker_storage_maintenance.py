from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from moonmind.workflows.temporal.runtime.docker_storage_maintenance import (
    DiskUsageSnapshot,
    DockerStorageMaintenanceConfig,
    reclaim_docker_storage_under_pressure,
)

# Routine expiry removes only untagged images (superseded digest-pinned
# releases) and anonymous volumes no container references.
ROUTINE_IMAGE_PRUNE = ("docker", "image", "prune", "-f", "--filter", "until=24h")
ROUTINE_BUILDER_PRUNE = ("docker", "builder", "prune", "-af", "--filter", "until=24h")
ROUTINE_VOLUME_PRUNE = (
    "docker",
    "volume",
    "prune",
    "-f",
    "--filter",
    "label=com.docker.volume.anonymous",
)
BUILDX_DISCOVERY = (
    "docker",
    "ps",
    "--no-trunc",
    "--filter",
    "name=buildx_buildkit_",
    "--format",
    "{{.ID}}",
)


def _usage(percent: int) -> DiskUsageSnapshot:
    total = 1_000
    used = total * percent // 100
    return DiskUsageSnapshot(total=total, used=used, free=total - used)


@pytest.mark.asyncio
async def test_storage_maintenance_prunes_aged_cache_below_high_watermark() -> None:
    commands: list[tuple[str, ...]] = []

    async def _run(
        command: Sequence[str],
    ) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return 0, "", ""

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(data_path=Path("/work/agent_jobs")),
        command_runner=_run,
        disk_usage=lambda _path: _usage(79),
    )

    assert result.pressure_detected is False
    assert result.usage_percent_before == 79
    assert result.commands_attempted == (
        "age-bounded image prune",
        "age-bounded builder prune",
        "unreferenced anonymous volume prune",
        "Buildx builder discovery",
    )
    assert commands == [
        ROUTINE_IMAGE_PRUNE,
        ROUTINE_BUILDER_PRUNE,
        ROUTINE_VOLUME_PRUNE,
        BUILDX_DISCOVERY,
    ]
    assert result.critical_pressure_detected is False


@pytest.mark.asyncio
async def test_routine_expiry_keeps_tagged_images_below_critical_pressure() -> None:
    """A declared container-job image is reused across jobs, not re-pulled.

    Image age is its build time, so an all-images age filter removed the
    multi-gigabyte Unreal base image whenever its last job had just exited.
    Only critical pressure may remove tagged images that no container uses.
    """
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(89), _usage(89)))

    async def _run(command: Sequence[str]) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return 0, "", ""

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(),
        command_runner=_run,
        disk_usage=lambda _path: next(usages),
    )

    image_prunes = [command for command in commands if command[1:3] == ("image", "prune")]
    assert image_prunes == [ROUTINE_IMAGE_PRUNE]
    assert result.critical_pressure_detected is False


@pytest.mark.asyncio
async def test_disabled_storage_maintenance_does_not_prune() -> None:
    async def _run(command: Sequence[str]) -> tuple[int, str, str]:
        raise AssertionError("Disabled maintenance must not mutate Docker")

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(enabled=False),
        command_runner=_run,
        disk_usage=lambda _path: _usage(96),
    )

    assert result.enabled is False
    assert result.commands_attempted == ()


@pytest.mark.asyncio
async def test_storage_maintenance_prunes_aged_data_at_high_watermark() -> None:
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(85), _usage(72)))

    async def _run(
        command: Sequence[str],
    ) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return 0, "", ""

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(data_path=Path("/work/agent_jobs")),
        command_runner=_run,
        disk_usage=lambda _path: next(usages),
    )

    assert commands == [
        ROUTINE_IMAGE_PRUNE,
        ROUTINE_BUILDER_PRUNE,
        ROUTINE_VOLUME_PRUNE,
        BUILDX_DISCOVERY,
    ]
    assert result.pressure_detected is True
    assert result.critical_pressure_detected is False
    assert result.usage_percent_after == 72
    assert result.reclaimed_bytes == 130
    assert result.errors == ()


@pytest.mark.asyncio
async def test_storage_maintenance_escalates_while_critical_pressure_remains() -> None:
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(96), _usage(93), _usage(68)))

    async def _run(
        command: Sequence[str],
    ) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return 0, "", ""

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(data_path=Path("/work/agent_jobs")),
        command_runner=_run,
        disk_usage=lambda _path: next(usages),
    )

    assert commands == [
        ROUTINE_IMAGE_PRUNE,
        ROUTINE_BUILDER_PRUNE,
        ROUTINE_VOLUME_PRUNE,
        BUILDX_DISCOVERY,
        ("docker", "image", "prune", "-af"),
        ("docker", "builder", "prune", "-af"),
    ]
    assert result.critical_pressure_detected is True
    assert result.usage_percent_after == 68
    assert result.reclaimed_bytes == 280


@pytest.mark.asyncio
async def test_storage_maintenance_continues_after_one_prune_failure() -> None:
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(85), _usage(70)))

    async def _run(
        command: Sequence[str],
    ) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return (1, "", "denied") if command[1:3] == ("image", "prune") else (0, "", "")

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(data_path=Path("/work/agent_jobs")),
        command_runner=_run,
        disk_usage=lambda _path: next(usages),
    )

    assert len(commands) == 4
    assert result.errors == ("age-bounded image prune exited with code 1",)


def test_storage_maintenance_config_defaults_and_validation() -> None:
    config = DockerStorageMaintenanceConfig.from_env({})

    assert config.enabled is True
    assert config.high_watermark_percent == 80
    assert config.critical_watermark_percent == 90
    assert config.image_min_age_hours == 24
    assert config.build_cache_min_age_hours == 24
    assert (
        DockerStorageMaintenanceConfig.from_env(
            {"MOONMIND_DOCKER_STORAGE_JANITOR_ENABLED": "false"}
        ).enabled
        is False
    )

    with pytest.raises(ValueError, match="high watermark"):
        DockerStorageMaintenanceConfig.from_env(
            {
                "MOONMIND_DOCKER_STORAGE_HIGH_WATERMARK_PERCENT": "95",
                "MOONMIND_DOCKER_STORAGE_CRITICAL_WATERMARK_PERCENT": "90",
            }
        )

    with pytest.raises(ValueError, match="must be a boolean"):
        DockerStorageMaintenanceConfig.from_env(
            {"MOONMIND_DOCKER_STORAGE_JANITOR_ENABLED": "sometimes"}
        )


def _buildx_container(name: str = "buildx_buildkit_default") -> dict[str, object]:
    return {
        "name": f"/{name}",
        "running": True,
        "entrypoint": ["/usr/bin/buildkitd"],
        "mounts": [
            {
                "Type": "volume",
                "Name": f"{name}_state",
                "Destination": "/var/lib/buildkit",
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entrypoint", ["/usr/bin/buildkitd", "/usr/bin/buildkitd-entrypoint"]
)
async def test_prunes_container_builder_cache_without_host_buildx_configuration(
    entrypoint: str,
) -> None:
    builder_id = "a" * 64
    commands: list[tuple[str, ...]] = []

    async def run(command: Sequence[str]) -> tuple[int, str, str]:
        commands.append(tuple(command))
        if command[1] == "ps":
            return 0, builder_id + "\n", ""
        if command[1] == "inspect":
            builder = _buildx_container()
            builder["entrypoint"] = [entrypoint]
            return 0, json.dumps(builder), ""
        return 0, "", ""

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(build_cache_min_age_hours=24),
        command_runner=run,
        disk_usage=lambda _: _usage(40),
    )

    assert (
        "docker",
        "exec",
        builder_id,
        "buildctl",
        "prune",
        "--all",
        "--keep-duration",
        "24h",
    ) in commands
    # The builder's named state volume survives; only anonymous volumes no
    # container references are eligible for routine expiry.
    assert not any("buildx_buildkit_default_state" in command for command in commands)
    assert not any("rm" in command for command in commands)
    assert [command for command in commands if command[1] == "volume"] == [
        ROUTINE_VOLUME_PRUNE
    ]
    assert result.errors == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["name", "stopped", "entrypoint", "mount"])
async def test_does_not_execute_cleanup_in_an_unverified_builder(invalid: str) -> None:
    builder = _buildx_container()
    if invalid == "name":
        builder["name"] = "/active-workflow"
    elif invalid == "stopped":
        builder["running"] = False
    elif invalid == "entrypoint":
        builder["entrypoint"] = ["python"]
    else:
        builder["mounts"] = []
    commands: list[tuple[str, ...]] = []

    async def run(command: Sequence[str]) -> tuple[int, str, str]:
        commands.append(tuple(command))
        if command[1] == "ps":
            return 0, "b" * 64 + "\n", ""
        if command[1] == "inspect":
            return 0, json.dumps(builder), ""
        return 0, "", ""

    await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(),
        command_runner=run,
        disk_usage=lambda _: _usage(40),
    )

    assert any(command[1] == "inspect" for command in commands)
    assert not any(command[1] == "exec" for command in commands)


@pytest.mark.asyncio
async def test_builder_discovery_failure_keeps_other_cleanup_and_reports_error() -> (
    None
):
    commands: list[tuple[str, ...]] = []

    async def run(command: Sequence[str]) -> tuple[int, str, str]:
        commands.append(tuple(command))
        return (1, "", "denied") if command[1] == "ps" else (0, "", "")

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(),
        command_runner=run,
        disk_usage=lambda _: _usage(40),
    )

    assert ROUTINE_BUILDER_PRUNE in commands
    assert result.errors == ("Buildx builder discovery exited with code 1",)
