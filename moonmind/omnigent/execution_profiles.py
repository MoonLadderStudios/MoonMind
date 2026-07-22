"""Versioned, MoonMind-owned Omnigent Codex launch contracts.

The catalog is intentionally built in for the first product slice.  Workflow
requests select stable refs; they never supply Docker or credential authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError

_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_PLACEHOLDER_DIGEST = "0" * 64
_SAFE_REF = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")


class OmnigentCodexExecutionProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(alias="profileId")
    version: int = Field(ge=1)
    display_name: str = Field(alias="displayName")
    enabled: bool = True
    endpoint_ref: str = Field(alias="endpointRef")
    agent_name: str = Field(alias="agentName")
    harness: Literal["codex-native"] = "codex-native"
    default_policy_ref: str = Field(alias="defaultPolicyRef")
    provider_runtime: Literal["codex_cli"] = Field("codex_cli", alias="providerRuntime")
    provider_auth: Literal["oauth_volume"] = Field("oauth_volume", alias="providerAuth")
    capture_defaults: dict[str, Any] = Field(alias="captureDefaults")
    model: str | None = None
    reasoning: str | None = None
    readiness: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"


class OmnigentLaunchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(alias="policyId")
    version: int = Field(ge=1)
    enabled: bool = True
    host_mode: Literal["static_compose", "on_demand_docker"] = Field(alias="hostMode")
    server_image_ref: str = Field(alias="serverImageRef")
    host_image_ref: str = Field(alias="hostImageRef")
    require_immutable_images: bool = Field(True, alias="requireImmutableImages")
    network_ref: str = Field(alias="networkRef")
    enforced_egress: bool = Field(alias="enforcedEgress")
    limits: dict[str, int]
    mount_classes: tuple[str, ...] = Field(alias="mountClasses")
    runtime_uid: int = Field(1000, alias="runtimeUid")
    runtime_gid: int = Field(1000, alias="runtimeGid")
    read_only_root: bool = Field(True, alias="readOnlyRoot")
    capture: dict[str, Any]
    cleanup: dict[str, Any]
    control_capabilities: tuple[str, ...] = Field(alias="controlCapabilities")

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@{self.version}"

    @model_validator(mode="after")
    def validate_authority(self) -> "OmnigentLaunchPolicy":
        if self.require_immutable_images:
            for label, image in (
                ("server", self.server_image_ref),
                ("host", self.host_image_ref),
            ):
                if not (_DIGEST_IMAGE.fullmatch(image) or image.startswith("bootstrap://")):
                    raise ValueError(f"{label} image must use an immutable sha256 digest")
                if image.endswith(_PLACEHOLDER_DIGEST):
                    raise ValueError(f"{label} image digest must not be a placeholder")
        if not _SAFE_REF.fullmatch(self.network_ref) or self.network_ref.startswith(
            ("/", ".")
        ):
            raise ValueError("networkRef must be a named deployment network")
        required_limits = {
            "cpuMillis",
            "memoryMiB",
            "processes",
            "timeoutSeconds",
            "temporaryStorageMiB",
        }
        if set(self.limits) != required_limits or any(
            not isinstance(v, int) or v <= 0 for v in self.limits.values()
        ):
            raise ValueError(
                "limits must contain positive cpu, memory, process, timeout, "
                "and temporary-storage values"
            )
        allowed_mounts = {
            "workspace",
            "oauth_home",
            "omnigent_state",
            "skills_tools",
            "artifacts",
            "cache",
        }
        if (
            not set(self.mount_classes) <= allowed_mounts
            or "oauth_home" not in self.mount_classes
        ):
            raise ValueError(
                "mountClasses contains an unsupported class or omits oauth_home"
            )
        if self.runtime_uid != 1000 or self.runtime_gid != 1000 or not self.read_only_root:
            raise ValueError("Codex hosts require UID/GID 1000 and a read-only root")
        if not self.enforced_egress:
            raise ValueError("Codex launch policy must enforce egress")
        return self


# Bootstrap values are resolved from operator-owned immutable configuration when
# the catalog is compiled. Mutable tags and synthetic placeholder digests never
# become product launch authority.
_IMAGE = "bootstrap://OMNIGENT_HOST_IMAGE_REF"
_SERVER_IMAGE = "bootstrap://OMNIGENT_IMAGE_REF"
_COMMON = dict(
    serverImageRef=_SERVER_IMAGE,
    hostImageRef=_IMAGE,
    networkRef="local-network",
    enforcedEgress=True,
    limits={
        "cpuMillis": 2000,
        "memoryMiB": 4096,
        "processes": 256,
        "timeoutSeconds": 5400,
        "temporaryStorageMiB": 256,
    },
    mountClasses=(
        "workspace",
        "oauth_home",
        "omnigent_state",
        "skills_tools",
    ),
    capture={"required": True, "retentionDays": 30},
    controlCapabilities=("interrupt", "terminate", "clear_context"),
)

POLICIES = {
    p.ref: p
    for p in (
        OmnigentLaunchPolicy(
            policyId="codex-static",
            version=1,
            hostMode="static_compose",
            cleanup={"mode": "drain", "janitor": True},
            **_COMMON,
        ),
        OmnigentLaunchPolicy(
            policyId="codex-on-demand",
            version=1,
            hostMode="on_demand_docker",
            cleanup={"mode": "remove", "janitor": True},
            **_COMMON,
        ),
    )
}
PROFILES = {
    p.ref: p
    for p in (
        OmnigentCodexExecutionProfile(
            profileId="omnigent-codex",
            version=1,
            displayName="Omnigent Codex",
            endpointRef="default",
            agentName="codex",
            defaultPolicyRef="codex-static@1",
            captureDefaults={"required": True, "retentionDays": 30},
            readiness={"requiresProviderLaunchReady": True, "validationVersion": 1},
        ),
    )
}


def public_execution_catalog() -> dict[str, Any]:
    """Return the safe, product-selectable built-in catalog for Workflow Create."""

    return {
        "profiles": [
            profile.model_dump(by_alias=True, mode="json") | {"ref": profile.ref}
            for profile in PROFILES.values()
            if profile.enabled
        ],
        "policies": [
            policy.model_dump(by_alias=True, mode="json") | {"ref": policy.ref}
            for policy in POLICIES.values()
            if policy.enabled
        ],
    }


def compile_effective_launch(
    *, profile_ref: str, policy_ref: str | None, provider_profile_id: str
) -> dict[str, Any]:
    profile = PROFILES.get(profile_ref)
    if profile is None or not profile.enabled:
        raise OmnigentOAuthHostError(
            "Omnigent execution profile is missing or disabled",
            code="OMNIGENT_EXECUTION_PROFILE_UNAVAILABLE",
        )
    selected_policy_ref = policy_ref or profile.default_policy_ref
    policy = POLICIES.get(selected_policy_ref)
    if policy is None or not policy.enabled:
        raise OmnigentOAuthHostError(
            "Omnigent launch policy is missing or disabled",
            code="OMNIGENT_LAUNCH_POLICY_UNAVAILABLE",
        )
    policy_payload = policy.model_dump(by_alias=True, mode="json")
    for field, variable in (
        ("serverImageRef", "OMNIGENT_IMAGE_REF"),
        ("hostImageRef", "OMNIGENT_HOST_IMAGE_REF"),
    ):
        value = policy_payload[field]
        if str(value).startswith("bootstrap://"):
            value = os.getenv(variable, "").strip()
        if not _DIGEST_IMAGE.fullmatch(str(value)) or str(value).endswith(_PLACEHOLDER_DIGEST):
            raise OmnigentOAuthHostError(
                f"{variable} must name a deployable immutable sha256 image",
                code="OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
            )
        policy_payload[field] = value
    payload = {
        "schemaVersion": 1,
        "executionProfileRef": profile.ref,
        "launchPolicyRef": policy.ref,
        "providerProfileId": provider_profile_id,
        "endpointRef": profile.endpoint_ref,
        "agentName": profile.agent_name,
        "harness": profile.harness,
        **policy_payload,
        "capture": {**profile.capture_defaults, **policy.capture},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    return payload


def validate_effective_launch_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Reject mutated or credential-bearing snapshots before they become authority."""

    payload = dict(snapshot)
    supplied_ref = str(payload.pop("snapshotRef", ""))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected_ref = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    if supplied_ref != expected_ref:
        raise OmnigentOAuthHostError(
            "effective launch snapshot digest does not match its content",
            code="OMNIGENT_EFFECTIVE_LAUNCH_CONFLICT",
        )
    serialized = canonical.lower()
    if any(marker in serialized for marker in ("credential", "password", "token", "docker.sock")):
        raise OmnigentOAuthHostError(
            "effective launch snapshot contains forbidden authority",
            code="OMNIGENT_EFFECTIVE_LAUNCH_FORBIDDEN_AUTHORITY",
        )


def selection_from_request(
    parameters: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    omnigent = parameters.get("omnigent") if isinstance(parameters, Mapping) else None
    if not isinstance(omnigent, Mapping):
        return None, None
    forbidden = {
        "hostid",
        "host_id",
        "volumename",
        "volume_ref",
        "credential",
        "bindsource",
        "absolutebindsource",
    }

    def contains_forbidden(value: object) -> bool:
        if isinstance(value, Mapping):
            return any(
                str(key).replace("-", "").lower() in forbidden
                or contains_forbidden(nested)
                for key, nested in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(contains_forbidden(item) for item in value)
        return False

    if contains_forbidden(omnigent):
        raise OmnigentOAuthHostError(
            "workflow request contains forbidden host or credential authority",
            code="OMNIGENT_LAUNCH_POLICY_FORBIDDEN_INPUT",
        )
    profile_ref = str(omnigent.get("executionTargetRef") or "").strip() or None
    policy_ref = str(omnigent.get("launchPolicyRef") or "").strip() or None
    return profile_ref, policy_ref
