"""Runner profile registry and policy validation for workload requests."""

from __future__ import annotations

import json
import logging
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


logger = logging.getLogger(__name__)


class WorkloadPolicyError(ValueError):
    """Raised when workload profile or request policy validation fails."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "policy_denied",
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.diagnostics = {
            "reason": reason,
            **dict(diagnostics or {}),
        }


class RunnerProfileRegistry:
    """Deployment-owned registry of curated workload runner profiles."""

    def __init__(
        self,
        profiles: Iterable[RunnerProfile] = (),
        *,
        workspace_root: str | Path,
        allowed_image_registries: Iterable[str] = (),
    ) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._allowed_image_registries = tuple(
            str(item).strip().rstrip("/")
            for item in allowed_image_registries
            if str(item).strip()
        )
        self._profiles: dict[str, RunnerProfile] = {}
        for profile in profiles:
            if profile.id in self._profiles:
                raise WorkloadPolicyError(
                    f"duplicate runner profile id: {profile.id}",
                    reason="duplicate_profile",
                    diagnostics={"profileId": profile.id},
                )
            self._validate_profile_policy(profile)
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
    def empty(
        cls,
        *,
        workspace_root: str | Path,
        allowed_image_registries: Iterable[str] = (),
    ) -> "RunnerProfileRegistry":
        return cls(
            (),
            workspace_root=workspace_root,
            allowed_image_registries=allowed_image_registries,
        )

    @classmethod
    def load_optional_file(
        cls,
        path: str | Path,
        *,
        workspace_root: str | Path,
        allowed_image_registries: Iterable[str] = (),
    ) -> "RunnerProfileRegistry":
        registry_path = Path(path)
        if not registry_path.exists():
            return cls.empty(
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
        allowed_image_registries: Iterable[str] = (),
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
        request: WorkloadRequest | Mapping[str, Any],
    ) -> ValidatedWorkloadRequest:
        parsed_request = (
            request
            if isinstance(request, WorkloadRequest)
            else WorkloadRequest.model_validate(request)
        )
        profile = self._profiles.get(parsed_request.profile_id)
        if profile is None:
            self._deny(
                f"unknown runner profile: {parsed_request.profile_id}",
                reason="unknown_profile",
                diagnostics={"profileId": parsed_request.profile_id},
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

    def _validate_profile_policy(self, profile: RunnerProfile) -> None:
        self._validate_profile_image(profile)
        for mount in (*profile.required_mounts, *profile.optional_mounts):
            if _is_auth_volume_name(mount.source):
                self._deny(
                    "runner profile must not inherit Codex/Claude/Gemini auth volumes",
                    reason="disallowed_auth_volume",
                    diagnostics={
                        "profileId": profile.id,
                        "mountSource": mount.source,
                        "mountTarget": mount.target,
                    },
                )

    def _validate_profile_image(self, profile: RunnerProfile) -> None:
        if not self._allowed_image_registries:
            return
        image_scope = _image_registry_scope(profile.image)
        for allowed in self._allowed_image_registries:
            if image_scope == allowed or image_scope.startswith(f"{allowed}/"):
                return
        self._deny(
            f"image registry for {profile.image!r} is not allowed",
            reason="disallowed_image_registry",
            diagnostics={
                "profileId": profile.id,
                "image": profile.image,
                "imageRegistry": image_scope.split("/", 1)[0],
                "allowedImageRegistries": list(self._allowed_image_registries),
            },
        )

    def _validate_workspace_path(self, value: str, *, field_name: str) -> None:
        resolved = Path(value).expanduser().resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError as exc:
            self._deny(
                f"{field_name} must be under workspace root {self._workspace_root}",
                reason="disallowed_mount",
                diagnostics={
                    "field": field_name,
                    "path": str(value),
                    "workspaceRoot": str(self._workspace_root),
                },
            )

    def _validate_env_overrides(
        self,
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        allowed = set(profile.env_allowlist)
        for key in request.env_overrides:
            if key not in allowed:
                self._deny(
                    f"environment override {key!r} is not allowed by profile "
                    f"{profile.id}",
                    reason="disallowed_env_key",
                    diagnostics={"profileId": profile.id, "envKey": key},
                )

    def _validate_timeout(
        self,
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        if request.timeout_seconds is None or profile.max_timeout_seconds is None:
            return
        if request.timeout_seconds > profile.max_timeout_seconds:
            self._deny(
                "timeoutSeconds exceeds profile maxTimeoutSeconds",
                reason="timeout_too_large",
                diagnostics={
                    "profileId": profile.id,
                    "timeoutSeconds": request.timeout_seconds,
                    "maxTimeoutSeconds": profile.max_timeout_seconds,
                },
            )

    def _validate_resources(
        self,
        request: WorkloadRequest,
        profile: RunnerProfile,
    ) -> None:
        resources = request.resources
        limits = profile.resources
        if resources.cpu is not None and limits.max_cpu is not None:
            if parse_cpu_units(resources.cpu) > parse_cpu_units(limits.max_cpu):
                self._deny(
                    "cpu override exceeds profile maxCpu",
                    reason="resource_request_too_large",
                    diagnostics={
                        "profileId": profile.id,
                        "resource": "cpu",
                        "requested": resources.cpu,
                        "maximum": limits.max_cpu,
                    },
                )
        if resources.memory is not None and limits.max_memory is not None:
            if parse_size_bytes(resources.memory) > parse_size_bytes(limits.max_memory):
                self._deny(
                    "memory override exceeds profile maxMemory",
                    reason="resource_request_too_large",
                    diagnostics={
                        "profileId": profile.id,
                        "resource": "memory",
                        "requested": resources.memory,
                        "maximum": limits.max_memory,
                    },
                )
        if resources.shm_size is not None and limits.max_shm_size is not None:
            if parse_size_bytes(resources.shm_size) > parse_size_bytes(
                limits.max_shm_size
            ):
                self._deny(
                    "shmSize override exceeds profile maxShmSize",
                    reason="resource_request_too_large",
                    diagnostics={
                        "profileId": profile.id,
                        "resource": "shmSize",
                        "requested": resources.shm_size,
                        "maximum": limits.max_shm_size,
                    },
                )

    def _deny(
        self,
        message: str,
        *,
        reason: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        logger.warning(
            "Docker workload policy denied: %s",
            message,
            extra={
                "workload_policy_denial": {
                    "reason": reason,
                    **dict(diagnostics or {}),
                }
            },
        )
        raise WorkloadPolicyError(
            message,
            reason=reason,
            diagnostics=diagnostics,
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
            raise WorkloadPolicyError(
                f"duplicate runner profile id: {profile.id}",
                reason="duplicate_profile",
                diagnostics={"profileId": profile.id},
            )
        seen.add(profile.id)
        profiles.append(profile)
    return profiles


def _image_registry_scope(image: str) -> str:
    image_without_digest = str(image).split("@", 1)[0]
    first, sep, _rest = image_without_digest.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return image_without_digest
    return f"docker.io/{image_without_digest}"


def _is_auth_volume_name(source: str) -> bool:
    normalized = str(source).lower()
    auth_markers = ("auth", "credential", "credentials", "token", "secret")
    runtime_markers = ("codex", "claude", "gemini")
    return any(marker in normalized for marker in auth_markers) and any(
        marker in normalized for marker in runtime_markers
    )
