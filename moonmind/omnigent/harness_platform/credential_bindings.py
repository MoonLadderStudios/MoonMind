"""Versioned credential-binding sets (sections 11, 12).

- Provider Profile remains durable authority (imported, not reimplemented)
- Binding set has stable id + immutable version + digest; plans carry exact ref
- Plan selects Provider Profile+materializer; runtime binding records generation after lease
- Capacity is min across profile, materializer, host, policy, backend
- All provider leases acquired in deterministic order by Provider Profile id, released reverse
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, UTC
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BINDING_REF_RE = re.compile(r"^omnigent-credential-bindings:[a-z0-9._-]+@\d+#sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class CredentialBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    providerProfileRef: str = Field(alias="providerProfileRef")
    materializerRef: str = Field(alias="materializerRef")

    @model_validator(mode="after")
    def validate(self) -> "CredentialBinding":
        if not self.providerProfileRef.strip():
            raise ValueError("providerProfileRef required")
        if not self.materializerRef.strip() or "@" not in self.materializerRef:
            raise ValueError("materializerRef must be id@version")
        return self


class CredentialBindingSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-credential-bindings.v1", alias="schemaVersion")
    bindingSetId: str = Field(alias="bindingSetId")
    version: int = Field(ge=1)
    digest: str
    bindings: dict[str, CredentialBinding]

    @model_validator(mode="after")
    def validate_top(self) -> "CredentialBindingSet":
        if not _SAFE_ID_RE.fullmatch(self.bindingSetId):
            raise ValueError("invalid bindingSetId")
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError("digest must be sha256")
        expected = compute_binding_set_digest(self.bindingSetId, self.version, self.bindings)
        if expected != self.digest:
            raise ValueError(f"digest mismatch: expected {expected}")
        return self

    @property
    def ref(self) -> str:
        return f"omnigent-credential-bindings:{self.bindingSetId}@{self.version}#{self.digest}"


def compute_binding_set_digest(bindingSetId: str, version: int, bindings: dict[str, CredentialBinding] | dict[str, Any]) -> str:
    normalized: dict[str, Any] = {}
    for slot, binding in bindings.items():
        if isinstance(binding, CredentialBinding):
            normalized[slot] = binding.model_dump(by_alias=True, mode="json")
        else:
            normalized[slot] = dict(binding)
    payload = {
        "bindingSetId": bindingSetId,
        "version": version,
        "bindings": normalized,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def create_binding_set(
    *,
    bindingSetId: str,
    version: int,
    bindings: dict[str, dict[str, str] | CredentialBinding],
) -> CredentialBindingSet:
    normalized = {}
    for slot, b in bindings.items():
        if isinstance(b, CredentialBinding):
            normalized[slot] = b
        else:
            normalized[slot] = CredentialBinding.model_validate(b)
    digest = compute_binding_set_digest(bindingSetId, version, normalized)
    return CredentialBindingSet.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-credential-bindings.v1",
            "bindingSetId": bindingSetId,
            "version": version,
            "digest": digest,
            "bindings": {k: v.model_dump(by_alias=True, mode="json") for k, v in normalized.items()},
        }
    )


def parse_binding_set_ref(ref: str) -> tuple[str, int, str]:
    if not _BINDING_REF_RE.fullmatch(ref):
        raise HarnessPlatformError(
            f"invalid binding set ref: {ref}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
        )
    # omnigent-credential-bindings:<id>@<version>#<digest>
    without_prefix = ref[len("omnigent-credential-bindings:") :]
    id_part, rest = without_prefix.split("@", 1)
    version_str, digest = rest.split("#", 1)
    return id_part, int(version_str), digest


def validate_binding_set_for_plan(
    *,
    binding_set: CredentialBindingSet,
    required_slots: list[str],
) -> None:
    for slot in required_slots:
        if slot not in binding_set.bindings:
            raise HarnessPlatformError(
                f"credential slot {slot} unbound",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
            )


# Generation ownership helpers
def assert_generation_fencing(
    *,
    planned_binding_set_ref: str,
    acquired_generation: int,
    recorded_generation: int | None,
    provider_profile_ref: str,
) -> None:
    """Enforce sticky generation after runtime binding exists."""
    if recorded_generation is None:
        # First lease acquisition: any generation is allowed (plan didn't pin one)
        return
    if acquired_generation != recorded_generation:
        raise HarnessPlatformError(
            f"credential generation fenced: acquired {acquired_generation} != recorded {recorded_generation} for {provider_profile_ref}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )


def effective_capacity(
    *,
    provider_capacity: int,
    materializer_capacity: int,
    host_capacity: int,
    policy_capacity: int,
    backend_capacity: int,
) -> int:
    return min(provider_capacity, materializer_capacity, host_capacity, policy_capacity, backend_capacity)


def deterministic_lease_order(provider_profile_refs: list[str]) -> list[str]:
    return sorted(provider_profile_refs)
