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
# Volumes the pre-reuse backend keyed by run principal. The current backend
# mounts the declared deployment volume, so these are never reattached.
LEGACY_CACHE_VOLUME_PRUNE = (
    "docker",
    "volume",
    "prune",
    "--all",
    "-f",
    "--filter",
    "label=moonmind.kind=container-job-cache",
    "--filter",
    "label=moonmind.cache_owner",
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
HOLD_DISCOVERY = (
    "docker",
    "container",
    "ls",
    "--all",
    "--no-trunc",
    "--filter",
    "label=moonmind.kind=image-retention-hold",
    "--format",
    '{{.ID}} {{.Label "moonmind.retained_image_id"}}',
)
ROUTINE_COMMANDS = [
    HOLD_DISCOVERY,
    ROUTINE_IMAGE_PRUNE,
    ROUTINE_BUILDER_PRUNE,
    ROUTINE_VOLUME_PRUNE,
    LEGACY_CACHE_VOLUME_PRUNE,
    BUILDX_DISCOVERY,
]


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
        "image retention hold discovery",
        "age-bounded image prune",
        "age-bounded builder prune",
        "unreferenced anonymous volume prune",
        "legacy owner-scoped cache volume prune",
        "Buildx builder discovery",
    )
    assert commands == ROUTINE_COMMANDS
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

    image_prunes = [
        command for command in commands if command[1:3] == ("image", "prune")
    ]
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

    assert commands == ROUTINE_COMMANDS
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

    # MoonMind-owned waste (leaked cache volumes) is reclaimed before the
    # pressure reading that decides whether unused tagged images go too.
    assert commands == [
        *ROUTINE_COMMANDS,
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

    assert len(commands) == len(ROUTINE_COMMANDS)
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
        ROUTINE_VOLUME_PRUNE,
        LEGACY_CACHE_VOLUME_PRUNE,
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


HOST_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "1" * 64
JOB_IMAGE = "ghcr.io/moonladderstudios/tactics-ue-base:5.8"
ABSENT_IMAGE = "ghcr.io/example/never-pulled:1"
HOST_IMAGE_ID = "sha256:" + "a" * 64
JOB_IMAGE_ID = "sha256:" + "b" * 64
SUPERSEDED_IMAGE_ID = "sha256:" + "c" * 64


def _retention_runner(
    commands: list[tuple[str, ...]],
    *,
    existing_holds: str = "",
    create_code: int = 0,
):
    image_ids = {HOST_IMAGE: HOST_IMAGE_ID, JOB_IMAGE: JOB_IMAGE_ID}

    async def run(command: Sequence[str]) -> tuple[int, str, str]:
        command = tuple(command)
        commands.append(command)
        if command[1:3] == ("image", "inspect"):
            ref = command[-1]
            if ref in image_ids:
                return 0, image_ids[ref] + "\n", ""
            return 1, "", f"Error response from daemon: No such image: {ref}"
        if command == HOLD_DISCOVERY:
            return 0, existing_holds, ""
        if command[1:3] == ("container", "create"):
            return create_code, "", "" if not create_code else "denied"
        return 0, "", ""

    return run


@pytest.mark.asyncio
async def test_launch_images_are_held_before_any_image_prune() -> None:
    """An unused Host Class or declared job image survives critical pruning.

    Docker has no image pin, but it never prunes an image a container
    references. A stopped, never-started hold container per retained image
    keeps the digest-pinned host image (and the multi-gigabyte job base image)
    out of both prunes, so the next launch does not re-pull it under pressure.
    """
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(96), _usage(95), _usage(91)))

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(),
        command_runner=_retention_runner(
            commands,
            existing_holds=(
                f"{'d' * 64} {HOST_IMAGE_ID}\n{'e' * 64} {SUPERSEDED_IMAGE_ID}\n"
            ),
        ),
        disk_usage=lambda _path: next(usages),
        retained_images=(HOST_IMAGE, JOB_IMAGE, ABSENT_IMAGE, HOST_IMAGE),
    )

    first_prune = commands.index(ROUTINE_IMAGE_PRUNE)
    holds = commands[:first_prune]
    # The superseded image's hold is released so routine expiry can take it.
    assert ("docker", "container", "rm", "e" * 64) in holds
    creates = [command for command in holds if command[1:3] == ("container", "create")]
    assert len(creates) == 1
    create = creates[0]
    assert create[-2:] == (JOB_IMAGE_ID, "true")
    assert "--pull" in create and create[create.index("--pull") + 1] == "never"
    assert "moonmind.kind=image-retention-hold" in create
    assert f"moonmind.retained_image_id={JOB_IMAGE_ID}" in create
    assert ("docker", "image", "prune", "-af") in commands[first_prune:]
    assert result.retained_images == (HOST_IMAGE, JOB_IMAGE)
    assert result.to_dict()["retainedImages"] == [HOST_IMAGE, JOB_IMAGE]
    assert result.errors == ()


@pytest.mark.asyncio
async def test_image_prunes_wait_until_launch_images_are_held() -> None:
    """Fail closed: an unheld launch image is never exposed to a prune."""
    commands: list[tuple[str, ...]] = []
    usages = iter((_usage(96), _usage(95), _usage(94)))

    result = await reclaim_docker_storage_under_pressure(
        config=DockerStorageMaintenanceConfig(),
        command_runner=_retention_runner(commands, create_code=1),
        disk_usage=lambda _path: next(usages),
        retained_images=(HOST_IMAGE,),
    )

    assert not [command for command in commands if command[1:3] == ("image", "prune")]
    # Every other owner still reclaims, including the critical builder pass.
    assert ROUTINE_BUILDER_PRUNE in commands
    assert LEGACY_CACHE_VOLUME_PRUNE in commands
    assert ("docker", "builder", "prune", "-af") in commands
    assert result.critical_pressure_detected is True
    assert result.retained_images == ()
    assert "image retention hold" in " ".join(result.errors)
    assert any("image prunes skipped" in error for error in result.errors)
