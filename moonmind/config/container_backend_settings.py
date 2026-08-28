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

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

#: The only backend kind this deployment supports. A second backend kind
#: (Podman, containerd, Kubernetes, endpoint pools) is explicitly out of scope.
SUPPORTED_CONTAINER_BACKEND_KINDS: Final[frozenset[str]] = frozenset({"docker-engine"})

#: Deployment default when the endpoint is not otherwise configured. Matches the
#: ``docker-proxy`` service the ``temporal-worker-agent-runtime`` container reaches.
_DEFAULT_DOCKER_ENDPOINT: Final[str] = "tcp://docker-proxy:2375"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSEY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})

PYTHON_TEST_IMAGE_SOURCE_REF: Final[str] = "moonmind-python-tests"
PYTHON_TEST_LOCAL_IMAGE: Final[str] = "moonmind-python-tests:local"
PYTHON_TEST_RECIPE_VERSION: Final[str] = "v1"

#: Deployment-declared registry image sources, as a JSON array of
#: ``{"sourceRef", "image", "pullPolicy"}`` objects.
IMAGE_SOURCES_ENV_KEY: Final[str] = "MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES"

#: Deployment-declared named-volume cache sources, as a JSON array of
#: ``{"cacheRef", "volumeName", "target", "readOnly"}`` objects.
CACHE_SOURCES_ENV_KEY: Final[str] = "MOONMIND_CONTAINER_BACKEND_CACHE_SOURCES"
PYTHON_TEST_FINGERPRINT_INPUTS: Final[tuple[str, ...]] = (
    ".dockerignore",
    "api_service/Dockerfile",
    "api_service/docker/**/*",
    "api_service/config.template.toml",
    "pyproject.toml",
    "poetry.lock",
    "README.md",
    "LICENSE",
    "NOTICE",
)


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


def _coerce_optional_int(value: object, *, minimum: int) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _coerce_int(value, default=minimum, minimum=minimum)


@dataclass(frozen=True)
class RegistryImageSource:
    """Deployment-owned registry image selected through an opaque source name."""

    source_ref: str
    image: str
    pull_policy: str = "if-missing"


@dataclass(frozen=True)
class LocalImageRecipe:
    """Deployment-owned local build recipe; none of its fields are caller input."""

    source_ref: str
    image: str
    context_root: Path
    dockerfile: str
    target: str
    build_args: tuple[tuple[str, str], ...]
    fingerprint_inputs: tuple[str, ...]
    recipe_version: str
    max_age_seconds: int | None
    validation_command: tuple[str, ...]
    validation_network_mode: str = "none"


ImageSource = RegistryImageSource | LocalImageRecipe


@dataclass(frozen=True)
class CacheSource:
    """Deployment-owned named-volume mount selected through an opaque ref."""

    cache_ref: str
    volume_name: str
    target: str
    read_only: bool = False


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
    max_active_memory_mib: int | None
    max_pids: int
    #: Highest device count a caller may request. ``None`` imposes no MoonMind
    #: ceiling, so host capability remains the only gate; ``0`` refuses every
    #: device request on this deployment.
    max_gpu_count: int | None
    #: Shared-memory size applied when a caller requests none.
    shm_size_mib: int
    #: Highest shared-memory size a caller may request, in MiB. A larger
    #: request is refused at the launch boundary rather than clamped.
    max_shm_size_mib: int
    max_timeout_seconds: int
    max_output_bytes: int
    max_output_files: int
    max_output_total_bytes: int
    image_sources: tuple[ImageSource, ...]
    cache_sources: tuple[CacheSource, ...]

    def require_endpoint(self) -> str:
        """Return the configured endpoint or fail readiness with a bounded error."""

        if self.endpoint is None or not self.endpoint.strip():
            raise ContainerBackendReadinessError(
                "container backend endpoint is not configured; set "
                "SYSTEM_DOCKER_HOST (or DOCKER_HOST) on the trusted worker"
            )
        return self.endpoint

    def image_source(self, source_ref: str) -> ImageSource:
        """Resolve one deployment-approved image source or fail closed."""

        for source in self.image_sources:
            if source.source_ref == source_ref:
                return source
        raise ContainerBackendConfigError(
            f"container image source {source_ref!r} is not configured"
        )

    def cache_source(self, cache_ref: str) -> CacheSource:
        """Resolve one deployment-approved cache source or fail closed."""

        for source in self.cache_sources:
            if source.cache_ref == cache_ref:
                return source
        raise ContainerBackendConfigError(
            f"container cache source {cache_ref!r} is not configured"
        )


