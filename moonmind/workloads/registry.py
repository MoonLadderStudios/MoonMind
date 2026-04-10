"""Runner profile registry and policy validation for workload requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    RunnerProfile,
    ValidatedWorkloadRequest,
    WorkloadRequest,
    parse_cpu_units,
    parse_size_bytes,
)


class WorkloadPolicyError(ValueError):
    """Raised when workload profile or request policy validation fails."""


class RunnerProfileRegistry:
    """Deployment-owned registry of curated workload runner profiles."""

    def __init__(
        self,
        profiles: Iterable[RunnerProfile] = (),
        *,
        workspace_root: str | Path,
    ) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._profiles: dict[str, RunnerProfile] = {}
        for profile in profiles:
            if profile.id in self._profiles:
                raise WorkloadPolicyError(
                    f"duplicate runner profile id: {profile.id}"
                )
            self._profiles[profile.id] = profile

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def get(self, profile_id: str) -> RunnerProfile | None:
        return self._profiles.get(profile_id)

    @classmethod
    def empty(cls, *, workspace_root: str | Path) -> "RunnerProfileRegistry":
        return cls((), workspace_root=workspace_root)

    @classmethod
    def load_optional_file(
        cls,
        path: str | Path,
        *,
        workspace_root: str | Path,
    ) -> "RunnerProfileRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            return cls.empty(workspace_root=workspace_root)
        return cls.load_file(registry_path, workspace_root=workspace_root)

    @classmethod
    def load_file(
        cls,
        path: str | Path,
        *,
        workspace_root: str | Path,
    ) -> "RunnerProfileRegistry":
        registry_path = Path(path)
        raw_text = registry_path.read_text(encoding="utf-8")
        payload = _load_registry_payload(registry_path, raw_text)
        profiles = _extract_profiles(payload)
        return cls(profiles, workspace_root=workspace_root)

    def validate_request(
        self,
        request: WorkloadRequest | Mapping[str, Any],
    ) -> ValidatedWorkloadRequest:
        parsed_request = (
            request
            if isinstance(request, WorkloadRequest)
            else WorkloadRequest.model_validate(request)
        )
        profile = self._profiles.get(parsed_request.profile_id)
        if profile is None:
            raise WorkloadPolicyError(
                f"unknown runner profile: {parsed_request.profile_id}"
            )

        self._validate_workspace_path(parsed_request.repo_dir, field_name="repoDir")
        self._validate_workspace_path(
            parsed_request.artifacts_dir,
            field_name="artifactsDir",
        )
        self._validate_env_overrides(parsed_request, profile)
        self._validate_timeout(parsed_request, profile)
        self._validate_resources(parsed_request, profile)

        return ValidatedWorkloadRequest(
            request=parsed_request,
            profile=profile,
            ownership=parsed_request.ownership_metadata(),
            containerName=parsed_request.container_name,
        )

    def _validate_workspace_path(self, value: str, *, field_name: str) -> None:
        resolved = Path(value).expanduser().resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise WorkloadPolicyError(
                f"{field_name} must be under workspace root {self._workspace_root}"
            ) from exc

    @staticmethod
    def _validate_env_overrides(
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        allowed = set(profile.env_allowlist)
        for key in request.env_overrides:
            if key not in allowed:
                raise WorkloadPolicyError(
                    f"environment override {key!r} is not allowed by profile "
                    f"{profile.id}"
                )

    @staticmethod
    def _validate_timeout(
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        if request.timeout_seconds is None or profile.max_timeout_seconds is None:
            return
        if request.timeout_seconds > profile.max_timeout_seconds:
            raise WorkloadPolicyError(
                "timeoutSeconds exceeds profile maxTimeoutSeconds"
            )

    @staticmethod
    def _validate_resources(
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        resources = request.resources
        limits = profile.resources
        if resources.cpu is not None and limits.max_cpu is not None:
            if parse_cpu_units(resources.cpu) > parse_cpu_units(limits.max_cpu):
                raise WorkloadPolicyError("cpu override exceeds profile maxCpu")
        if resources.memory is not None and limits.max_memory is not None:
            if parse_size_bytes(resources.memory) > parse_size_bytes(limits.max_memory):
                raise WorkloadPolicyError(
                    "memory override exceeds profile maxMemory"
                )
        if resources.shm_size is not None and limits.max_shm_size is not None:
            if parse_size_bytes(resources.shm_size) > parse_size_bytes(
                limits.max_shm_size
            ):
                raise WorkloadPolicyError(
                    "shmSize override exceeds profile maxShmSize"
                )


def _load_registry_payload(path: Path, raw_text: str) -> Any:
    if path.suffix.lower() == ".json":
        return json.loads(raw_text or "{}")
    return yaml.safe_load(raw_text) or {}


def _extract_profiles(payload: Any) -> list[RunnerProfile]:
    if isinstance(payload, Mapping):
        if "profiles" in payload:
            raw_profiles = payload["profiles"]
        else:
            raw_profiles = []
            for profile_id, raw_profile in payload.items():
                if not isinstance(raw_profile, Mapping):
                    raise WorkloadPolicyError(
                        "runner profile mapping values must be objects"
                    )
                profile_payload = dict(raw_profile)
                profile_payload.setdefault("id", str(profile_id))
                raw_profiles.append(profile_payload)
    else:
        raw_profiles = payload

    if raw_profiles in (None, ""):
        return []
    if not isinstance(raw_profiles, list):
        raise WorkloadPolicyError("runner profile registry must contain a list")

    profiles: list[RunnerProfile] = []
    seen: set[str] = set()
    for raw_profile in raw_profiles:
        try:
            profile = RunnerProfile.model_validate(raw_profile)
        except ValidationError:
            raise
        except Exception as exc:
            raise WorkloadPolicyError(f"invalid runner profile: {exc}") from exc
        if profile.id in seen:
            raise WorkloadPolicyError(f"duplicate runner profile id: {profile.id}")
        seen.add(profile.id)
        profiles.append(profile)
    return profiles
