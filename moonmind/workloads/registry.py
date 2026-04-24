"""Runner profile registry and policy validation for workload requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    RunnerProfile,
    UnrestrictedContainerRequest,
    UnrestrictedDockerRequest,
    ValidatedWorkloadRequest,
    WorkflowDockerMode,
    WorkloadRequest,
    WorkloadOwnershipMetadata,
    helper_container_name,
    parse_cpu_units,
    parse_size_bytes,
    parse_workload_request,
)


class WorkloadPolicyError(ValueError):
    """Raised when workload profile or request policy validation fails."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "policy_denied",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})


class RunnerProfileRegistry:
    """Deployment-owned registry of curated workload runner profiles."""

    DEFAULT_ALLOWED_IMAGE_REGISTRIES = ("docker.io", "ghcr.io", "quay.io")

    def __init__(
        self,
        profiles: Iterable[RunnerProfile] = (),
        *,
        workspace_root: str | Path,
        allowed_image_registries: Iterable[str] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._allowed_image_registries = _normalize_registry_allowlist(
            allowed_image_registries
            if allowed_image_registries is not None
            else self.DEFAULT_ALLOWED_IMAGE_REGISTRIES
        )
        self._profiles: dict[str, RunnerProfile] = {}
        for profile in profiles:
            if profile.id in self._profiles:
                raise WorkloadPolicyError(
                    f"duplicate runner profile id: {profile.id}",
                    reason="duplicate_profile",
                    details={"profileId": profile.id},
                )
            self._validate_profile_image_policy(profile)
            self._profiles[profile.id] = profile

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    @property
    def allowed_image_registries(self) -> tuple[str, ...]:
        return self._allowed_image_registries

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
        allowed_image_registries: Iterable[str] | None = None,
    ) -> "RunnerProfileRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            return cls(
                (),
                workspace_root=workspace_root,
                allowed_image_registries=allowed_image_registries,
            )
        return cls.load_file(
            registry_path,
            workspace_root=workspace_root,
            allowed_image_registries=allowed_image_registries,
        )

    @classmethod
    def load_file(
        cls,
        path: str | Path,
        *,
        workspace_root: str | Path,
        allowed_image_registries: Iterable[str] | None = None,
    ) -> "RunnerProfileRegistry":
        registry_path = Path(path)
        raw_text = registry_path.read_text(encoding="utf-8")
        payload = _load_registry_payload(registry_path, raw_text)
        profiles = _extract_profiles(payload)
        return cls(
            profiles,
            workspace_root=workspace_root,
            allowed_image_registries=allowed_image_registries,
        )

    def validate_request(
        self,
        request: WorkloadRequest | UnrestrictedContainerRequest | UnrestrictedDockerRequest | Mapping[str, Any],
        *,
        workflow_docker_mode: WorkflowDockerMode | None = None,
    ) -> ValidatedWorkloadRequest:
        parsed_request = parse_workload_request(request) if isinstance(request, Mapping) else request

        if isinstance(parsed_request, WorkloadRequest):
            profile = self._profiles.get(parsed_request.profile_id)
            if profile is None:
                raise WorkloadPolicyError(
                    f"unknown runner profile: {parsed_request.profile_id}",
                    reason="unknown_profile",
                    details={"profileId": parsed_request.profile_id},
                )

            self._validate_workspace_path(parsed_request.repo_dir, field_name="repoDir")
            self._validate_workspace_path(
                parsed_request.artifacts_dir,
                field_name="artifactsDir",
            )
            self._validate_env_overrides(parsed_request, profile)
            self._validate_timeout(parsed_request, profile)
            self._validate_helper_ttl(parsed_request, profile)
            self._validate_resources(parsed_request, profile)
            container_name = parsed_request.container_name
            ownership = parsed_request.ownership_metadata(
                workflow_docker_mode=workflow_docker_mode or "profiles"
            )
            if profile.kind == "bounded_service":
                container_name = helper_container_name(
                    task_run_id=parsed_request.task_run_id,
                    step_id=parsed_request.step_id,
                    attempt=parsed_request.attempt,
                )
                ownership = WorkloadOwnershipMetadata(
                    kind="bounded_service",
                    taskRunId=parsed_request.task_run_id,
                    stepId=parsed_request.step_id,
                    attempt=parsed_request.attempt,
                    toolName=parsed_request.tool_name,
                    workloadProfile=parsed_request.profile_id,
                    sessionId=parsed_request.session_id,
                    sessionEpoch=parsed_request.session_epoch,
                    workloadAccess="profile",
                    workflowDockerMode=workflow_docker_mode or ownership.workflow_docker_mode,
                )

            return ValidatedWorkloadRequest(
                request=parsed_request,
                profile=profile,
                ownership=ownership,
                containerName=container_name,
            )

        self._validate_workspace_path(parsed_request.repo_dir, field_name="repoDir")
        self._validate_workspace_path(parsed_request.artifacts_dir, field_name="artifactsDir")
        if isinstance(parsed_request, UnrestrictedContainerRequest):
            self._validate_workspace_path(parsed_request.scratch_dir, field_name="scratchDir")
        return ValidatedWorkloadRequest(
            request=parsed_request,
            profile=None,
            ownership=parsed_request.ownership_metadata(
                workflow_docker_mode=workflow_docker_mode or "unrestricted"
            ),
            containerName=parsed_request.container_name,
        )

    def _validate_workspace_path(self, value: str, *, field_name: str) -> None:
        resolved = Path(value).expanduser().resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            raise WorkloadPolicyError(
                f"{field_name} must be under workspace root {self._workspace_root}",
                reason="disallowed_mount",
                details={"field": field_name, "workspaceRoot": str(self._workspace_root)},
            ) from exc

    def _validate_profile_image_policy(self, profile: RunnerProfile) -> None:
        image_registry = _image_registry(profile.image)
        if image_registry not in self._allowed_image_registries:
            raise WorkloadPolicyError(
                f"image registry {image_registry!r} is not allowed by workload policy",
                reason="disallowed_image_registry",
                details={"profileId": profile.id, "imageRegistry": image_registry},
            )

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
                    f"{profile.id}",
                    reason="disallowed_env_key",
                    details={"envKey": key, "profileId": profile.id},
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
                "timeoutSeconds exceeds profile maxTimeoutSeconds",
                reason="resource_request_too_large",
                details={"resource": "timeoutSeconds", "profileId": profile.id},
            )

    @staticmethod
    def _validate_helper_ttl(
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        if profile.kind != "bounded_service":
            if request.ttl_seconds is not None:
                raise WorkloadPolicyError(
                    "ttlSeconds is only valid for bounded_service profiles",
                    reason="unsupported_helper_ttl",
                    details={"profileId": profile.id},
                )
            return
        if request.ttl_seconds is None:
            raise WorkloadPolicyError(
                "ttlSeconds is required for bounded_service profiles",
                reason="missing_helper_ttl",
                details={"profileId": profile.id},
            )
        if (
            profile.max_helper_ttl_seconds is not None
            and request.ttl_seconds > profile.max_helper_ttl_seconds
        ):
            raise WorkloadPolicyError(
                "ttlSeconds exceeds profile maxHelperTtlSeconds",
                reason="resource_request_too_large",
                details={"resource": "ttlSeconds", "profileId": profile.id},
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
                raise WorkloadPolicyError(
                    "cpu override exceeds profile maxCpu",
                    reason="resource_request_too_large",
                    details={"resource": "cpu", "profileId": profile.id},
                )
        if resources.memory is not None and limits.max_memory is not None:
            if parse_size_bytes(resources.memory) > parse_size_bytes(limits.max_memory):
                raise WorkloadPolicyError(
                    "memory override exceeds profile maxMemory",
                    reason="resource_request_too_large",
                    details={"resource": "memory", "profileId": profile.id},
                )
        if resources.shm_size is not None and limits.max_shm_size is not None:
            if parse_size_bytes(resources.shm_size) > parse_size_bytes(
                limits.max_shm_size
            ):
                raise WorkloadPolicyError(
                    "shmSize override exceeds profile maxShmSize",
                    reason="resource_request_too_large",
                    details={"resource": "shmSize", "profileId": profile.id},
                )


def _load_registry_payload(path: Path, raw_text: str) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(raw_text or "{}")
        except json.JSONDecodeError as exc:
            raise WorkloadPolicyError(
                f"invalid runner profile registry JSON: {exc}"
            ) from exc
    if suffix in {".yaml", ".yml"}:
        try:
            return yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            raise WorkloadPolicyError(
                f"invalid runner profile registry YAML: {exc}"
            ) from exc
    raise WorkloadPolicyError(
        "runner profile registry must use a .json, .yaml, or .yml file extension"
    )


def _normalize_registry_allowlist(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        registry = str(value or "").strip().lower()
        if registry in {"", "index.docker.io", "registry-1.docker.io"}:
            registry = "docker.io"
        if registry and registry not in normalized:
            normalized.append(registry)
    return tuple(normalized)


def _image_registry(image: str) -> str:
    if "/" not in image:
        return "docker.io"
    first_segment = image.split("/", 1)[0].lower()
    if "." not in first_segment and ":" not in first_segment and first_segment != "localhost":
        return "docker.io"
    if first_segment in {"index.docker.io", "registry-1.docker.io"}:
        return "docker.io"
    return first_segment


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