def _named_volume(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not all(
        character.isalnum() or character in "_.-" for character in normalized
    ):
        raise ContainerBackendConfigError(
            f"{field_name} must be a non-empty Docker named volume"
        )
    return normalized


def _registry_pull_policy(value: object, *, default: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in {"if-missing", "always", "never"}:
        raise ContainerBackendConfigError(
            "container registry image pull policy must be one of "
            "if-missing, always, never"
        )
    return normalized


def _mount_target(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized.startswith("/")
        or len(normalized) > 512
        or ".." in normalized.split("/")
    ):
        raise ContainerBackendConfigError(
            f"{field_name} must be a normalized absolute container path"
        )
    return normalized


def _declaration_objects(
    raw: object, *, field_name: str
) -> tuple[Mapping[str, Any], ...]:
    """Parse one deployment-declared JSON array of source objects."""

    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContainerBackendConfigError(
            f"{field_name} must be a JSON array of objects: {exc.msg}"
        ) from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, Mapping) for item in parsed
    ):
        raise ContainerBackendConfigError(
            f"{field_name} must be a JSON array of objects"
        )
    return tuple(parsed)


def _declared_image_sources(
    raw: object, *, reserved_refs: frozenset[str]
) -> tuple[RegistryImageSource, ...]:
    """Resolve the deployment's own registry image sources.

    MoonMind ships no project image defaults: a caller selects an image through
    an opaque ``sourceRef`` and the deployment decides what that ref resolves
    to. An unknown ref already fails closed in ``image_source``.
    """

    sources: list[RegistryImageSource] = []
    seen: set[str] = set()
    for index, item in enumerate(
        _declaration_objects(raw, field_name=IMAGE_SOURCES_ENV_KEY)
    ):
        field = f"{IMAGE_SOURCES_ENV_KEY}[{index}]"
        source_ref = str(item.get("sourceRef") or "").strip()
        image = str(item.get("image") or "").strip()
        if not source_ref or not image:
            raise ContainerBackendConfigError(
                f"{field} requires a non-empty sourceRef and image"
            )
        if source_ref in reserved_refs:
            raise ContainerBackendConfigError(
                f"{field} sourceRef {source_ref!r} is reserved by MoonMind"
            )
        if source_ref in seen:
            raise ContainerBackendConfigError(
                f"{field} declares duplicate sourceRef {source_ref!r}"
            )
        seen.add(source_ref)
        sources.append(
            RegistryImageSource(
                source_ref=source_ref,
                image=image,
                # First use is cold-start provisioning, not an operator-only
                # prewarm requirement. Private registries still fail through
                # the normal credential-aware pull contract when authorization
                # is unavailable; deployments may explicitly select ``never``.
                pull_policy=_registry_pull_policy(
                    item.get("pullPolicy"), default="if-missing"
                ),
            )
        )
    return tuple(sources)


