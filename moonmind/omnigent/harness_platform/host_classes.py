"""Host Class selection and launch-policy contracts.

Production Host Classes are compiled from deployment runtime-pack templates and
an exact persisted harness-catalog row. This module deliberately registers no
synthetic Host Classes at import time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

OMNIGENT_OPENCODE_HOST_IMAGE_ENV = "OMNIGENT_OPENCODE_HOST_IMAGE_REF"
OMNIGENT_PI_HOST_IMAGE_ENV = "OMNIGENT_PI_HOST_IMAGE_REF"
OPENCODE_PINNED_VERSION = "1.18.11"
OPENCODE_SUPPORTED_RANGE = ">=1.17.7,<1.19.0"


def _persisted_image_ref(key: str) -> str:
    """Return the digest this deployment already resolved for ``key``.

    Only the API process resolves images, but every process that launches a host
    selects a Host Class. Execution workers start with the runtime-pack refs
    unset on the canonical Compose path and read the same persisted resolved
    state, so consulting it here is what keeps an advertised target launchable
    on the worker that actually runs it. The value is still validated as a
    digest-pinned ref below, and an operator-supplied environment value always
    wins.
    """

    try:
        from moonmind.omnigent.bootstrap.store import load_resolved_state

        state = load_resolved_state()
    except Exception:
        return ""
    if state is None:
        return ""
    attribute = {
        OMNIGENT_OPENCODE_HOST_IMAGE_ENV: "opencode_host_image_ref",
        OMNIGENT_PI_HOST_IMAGE_ENV: "pi_host_image_ref",
    }.get(key)
    if attribute is None:
        return ""
    return str(getattr(state, attribute, "") or "").strip()


def _require_image_ref(environment: Mapping[str, str], key: str) -> str:
    raw = str(environment.get(key) or "").strip()
    if not raw:
        raw = _persisted_image_ref(key)
    if not raw or not _IMAGE_RE.fullmatch(raw):
        raise HarnessPlatformError(
            f"{key} must be set to a digest-pinned image ref",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if raw.endswith("0" * 64) or raw.endswith("c" * 64):
        raise HarnessPlatformError(
            f"{key} digest must not be a placeholder",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    return raw


def get_opencode_host_image_ref() -> str:
    return _require_image_ref(os.environ, OMNIGENT_OPENCODE_HOST_IMAGE_ENV)


def get_pi_host_image_ref() -> str:
    return _require_image_ref(os.environ, OMNIGENT_PI_HOST_IMAGE_ENV)


class HostClassHarnessEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    harnessId: str = Field(alias="harnessId")
    implementationRef: str = Field(alias="implementationRef")
    runtimeDependencies: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple, alias="runtimeDependencies"
    )


class HostClass(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-host-class.v1", alias="schemaVersion")
    hostClassId: str = Field(alias="hostClassId")
    version: int = Field(ge=1)
    imageRef: str = Field(alias="imageRef")
    omnigentVersion: str = Field(alias="omnigentVersion")
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    architectures: tuple[str, ...]
    declaredHarnessImplementations: tuple[HostClassHarnessEntry, ...] = Field(
        alias="declaredHarnessImplementations"
    )
    integrationModes: tuple[str, ...] = Field(alias="integrationModes")
    materializerRefs: tuple[str, ...] = Field(alias="materializerRefs")
    features: dict[str, bool]
    runtime: dict[str, Any]

    @model_validator(mode="after")
    def validate_top(self) -> "HostClass":
        if not _SAFE_ID_RE.fullmatch(self.hostClassId):
            raise ValueError("invalid hostClassId")
        if not _IMAGE_RE.fullmatch(self.imageRef):
            raise ValueError("imageRef must be digest-pinned")
        if not _DIGEST_RE.fullmatch(self.omnigentBuildDigest):
            raise ValueError("omnigentBuildDigest must be sha256")
        if not self.architectures:
            raise ValueError("architectures required")
        if not self.integrationModes:
            raise ValueError("integrationModes required")
        for entry in self.declaredHarnessImplementations:
            if not entry.implementationRef.startswith(
                "omnigent-harness-implementation:sha256:"
            ):
                raise ValueError("implementationRef invalid")
        return self

    @property
    def ref(self) -> str:
        return f"{self.hostClassId}@{self.version}"

    def declares_harness(self, harness_id: str, implementation_ref: str) -> bool:
        return any(
            entry.harnessId == harness_id
            and entry.implementationRef == implementation_ref
            for entry in self.declaredHarnessImplementations
        )

    def supports_materializer(self, materializer_ref: str) -> bool:
        return materializer_ref in self.materializerRefs


@dataclass(frozen=True)
class HostClassTemplate:
    """Deployment-owned runtime-pack compatibility declaration."""

    host_class_id: str
    version: int
    harness_ids: tuple[str, ...]
    image_env: str
    integration_modes: tuple[str, ...]
    materializer_refs: tuple[str, ...]
    architectures: tuple[str, ...] = ("linux/amd64", "linux/arm64")
    runtime_dependencies: tuple[dict[str, Any], ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.host_class_id}@{self.version}"


DEFAULT_HOST_CLASS_TEMPLATES: tuple[HostClassTemplate, ...] = (
    HostClassTemplate(
        host_class_id="omnigent-opencode",
        version=1,
        harness_ids=("opencode-native",),
        image_env=OMNIGENT_OPENCODE_HOST_IMAGE_ENV,
        integration_modes=("native-server",),
        materializer_refs=("opencode-auth-json@1",),
        runtime_dependencies=(
            {"name": "opencode", "version": OPENCODE_PINNED_VERSION},
        ),
    ),
    HostClassTemplate(
        host_class_id="omnigent-pi",
        version=1,
        harness_ids=("pi-native",),
        image_env=OMNIGENT_PI_HOST_IMAGE_ENV,
        integration_modes=("native-server",),
        materializer_refs=(
            "omnigent-provider-config@1",
            "host-owned-auth@1",
            "none@1",
        ),
    ),
)


class OmnigentHostClassSelector:
    """Select a Host Class from catalog identity and deployment data."""

    def __init__(
        self,
        *,
        templates: tuple[HostClassTemplate, ...] = DEFAULT_HOST_CLASS_TEMPLATES,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._templates = templates
        self._environment = environment if environment is not None else os.environ

    def select(
        self,
        *,
        harness: Any,
        omnigent_version: str,
        omnigent_build_digest: str,
        integration_mode: str,
        materializer_refs: list[str],
        architecture: str = "linux/amd64",
        requested_host_mode: str = "on-demand",
        requested_host_class_ref: str | None = None,
    ) -> HostClass:
        candidates: list[HostClass] = []
        reasons: list[str] = []
        for template in sorted(self._templates, key=lambda item: item.ref):
            if requested_host_class_ref and template.ref != requested_host_class_ref:
                continue
            if harness.id not in template.harness_ids:
                continue
            try:
                image_ref = _require_image_ref(self._environment, template.image_env)
            except HarnessPlatformError:
                reasons.append(
                    f"{template.ref}: {template.image_env} is not digest-pinned"
                )
                continue
            if integration_mode not in template.integration_modes:
                reasons.append(
                    f"{template.ref}: integration mode {integration_mode} unsupported"
                )
                continue
            if architecture not in template.architectures:
                reasons.append(
                    f"{template.ref}: architecture {architecture} unsupported"
                )
                continue
            unsupported = sorted(
                set(materializer_refs) - set(template.materializer_refs)
            )
            if unsupported:
                reasons.append(
                    f"{template.ref}: materializers unsupported: {unsupported}"
                )
                continue
            if requested_host_mode not in {"on-demand", "on_demand_docker"}:
                reasons.append(
                    f"{template.ref}: host mode {requested_host_mode} unsupported"
                )
                continue
            candidates.append(
                HostClass.model_validate(
                    {
                        "hostClassId": template.host_class_id,
                        "version": template.version,
                        "imageRef": image_ref,
                        "omnigentVersion": omnigent_version,
                        "omnigentBuildDigest": omnigent_build_digest,
                        "architectures": list(template.architectures),
                        "declaredHarnessImplementations": [
                            {
                                "harnessId": harness.id,
                                "implementationRef": harness.implementation.implementation_ref(),
                                "runtimeDependencies": list(
                                    template.runtime_dependencies
                                ),
                            }
                        ],
                        "integrationModes": list(template.integration_modes),
                        "materializerRefs": list(template.materializer_refs),
                        "features": {
                            "git": True,
                            "tmux": True,
                            "bubblewrap": True,
                            "workspaceBind": True,
                            "readOnlyRoot": True,
                            "restrictedEgress": True,
                            "mountedSkills": True,
                            "mountedTools": True,
                        },
                        "runtime": {
                            "uid": 1000,
                            "gid": 1000,
                            "home": "/home/app",
                        },
                    }
                )
            )
        if not candidates:
            detail = "; ".join(reasons) or "no registered template matched"
            raise HarnessPlatformError(
                f"no admissible Host Class for {harness.id}: {detail}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
            )
        if len(candidates) > 1:
            raise HarnessPlatformError(
                f"Host Class selection is ambiguous: {[item.ref for item in candidates]}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
            )
        return candidates[0]


# Available only for isolated contract tests and explicitly registered static
# classes. Production planning uses OmnigentHostClassSelector.
HOST_CLASSES: dict[str, HostClass] = {}


def register_host_class(data: dict[str, Any]) -> HostClass:
    host_class = HostClass.model_validate(data)
    existing = HOST_CLASSES.get(host_class.ref)
    if existing is not None and existing != host_class:
        raise ValueError(
            f"Host Class {host_class.ref} already registered with a different definition"
        )
    HOST_CLASSES[host_class.ref] = host_class
    return host_class


def get_host_class(ref: str) -> HostClass:
    host_class = HOST_CLASSES.get(ref)
    if host_class is None:
        raise HarnessPlatformError(
            f"host class {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    return host_class


def get_opencode_host_class() -> HostClass:
    raise HarnessPlatformError(
        "context-free OpenCode Host Class lookup is unsupported; use OmnigentHostClassSelector",
        code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
    )


def get_pi_host_class() -> HostClass:
    raise HarnessPlatformError(
        "context-free Pi Host Class lookup is unsupported; use OmnigentHostClassSelector",
        code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
    )


class LaunchPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-launch-policy.v2", alias="schemaVersion"
    )
    policyId: str = Field(alias="policyId")
    version: int = Field(ge=1)
    hostMode: Literal[
        "on-demand", "static-connected", "on_demand_docker", "static_compose"
    ] = Field(alias="hostMode")
    hostClassSelector: dict[str, Any] = Field(alias="hostClassSelector")
    isolation: dict[str, Any]
    limits: dict[str, int]
    network: dict[str, Any]
    capture: dict[str, Any]
    cleanup: dict[str, Any]
    controlCapabilities: tuple[str, ...] = Field(alias="controlCapabilities")

    @model_validator(mode="after")
    def validate_top(self) -> "LaunchPolicy":
        if not _SAFE_ID_RE.fullmatch(self.policyId):
            raise ValueError("invalid policyId")
        required_limits = {
            "cpuMillis",
            "memoryMiB",
            "processes",
            "timeoutSeconds",
            "temporaryStorageMiB",
        }
        if set(self.limits) != required_limits:
            raise ValueError(f"limits must contain {required_limits}")
        if any(
            not isinstance(value, int) or value <= 0 for value in self.limits.values()
        ):
            raise ValueError("limits must be positive ints")
        return self

    @property
    def ref(self) -> str:
        return f"{self.policyId}@{self.version}"

    def allows_host_class(self, host_class: HostClass) -> bool:
        return all(
            host_class.features.get(feature)
            for feature in self.hostClassSelector.get("requiredFeatures", [])
        )

    def allows_integration_mode(self, mode: str, host_class: HostClass) -> bool:
        return mode in host_class.integrationModes


LAUNCH_POLICIES: dict[str, LaunchPolicy] = {}


def register_launch_policy(data: dict[str, Any]) -> LaunchPolicy:
    policy = LaunchPolicy.model_validate(data)
    existing = LAUNCH_POLICIES.get(policy.ref)
    if existing is not None and existing != policy:
        raise ValueError(
            f"Launch Policy {policy.ref} already registered with a different definition"
        )
    LAUNCH_POLICIES[policy.ref] = policy
    return policy


def _register_policy(
    policy_id: str,
    host_mode: Literal["on-demand", "static-connected"],
    required_features: list[str],
) -> None:
    register_launch_policy(
        {
            "policyId": policy_id,
            "version": 1,
            "hostMode": host_mode,
            "hostClassSelector": {"requiredFeatures": required_features},
            "isolation": {"runDedicated": host_mode == "on-demand"},
            "limits": {
                "cpuMillis": 2000,
                "memoryMiB": 4096,
                "processes": 256,
                "timeoutSeconds": 5400,
                "temporaryStorageMiB": 256,
            },
            "network": {"egressPolicyRef": "omnigent-restricted-egress@1"},
            "capture": {"required": True, "retentionDays": 30},
            "cleanup": {
                "mode": "remove" if host_mode == "on-demand" else "drain",
                "janitor": True,
            },
            "controlCapabilities": ["interrupt", "terminate", "clear_context"],
        }
    )


_register_policy(
    "omnigent-on-demand",
    "on-demand",
    ["readOnlyRoot", "restrictedEgress", "workspaceBind"],
)
_register_policy(
    "opencode-on-demand",
    "on-demand",
    ["readOnlyRoot", "restrictedEgress", "workspaceBind"],
)
_register_policy("opencode-static", "static-connected", ["workspaceBind"])
_register_policy(
    "codex-on-demand",
    "on-demand",
    ["readOnlyRoot", "restrictedEgress", "workspaceBind"],
)
_register_policy("codex-static", "static-connected", ["workspaceBind"])


def get_launch_policy(ref: str) -> LaunchPolicy:
    policy = LAUNCH_POLICIES.get(ref)
    if policy is None:
        raise HarnessPlatformError(
            f"launch policy {ref} incompatible or unavailable",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    return policy


def validate_policy_for_host_class(
    *,
    policy: LaunchPolicy,
    host_class: HostClass,
    harness_integration_mode: str,
    materializer_refs: list[str],
    workspace_mutation: bool = True,
) -> None:
    del workspace_mutation
    if not policy.allows_host_class(host_class):
        raise HarnessPlatformError(
            f"policy {policy.ref} incompatible with host class {host_class.ref}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    if harness_integration_mode not in host_class.integrationModes:
        raise HarnessPlatformError(
            f"harness integration mode {harness_integration_mode} not in host class",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    unsupported = [
        ref for ref in materializer_refs if not host_class.supports_materializer(ref)
    ]
    if unsupported:
        raise HarnessPlatformError(
            f"materializers {unsupported} not supported by host class {host_class.ref}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
