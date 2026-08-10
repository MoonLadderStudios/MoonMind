from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from moonmind.workflows.temporal.runtime.docker_storage_maintenance import (
    DiskUsageSnapshot,
    DockerStorageMaintenanceConfig,
    reclaim_docker_storage_under_pressure,
)


def _usage(percent: int) -> DiskUsageSnapshot:
    total = 1_000
    used = total * percent // 100
    return DiskUsageSnapshot(total=total, used=used, free=total - used)


@pytest.mark.asyncio
async def test_storage_maintenance_is_read_only_below_high_watermark() -> None:
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
    assert result.commands_attempted == ()
    assert commands == []


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
        ("docker", "image", "prune", "-af", "--filter", "until=168h"),
        ("docker", "builder", "prune", "-af", "--filter", "until=24h"),
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
        ("docker", "image", "prune", "-af", "--filter", "until=168h"),
        ("docker", "builder", "prune", "-af", "--filter", "until=24h"),
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

    assert len(commands) == 2
    assert result.errors == ("age-bounded image prune exited with code 1",)


def test_storage_maintenance_config_defaults_and_validation() -> None:
    config = DockerStorageMaintenanceConfig.from_env({})

    assert config.enabled is True
    assert config.high_watermark_percent == 80
    assert config.critical_watermark_percent == 90
    assert config.image_min_age_hours == 168
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
