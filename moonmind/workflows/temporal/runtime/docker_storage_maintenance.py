"""Periodic Docker cache expiry with bounded escalation under disk pressure."""

from __future__ import annotations

import hashlib
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
#: Shared with the workspace janitor: one definition of critical pressure.
CRITICAL_WATERMARK_ENV = "MOONMIND_DOCKER_STORAGE_CRITICAL_WATERMARK_PERCENT"
DEFAULT_CRITICAL_WATERMARK_PERCENT = 90
_IMAGE_HOLD_KIND = "moonmind.kind=image-retention-hold"
_IMAGE_HOLD_ID_LABEL = "moonmind.retained_image_id"
# Not Compose's own project label: `up --remove-orphans` would delete the holds.
_IMAGE_HOLD_DEPLOYMENT_LABEL = "moonmind.deployment"
_IMAGE_HOLD_FORMAT = '{{.ID}} {{.Label "moonmind.retained_image_id"}}'
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")


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
    critical_watermark_percent: int = DEFAULT_CRITICAL_WATERMARK_PERCENT
    image_min_age_hours: int = 24
    build_cache_min_age_hours: int = 24
    # Compose project that owns this pass's image holds. Deployments sharing a
    # daemon keep their own holds, so neither releases the other's.
    deployment: str = "moonmind"

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
                CRITICAL_WATERMARK_ENV,
                DEFAULT_CRITICAL_WATERMARK_PERCENT,
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
            deployment=(
                str(source.get("MOONMIND_DEPLOYMENT_PROJECT_NAME") or "").strip()
                or "moonmind"
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
    retained_images: tuple[str, ...] = ()

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
            "retainedImages": list(self.retained_images),
        }


DockerCommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, str, str]]]
DiskUsageProvider = Callable[[Path], DiskUsageSnapshot]


