"""Periodic Docker cache expiry with bounded escalation under disk pressure."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_TRUEY = frozenset({"1", "true", "yes", "on"})
_FALSEY = frozenset({"0", "false", "no", "off"})
_BUILDX_INSPECT_FORMAT = (
    '{"name":{{json .Name}},"running":{{json .State.Running}},'
    '"entrypoint":{{json .Config.Entrypoint}},"mounts":{{json .Mounts}}}'
)


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
    image_min_age_hours: int = 24
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
                24,
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
    """Expire unused cache every pass, then escalate under critical pressure.

    Docker Desktop's sparse VM disk can report free space after the host disk
    fills. Routine expiry must therefore work independently of that reading.
    Docker protects images referenced by containers and active build cache.

    Routine expiry removes untagged images only. Image age is build time, not
    last use, so an all-images age filter deletes a reused tagged image (such
    as a declared container-job base image) whenever no container holds it.
    Superseded digest-pinned releases are untagged and still expire. Tagged
    unused images are removed only under critical pressure.
    """

    config.validate()
    usage_provider = disk_usage or _disk_usage
    before = usage_provider(config.data_path)
    if not config.enabled:
        return _result(
            config=config,
            before=before,
            after=before,
            pressure_detected=False,
            critical_pressure_detected=False,
        )

    commands_attempted: list[str] = []
    errors: list[str] = []

    async def run(label: str, command: tuple[str, ...]) -> tuple[int, str]:
        commands_attempted.append(label)
        code, stdout, _stderr = await command_runner(command)
        if code:
            errors.append(f"{label} exited with code {code}")
        return code, stdout

    await run(
        "age-bounded image prune",
        (
            docker_binary,
            "image",
            "prune",
            "-f",
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
    # An anonymous volume no container references can never be reattached:
    # Compose only carries anonymous volumes across a recreate while the old
    # container still exists. Named volumes (deployment data, caches, builder
    # state) are excluded by the daemon default and by the label filter.
    await run(
        "unreferenced anonymous volume prune",
        (
            docker_binary,
            "volume",
            "prune",
            "-f",
            "--filter",
            "label=com.docker.volume.anonymous",
        ),
    )

    # Buildx's docker-container driver owns a separate cache that the daemon's
    # builder prune cannot reach. Discover it from Docker, since the runtime
    # worker does not share the operator's ~/.docker/buildx configuration.
    code, builder_ids = await run(
        "Buildx builder discovery",
        (
            docker_binary,
            "ps",
            "--no-trunc",
            "--filter",
            "name=buildx_buildkit_",
            "--format",
            "{{.ID}}",
        ),
    )
    verified_builders: list[str] = []
    if not code:
        for builder_id in dict.fromkeys(builder_ids.split()):
            if not re.fullmatch(r"[0-9a-f]{64}", builder_id):
                errors.append("Buildx discovery returned an invalid container ID")
                continue
            code, details = await run(
                f"Buildx builder inspection {builder_id}",
                (
                    docker_binary,
                    "inspect",
                    "--format",
                    _BUILDX_INSPECT_FORMAT,
                    builder_id,
                ),
            )
            if code:
                continue
            try:
                builder = json.loads(details)
            except (TypeError, ValueError):
                errors.append(f"Buildx builder {builder_id} inspection was unreadable")
                continue
            if not _is_buildx_builder(builder):
                errors.append(
                    f"Buildx builder {builder_id} ownership could not be verified"
                )
                continue
            verified_builders.append(builder_id)
            await run(
                f"age-bounded Buildx cache prune {builder_id}",
                (
                    docker_binary,
                    "exec",
                    builder_id,
                    "buildctl",
                    "prune",
                    "--all",
                    "--keep-duration",
                    f"{config.build_cache_min_age_hours}h",
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
        for builder_id in verified_builders:
            await run(
                f"critical Buildx cache prune {builder_id}",
                (docker_binary, "exec", builder_id, "buildctl", "prune", "--all"),
            )
        after = usage_provider(config.data_path)

    return _result(
        config=config,
        before=before,
        after=after,
        pressure_detected=_at_or_above(before, config.high_watermark_percent),
        critical_pressure_detected=critical_pressure_detected,
        commands_attempted=tuple(commands_attempted),
        errors=tuple(errors),
    )


def _is_buildx_builder(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("running") is not True:
        return False
    name = str(value.get("name") or "").removeprefix("/")
    if not re.fullmatch(r"buildx_buildkit_[a-zA-Z0-9_.-]+", name):
        return False
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, list) or not entrypoint:
        return False
    if Path(str(entrypoint[0])).name not in {
        "buildkitd",
        "buildkitd-entrypoint",
        "buildkitd-entrypoint.sh",
    }:
        return False
    mounts = value.get("mounts")
    return isinstance(mounts, list) and any(
        isinstance(mount, Mapping)
        and mount.get("Type") == "volume"
        and mount.get("Name") == f"{name}_state"
        and mount.get("Destination") == "/var/lib/buildkit"
        for mount in mounts
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
