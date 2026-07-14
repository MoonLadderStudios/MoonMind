"""Normalized container-job backend settings (MoonLadderStudios/MoonMind#3254).

This is the single place that resolves the deployment-owned container-job
backend configuration: whether the backend is enabled, which backend ``kind``
is selected, the deployment-owned ``defaultBackendRef``, the Docker endpoint
(sourced from deployment configuration only, never from a caller), whether the
raw Docker CLI escape hatch is enabled, and the non-overridable resource
ceilings enforced at the final launch boundary.

Keeping this resolution in one module means public HTTP, MCP, workflow,
managed-session, and Omnigent contracts stay backend-neutral: none of them
carries an endpoint, socket path, or TLS material. Only trusted worker
construction reads these settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Mapping

#: The only backend kind this deployment supports. A second backend kind
#: (Podman, containerd, Kubernetes, endpoint pools) is explicitly out of scope.
SUPPORTED_CONTAINER_BACKEND_KINDS: Final[frozenset[str]] = frozenset({"docker-engine"})

#: Deployment default when the endpoint is not otherwise configured. Matches the
#: ``docker-proxy`` service the ``temporal-worker-agent-runtime`` container reaches.
_DEFAULT_DOCKER_ENDPOINT: Final[str] = "tcp://docker-proxy:2375"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSEY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})


class ContainerBackendConfigError(RuntimeError):
    """Raised when the container-job backend configuration is invalid."""


class ContainerBackendReadinessError(RuntimeError):
    """Raised when a configured backend endpoint is missing or unreachable."""


def _coerce_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSEY:
        return default if text == "" else False
    raise ContainerBackendConfigError(
        f"container backend boolean flag must be truthy/falsey, got {value!r}"
    )


def _coerce_int(value: object, *, default: int, minimum: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ContainerBackendConfigError(
            f"container backend integer ceiling must be an integer, got {value!r}"
        ) from exc
    if parsed < minimum:
        raise ContainerBackendConfigError(
            f"container backend integer ceiling must be >= {minimum}, got {parsed}"
        )
    return parsed


@dataclass(frozen=True)
class ContainerBackendSettings:
    """Resolved, deployment-owned container-job backend configuration.

    The ceilings here are non-overridable: a caller-supplied launch spec that
    requests more than a ceiling is rejected at the final launch boundary rather
    than silently clamped, so billing-relevant resource values never drift.
    """

    enabled: bool
    kind: str
    default_backend_ref: str
    endpoint: str | None
    raw_cli_enabled: bool
    max_cpu_millis: int
    max_memory_mib: int
    max_pids: int
    shm_size_mib: int
    max_timeout_seconds: int
    max_output_bytes: int

    def require_endpoint(self) -> str:
        """Return the configured endpoint or fail readiness with a bounded error."""

        if self.endpoint is None or not self.endpoint.strip():
            raise ContainerBackendReadinessError(
                "container backend endpoint is not configured; set "
                "SYSTEM_DOCKER_HOST (or DOCKER_HOST) on the trusted worker"
            )
        return self.endpoint


def resolve_container_backend_settings(
    env: Mapping[str, str] | None = None,
) -> ContainerBackendSettings:
    """Resolve backend settings from deployment configuration only.

    The endpoint is sourced from ``SYSTEM_DOCKER_HOST`` first (the deployment
    authority handoff preserved by ``docker-compose.yaml``), then ``DOCKER_HOST``,
    then the ``docker-proxy`` default. A caller can never provide or override it.
    An unsupported ``kind`` fails fast.
    """

    source = os.environ if env is None else env

    kind = (source.get("MOONMIND_CONTAINER_BACKEND_KIND") or "docker-engine").strip()
    if kind not in SUPPORTED_CONTAINER_BACKEND_KINDS:
        allowed = ", ".join(sorted(SUPPORTED_CONTAINER_BACKEND_KINDS))
        raise ContainerBackendConfigError(
            f"unsupported container backend kind {kind!r}; supported kinds: {allowed}"
        )

    default_backend_ref = (
        source.get("MOONMIND_CONTAINER_BACKEND_DEFAULT_REF") or "system"
    ).strip()
    if not default_backend_ref:
        raise ContainerBackendConfigError(
            "container backend defaultBackendRef must not be empty"
        )

    endpoint = (
        (source.get("SYSTEM_DOCKER_HOST") or "").strip()
        or (source.get("DOCKER_HOST") or "").strip()
        or _DEFAULT_DOCKER_ENDPOINT
    ) or None

    return ContainerBackendSettings(
        enabled=_coerce_bool(
            source.get("MOONMIND_CONTAINER_BACKEND_ENABLED"), default=True
        ),
        kind=kind,
        default_backend_ref=default_backend_ref,
        endpoint=endpoint,
        raw_cli_enabled=_coerce_bool(
            source.get("MOONMIND_CONTAINER_BACKEND_RAW_CLI_ENABLED"), default=False
        ),
        max_cpu_millis=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_CPU_MILLIS"),
            default=8000,
            minimum=1,
        ),
        max_memory_mib=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB"),
            default=16384,
            minimum=16,
        ),
        max_pids=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_PIDS"),
            default=2048,
            minimum=16,
        ),
        shm_size_mib=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB"),
            default=64,
            minimum=1,
        ),
        max_timeout_seconds=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_TIMEOUT_SECONDS"),
            default=14400,
            minimum=1,
        ),
        max_output_bytes=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_BYTES"),
            default=64_000,
            minimum=1024,
        ),
    )