async def reclaim_docker_storage_under_pressure(
    *,
    config: DockerStorageMaintenanceConfig,
    command_runner: DockerCommandRunner,
    disk_usage: DiskUsageProvider | None = None,
    docker_binary: str = "docker",
    retained_images: Sequence[str] = (),
) -> DockerStorageMaintenanceResult:
    """Expire unused cache every pass, then escalate under critical pressure.

    Docker Desktop's sparse VM disk can report free space after the host disk
    fills. Routine expiry must therefore work independently of that reading.
    Docker protects images referenced by containers and active build cache.

    Routine expiry removes untagged images only. Image age is build time, not
    last use, so an all-images age filter deletes a reused tagged image (such
    as a declared container-job base image) whenever no container holds it.
    Superseded digest-pinned releases are untagged and still expire. Tagged
    unused images are removed only under critical pressure, after MoonMind's
    own reclaimable state is gone, and never the ``retained_images`` the
    current configuration launches: a never-started hold container references
    each one, and image prunes wait until every present one is held.
    """

    config.validate()
    usage_provider = disk_usage or filesystem_usage
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

    async def run(
        label: str, command: tuple[str, ...], *, absent_ok: bool = False
    ) -> tuple[int, str, bool]:
        """Run one command; the flag reports a tolerated "no such image"."""
        commands_attempted.append(label)
        code, stdout, stderr = await command_runner(command)
        absent = bool(code) and absent_ok and "no such image" in stderr.lower()
        if code and not absent:
            errors.append(f"{label} exited with code {code}")
        return code, stdout, absent

    held_images, images_held = await _hold_retained_images(
        run, docker_binary, retained_images, deployment=config.deployment
    )
    if images_held:
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
    else:
        errors.append("image prunes skipped until every retained image is held")
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
    # state) are excluded by this label filter. Since API 1.42 the daemon's
    # own anonymous-only default applies this same label, so dropping it
    # reclaims nothing more; older daemons prune every unused volume, named
    # ones included, unless the filter is explicit.
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
    # Container-job caches were once keyed by job principal, which is
    # execution-scoped, so each run created a multi-gigabyte cache no later
    # run mounts. Only those volumes carry the owner label; the declared
    # deployment cache volumes that jobs now reuse never do. Docker refuses
    # to prune a volume any container still references.
    await run(
        "legacy owner-scoped cache volume prune",
        (
            docker_binary,
            "volume",
            "prune",
            "--all",
            "-f",
            "--filter",
            "label=moonmind.kind=container-job-cache",
            "--filter",
            "label=moonmind.cache_owner",
        ),
    )

    # Buildx's docker-container driver owns a separate cache that the daemon's
    # builder prune cannot reach. Discover it from Docker, since the runtime
    # worker does not share the operator's ~/.docker/buildx configuration.
    code, builder_ids, _ = await run(
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
            code, details, _ = await run(
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
        if images_held:
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
        retained_images=held_images,
    )


async def _hold_retained_images(
    run: Callable[..., Awaitable[tuple[int, str, bool]]],
    docker_binary: str,
    retained_images: Sequence[str],
    *,
    deployment: str,
) -> tuple[tuple[str, ...], bool]:
    """Keep each present retained image referenced by a hold container.

    Docker has no image pin, but no prune removes an image a container
    references. Holds are created, never started, from the exact image ID,
    and released once their image is no longer retained so it can expire.
    Each deployment reconciles only its own holds. Returns the held refs and
    whether every present retained image is held.
    """

    wanted: dict[str, str] = {}
    complete = True
    for ref in dict.fromkeys(ref.strip() for ref in retained_images if ref.strip()):
        code, stdout, absent = await run(
            f"retained image inspection {ref}",
            (docker_binary, "image", "inspect", "--format", "{{.Id}}", ref),
            absent_ok=True,
        )
        image_id = stdout.strip()
        if not code and _IMAGE_ID.fullmatch(image_id):
            wanted.setdefault(image_id, ref)
        elif not absent:
            # Neither an image ID nor a clean absence: protection is unknown.
            complete = False

    code, listing, _ = await run(
        "image retention hold discovery",
        (
            docker_binary,
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={_IMAGE_HOLD_KIND}",
            "--filter",
            f"label={_IMAGE_HOLD_DEPLOYMENT_LABEL}={deployment}",
            "--format",
            _IMAGE_HOLD_FORMAT,
        ),
    )
    held: set[str] = set()
    if code:
        complete = False
    else:
        for line in listing.splitlines():
            container_id, _, image_id = line.strip().partition(" ")
            if not container_id:
                continue
            if image_id in wanted and image_id not in held:
                held.add(image_id)
                continue
            await run(
                f"image retention hold release {container_id}",
                (docker_binary, "container", "rm", container_id),
            )

    owner = hashlib.sha256(deployment.encode()).hexdigest()[:8]
    for image_id, ref in wanted.items():
        if image_id in held:
            continue
        code, _, _ = await run(
            f"image retention hold {ref}",
            (
                docker_binary,
                "container",
                "create",
                "--pull",
                "never",
                "--name",
                f"moonmind-image-hold-{owner}-{image_id.removeprefix('sha256:')[:24]}",
                "--label",
                _IMAGE_HOLD_KIND,
                "--label",
                f"{_IMAGE_HOLD_DEPLOYMENT_LABEL}={deployment}",
                "--label",
                f"{_IMAGE_HOLD_ID_LABEL}={image_id}",
                "--label",
                f"moonmind.retained_image={ref}",
                "--network",
                "none",
                image_id,
                "true",
            ),
        )
        if code:
            complete = False
        else:
            held.add(image_id)
    return tuple(ref for image_id, ref in wanted.items() if image_id in held), complete


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
    retained_images: tuple[str, ...] = (),
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
        retained_images=retained_images,
    )


def filesystem_usage(path: Path) -> DiskUsageSnapshot:
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
    "CRITICAL_WATERMARK_ENV",
    "DEFAULT_CRITICAL_WATERMARK_PERCENT",
    "DiskUsageProvider",
    "DiskUsageSnapshot",
    "DockerStorageMaintenanceConfig",
    "DockerStorageMaintenanceResult",
    "filesystem_usage",
    "reclaim_docker_storage_under_pressure",
]
