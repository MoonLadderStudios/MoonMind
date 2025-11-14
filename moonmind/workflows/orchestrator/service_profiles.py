"""Static service metadata used by the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class HealthCheck:
    """Configuration describing how to verify a service is healthy."""

    url: str
    method: str = "GET"
    timeout_seconds: int = 120
    interval_seconds: float = 5.0
    expected_status: int = 200


@dataclass(frozen=True)
class ServiceProfile:
    """Metadata describing how the orchestrator should manage a service."""

    key: str
    compose_service: str
    workspace_path: Path
    allowlist_globs: tuple[str, ...]
    description: str | None = None
    healthcheck: HealthCheck | None = None
    restart_timeout_seconds: int = 60
    compose_project: str = "moonmind"

    def validate_path(self, candidate: str | Path) -> bool:
        """Return ``True`` when ``candidate`` matches the allow-list."""

        from fnmatch import fnmatch

        path = Path(candidate)
        try:
            root = self.workspace_path.resolve()
            resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
            relative = resolved.relative_to(root)
        except ValueError:
            return False

        relative_str = str(relative)
        for pattern in self.allowlist_globs:
            normalized_pattern = pattern.lstrip("./")
            if fnmatch(relative_str, pattern) or fnmatch(
                relative_str, normalized_pattern
            ):
                return True
        return False


_DEFAULT_PROFILES: Mapping[str, ServiceProfile] = {
    "api": ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=Path("."),
        description="MoonMind API FastAPI service",
        allowlist_globs=(
            "api_service/Dockerfile",
            "api_service/Dockerfile.*",
            "api_service/pyproject.toml",
            "api_service/poetry.lock",
            "api_service/requirements*.txt",
            "pyproject.toml",
            "poetry.lock",
        ),
        healthcheck=HealthCheck(url="http://api:5000/health"),
    ),
    "celery-worker": ServiceProfile(
        key="celery-worker",
        compose_service="celery-worker",
        workspace_path=Path("."),
        description="Celery worker responsible for Spec Kit tasks",
        allowlist_globs=(
            "celery_worker/Dockerfile",
            "celery_worker/Dockerfile.*",
            "celery_worker/requirements*.txt",
            "celery_worker/pyproject.toml",
            "celery_worker/poetry.lock",
        ),
        healthcheck=None,
    ),
}


@lru_cache(maxsize=None)
def get_service_profile(service_name: str) -> ServiceProfile:
    """Return the configured profile for ``service_name``."""

    normalized = service_name.strip().lower()
    if normalized not in _DEFAULT_PROFILES:
        raise KeyError(f"Service '{service_name}' is not managed by the orchestrator")
    return _DEFAULT_PROFILES[normalized]


def list_service_profiles() -> Iterable[ServiceProfile]:
    """Return an iterable of all known service profiles."""

    return tuple(_DEFAULT_PROFILES.values())


def resolve_service_context(profile: ServiceProfile) -> dict[str, object]:
    """Return a JSON-serialisable payload describing ``profile`` for ActionPlans."""

    health = None
    if profile.healthcheck is not None:
        health = {
            "url": profile.healthcheck.url,
            "method": profile.healthcheck.method,
            "timeoutSeconds": profile.healthcheck.timeout_seconds,
            "intervalSeconds": profile.healthcheck.interval_seconds,
            "expectedStatus": profile.healthcheck.expected_status,
        }
    return {
        "service": profile.compose_service,
        "composeProject": profile.compose_project,
        "workspace": str(profile.workspace_path),
        "allowlist": list(profile.allowlist_globs),
        "healthcheck": health,
        "restartTimeoutSeconds": profile.restart_timeout_seconds,
    }


__all__ = [
    "HealthCheck",
    "ServiceProfile",
    "get_service_profile",
    "list_service_profiles",
    "resolve_service_context",
]