def _declared_cache_sources(raw: object) -> tuple[CacheSource, ...]:
    """Resolve the deployment's own named-volume cache sources."""

    sources: list[CacheSource] = []
    seen: set[str] = set()
    for index, item in enumerate(
        _declaration_objects(raw, field_name=CACHE_SOURCES_ENV_KEY)
    ):
        field = f"{CACHE_SOURCES_ENV_KEY}[{index}]"
        cache_ref = str(item.get("cacheRef") or "").strip()
        if not cache_ref:
            raise ContainerBackendConfigError(
                f"{field} requires a non-empty cacheRef"
            )
        if cache_ref in seen:
            raise ContainerBackendConfigError(
                f"{field} declares duplicate cacheRef {cache_ref!r}"
            )
        seen.add(cache_ref)
        sources.append(
            CacheSource(
                cache_ref=cache_ref,
                volume_name=_named_volume(
                    item.get("volumeName"), field_name=f"{field}.volumeName"
                ),
                target=_mount_target(
                    item.get("target"), field_name=f"{field}.target"
                ),
                read_only=_coerce_bool(item.get("readOnly"), default=False),
            )
        )
    return tuple(sources)


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

    prebuilt_python_test_image = str(
        source.get("MOONMIND_PYTHON_TEST_IMAGE") or ""
    ).strip()
    if prebuilt_python_test_image:
        python_test_source: ImageSource = RegistryImageSource(
            source_ref=PYTHON_TEST_IMAGE_SOURCE_REF,
            image=prebuilt_python_test_image,
        )
    else:
        project_root = Path(
            str(
                source.get("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR")
                or Path(__file__).resolve().parents[2]
            )
        ).resolve()
        python_test_source = LocalImageRecipe(
            source_ref=PYTHON_TEST_IMAGE_SOURCE_REF,
            image=PYTHON_TEST_LOCAL_IMAGE,
            context_root=project_root,
            dockerfile="api_service/Dockerfile",
            target="test-runtime",
            build_args=(
                ("INSTALL_CODEX_CLI", "false"),
                ("INSTALL_TEST_DEPS", "true"),
            ),
            fingerprint_inputs=PYTHON_TEST_FINGERPRINT_INPUTS,
            recipe_version=PYTHON_TEST_RECIPE_VERSION,
            max_age_seconds=_coerce_optional_int(
                source.get("MOONMIND_PYTHON_TEST_IMAGE_MAX_AGE_SECONDS"),
                minimum=1,
            ),
            validation_command=("python", "-c", "import pytest"),
        )

    # The python-test image is MoonMind's own self-test surface and is the only
    # image source MoonMind itself owns. Every other image and cache source is
    # declared by the deployment, so no project's image, volume, or
    # in-container path is knowledge this module carries.
    image_sources: list[ImageSource] = [python_test_source]
    image_sources.extend(
        _declared_image_sources(
            source.get(IMAGE_SOURCES_ENV_KEY),
            reserved_refs=frozenset({PYTHON_TEST_IMAGE_SOURCE_REF}),
        )
    )
    cache_sources = _declared_cache_sources(source.get(CACHE_SOURCES_ENV_KEY))

    max_memory_mib = _coerce_int(
        source.get("MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB"),
        default=16384,
        minimum=16,
    )

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
        max_memory_mib=max_memory_mib,
        max_active_memory_mib=_coerce_optional_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_MEMORY_MIB"),
            minimum=16,
        ),
        max_pids=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_PIDS"),
            default=2048,
            minimum=16,
        ),
        max_gpu_count=_coerce_optional_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_GPU_COUNT"),
            minimum=0,
        ),
        shm_size_mib=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB"),
            default=64,
            minimum=1,
        ),
        # Defaults to the memory ceiling so an omitted value admits any
        # shared-memory request the job's own memory ceiling already permits,
        # and never publishes a shared-memory allowance larger than that.
        max_shm_size_mib=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB"),
            default=max_memory_mib,
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
        # Non-overridable ceilings on declared-output collection: reject a job
        # that would publish an excessive number of files or an excessive total
        # size rather than silently truncating collected evidence.
        max_output_files=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_FILES"),
            default=1024,
            minimum=1,
        ),
        max_output_total_bytes=_coerce_int(
            source.get("MOONMIND_CONTAINER_BACKEND_MAX_OUTPUT_TOTAL_BYTES"),
            default=256 * 1024 * 1024,
            minimum=1024,
        ),
        image_sources=tuple(image_sources),
        cache_sources=cache_sources,
    )
