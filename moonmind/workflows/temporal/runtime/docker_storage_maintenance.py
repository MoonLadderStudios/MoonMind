"""Bounded Docker image/cache reclamation under data-volume pressure."""

from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_TRUEY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class DiskUsageSnapshot:
    total: int
    used: int
    free: int


@dataclass(frozen=True, slots=True)
class DockerStorageMaintenanceConfig:
    enabled: bool = True
    data_path: Path = Path("/work/agent_jobs")
    high_watermark_percent: int = 80
    critical_watermark_percent: int = 90
    image_min_age_hours: int = 168
    build_cache_min_age_hours: int = 24

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> DockerStorageMaintenanceConfig:
        source = os.environ if env is None else env
        config = cls(
            enabled=_env_bool(source, "MOONMIND_DOCKER_STORAGE_JANITOR_ENABLED", True),
            data_path=Path(
                source.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
                or "/work/agent_jobs"
            ),
            high_watermark_percent=_required_int(
                source,
                "MOONMIND_DOCKER_STORAGE_HIGH_WATERMARK_PERCENT",
                80,
            ),
            critical_watermark_percent=_required_int(
                source,
                "MOONMIND_DOCKER_STORAGE_CRITICAL_WATERMARK_PERCENT",
                90,
            ),
            image_min_age_hours=_required_int(
                source,
                "MOONMIND_DOCKER_STORAGE_IMAGE_MIN_AGE_HOURS",
                168,
            ),
            build_cache_min_age_hours=_required_int(
                source,
                "MOONMIND_DOCKER_STORAGE_BUILD_CACHE_MIN_AGE_HOURS",
                24,
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not 1 <= self.high_watermark_percent <= 98:
            raise ValueError("Docker storage high watermark must be between 1 and 98")
        if not self.high_watermark_percent < self.critical_watermark_percent <= 99:
            raise ValueError(
                "Docker storage high watermark must be lower than the critical "
                "watermark, which must not exceed 99"
            )
        if self.image_min_age_hours < 1:
            raise ValueError("Docker storage image minimum age must be positive")
        if self.build_cache_min_age_hours < 1:
            raise ValueError("Docker storage build-cache minimum age must be positive")


@dataclass(frozen=True, slots=True)
class DockerStorageMaintenanceResult:
    enabled: bool
    pressure_detected: bool
    critical_pressure_detected: bool
    usage_percent_before: int
    usage_percent_after: int
    total_bytes: int
    free_bytes_before: int
    free_bytes_after: int
    reclaimed_bytes: int
    commands_attempted: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "pressureDetected": self.pressure_detected,
            "criticalPressureDetected": self.critical_pressure_detected,
            "usagePercentBefore": self.usage_percent_before,
            "usagePercentAfter": self.usage_percent_after,
            "totalBytes": self.total_bytes,
            "freeBytesBefore": self.free_bytes_before,
            "freeBytesAfter": self.free_bytes_after,
            "reclaimedBytes": self.reclaimed_bytes,
            "commandsAttempted": list(self.commands_attempted),
            "errors": list(self.errors),
        }


DockerCommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, str, str]]]
DiskUsageProvider = Callable[[Path], DiskUsageSnapshot]


async def reclaim_docker_storage_under_pressure(
    *,
    config: DockerStorageMaintenanceConfig,
    command_runner: DockerCommandRunner,
    disk_usage: DiskUsageProvider | None = None,
    docker_binary: str = "docker",
) -> DockerStorageMaintenanceResult:
    """Prune only unused image/cache state after crossing configured watermarks."""

    config.validate()
    usage_provider = disk_usage or _disk_usage
    before = usage_provider(config.data_path)
    if not config.enabled or not _at_or_above(
        before,
        config.high_watermark_percent,
    ):
        return _result(
            config=config,
            before=before,
            after=before,
            pressure_detected=False,
            critical_pressure_detected=False,
        )

    commands_attempted: list[str] = []
    errors: list[str] = []

    async def run(label: str, command: tuple[str, ...]) -> None:
        commands_attempted.append(label)
        code, _stdout, _stderr = await command_runner(command)
        if code:
            errors.append(f"{label} exited with code {code}")

    await run(
        "age-bounded image prune",
        (
            docker_binary,
            "image",
            "prune",
            "-af",
            "--filter",
            f"until={config.image_min_age_hours}h",
        ),
    )
    await run(
        "age-bounded builder prune",
        (
            docker_binary,
            "builder",
            "prune",
            "-af",
            "--filter",
            f"until={config.build_cache_min_age_hours}h",
        ),
    )

    after_aged = usage_provider(config.data_path)
    critical_pressure_detected = _at_or_above(
        after_aged,
        config.critical_watermark_percent,
    )
    after = after_aged
    if critical_pressure_detected:
        await run(
            "critical image prune",
            (docker_binary, "image", "prune", "-af"),
        )
        await run(
            "critical builder prune",
            (docker_binary, "builder", "prune", "-af"),
        )
        after = usage_provider(config.data_path)

    return _result(
        config=config,
        before=before,
        after=after,
        pressure_detected=True,
        critical_pressure_detected=critical_pressure_detected,
        commands_attempted=tuple(commands_attempted),
        errors=tuple(errors),
    )


def _result(
    *,
    config: DockerStorageMaintenanceConfig,
    before: DiskUsageSnapshot,
    after: DiskUsageSnapshot,
    pressure_detected: bool,
    critical_pressure_detected: bool,
    commands_attempted: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> DockerStorageMaintenanceResult:
    return DockerStorageMaintenanceResult(
        enabled=config.enabled,
        pressure_detected=pressure_detected,
        critical_pressure_detected=critical_pressure_detected,
        usage_percent_before=_usage_percent(before),
        usage_percent_after=_usage_percent(after),
        total_bytes=before.total,
        free_bytes_before=before.free,
        free_bytes_after=after.free,
        reclaimed_bytes=max(0, after.free - before.free),
        commands_attempted=commands_attempted,
        errors=errors,
    )


def _disk_usage(path: Path) -> DiskUsageSnapshot:
    usage = shutil.disk_usage(path)
    return DiskUsageSnapshot(total=usage.total, used=usage.used, free=usage.free)


def _usage_percent(usage: DiskUsageSnapshot) -> int:
    if usage.total <= 0:
        raise ValueError("Docker storage filesystem reported a non-positive size")
    return min(100, max(0, round((usage.used * 100) / usage.total)))


def _at_or_above(usage: DiskUsageSnapshot, watermark_percent: int) -> bool:
    if usage.total <= 0:
        raise ValueError("Docker storage filesystem reported a non-positive size")
    return usage.used * 100 >= usage.total * watermark_percent


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if not normalized:
        return default
    if normalized in _TRUEY:
        return True
    if normalized in _FALSEY:
        return False
    raise ValueError(f"{key} must be a boolean")


def _required_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


__all__ = [
    "DiskUsageSnapshot",
    "DockerStorageMaintenanceConfig",
    "DockerStorageMaintenanceResult",
    "reclaim_docker_storage_under_pressure",
]
