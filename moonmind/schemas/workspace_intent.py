"""Versioned, durable workspace-intent contract for normal Workflow submissions.

Every normal MoonMind authoring path (create, edit, rerun/edit-for-rerun,
schedules/recurring, preset-expanded, read-only and mutation-capable jobs)
converges on a single :class:`~moonmind.schemas.agent_runtime_models.AgentExecutionRequest`.
This module defines the one canonical, versioned record those requests compile
to *before* an Omnigent host or Docker runtime is selected or mutated.

The record preserves the exact repository and workspace state the operator
authored while carrying only stable references and product intent. It never
carries a worker-local bind path, a Docker-daemon socket or credential, a
caller-selected volume name, an arbitrary host id, or raw credential bodies:
those are rejected as unsafe input, and the canonical workspace identity is a
typed :class:`~moonmind.schemas.workspace_locator_models.WorkspaceLocator`.

The ``intentDigest`` is computed deterministically over the governing values
(independent of ``createdAt`` and the digest itself) so equivalent authored
requests always produce the same immutable intent; a retry reproduces the same
record rather than authoring conflicting workspace authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.workspace_locator_models import WorkspaceLocator

WORKSPACE_INTENT_SCHEMA_VERSION = "v1"
WORKSPACE_INTENT_PRODUCER_VERSION = "omnigent-workspace-intent@1"

# The host runtime injects GitHub/provider credentials only into process memory
# at launch; the durable intent record never carries a credential body, so the
# only supported policy is in-memory injection resolved at the owning worker.
CREDENTIAL_INJECTION_IN_MEMORY_ONLY = "in_memory_only"

# Substrings that indicate a raw credential body has leaked into a value.
_CREDENTIAL_VALUE_MARKERS: tuple[str, ...] = (
    "bearer ",
    "token=",
    "password=",
    "secret=",
    "-----begin",
)
# Runtime-specific shortcut values that must never substitute for the canonical
# workspace contract (Docker-daemon authority, socket transports).
_UNSAFE_VALUE_MARKERS: tuple[str, ...] = (
    "docker.sock",
    "/var/run/docker",
    "unix://",
    "tcp://docker",
)
# Authoring keys that smuggle worker/Docker-daemon authority instead of the
# typed workspace locator. Compared after normalizing separators away.
_UNSAFE_SHORTCUT_KEYS: frozenset[str] = frozenset(
    {
        "bindsource",
        "bindmount",
        "bind",
        "hostpath",
        "hostbindpath",
        "dockervolume",
        "volume",
        "volumename",
        "dockersocket",
        "dockerhost",
        "dockerdaemon",
        "daemon",
        "hostid",
        "omnigenthostid",
        "sandboxpath",
    }
)

# Nested sections whose values are the sanctioned typed identity or an opaque
# portable-capability payload, not host authority. The shortcut-key scan must
# not descend into them: ``workspaceLocator`` is the canonical typed identity,
# and ``inputs`` carries a selected Skill/tool's portable inputs, where an
# ordinary field named ``volume``/``bind``/``hostId`` grants no runtime authority.
_SHORTCUT_SCAN_SKIP_KEYS: frozenset[str] = frozenset({"workspacelocator", "inputs"})

WORKSPACE_INTENT_UNSAFE_INPUT = "WORKSPACE_INTENT_UNSAFE_INPUT"
WORKSPACE_INTENT_LOCATOR_REQUIRED = "WORKSPACE_INTENT_LOCATOR_REQUIRED"
WORKSPACE_INTENT_DIGEST_MISMATCH = "WORKSPACE_INTENT_DIGEST_MISMATCH"


def _normalized_key(key: Any) -> str:
    return "".join(ch for ch in str(key).strip().lower() if ch.isalnum())


def _string_leaves(value: Any) -> list[str]:
    leaves: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            leaves.extend(_string_leaves(nested))
    elif isinstance(value, (list, tuple)):
        for item in value:
            leaves.extend(_string_leaves(item))
    elif isinstance(value, str):
        leaves.append(value)
    return leaves


def _dedupe_refs(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    seen: dict[str, None] = {}
    for value in values:
        candidate = str(value).strip()
        if candidate:
            seen.setdefault(candidate, None)
    return list(seen)


class WorkspaceIntentAssetProjection(BaseModel):
    """Immutable projection of one selected Skill or executable tool."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    name: str = Field(..., min_length=1, max_length=300)
    version: str | None = Field(None, max_length=300)
    digest: str | None = Field(None, max_length=300)


class WorkspaceIntentRecord(BaseModel):
    """Canonical, versioned, immutable workspace-intent contract for one run.

    The record is frozen and every digest-governing collection is a tuple, so no
    caller can reassign a field or append to a ref/capability/projection list
    after construction. ``intentDigest`` therefore always matches the governing
    values it was finalized over.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["v1"] = Field(
        WORKSPACE_INTENT_SCHEMA_VERSION, alias="schemaVersion"
    )
    producer_version: str = Field(
        WORKSPACE_INTENT_PRODUCER_VERSION, alias="producerVersion", min_length=1
    )
    intent_digest: str | None = Field(None, alias="intentDigest")
    created_at: datetime = Field(..., alias="createdAt")

    # Lineage.
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str | None = Field(None, alias="runId")
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)

    # Repository identity and immutable source evidence.
    repository: str | None = Field(None, alias="repository", max_length=2000)
    repository_kind: str | None = Field(None, alias="repositoryKind", max_length=50)
    checkout_commit: str | None = Field(None, alias="checkoutCommit", max_length=200)

    # Requested branch intent.
    starting_branch: str | None = Field(None, alias="startingBranch", max_length=400)
    target_branch: str | None = Field(None, alias="targetBranch", max_length=400)

    # Attachments and declared input artifact refs.
    input_refs: tuple[str, ...] = Field(default_factory=tuple, alias="inputRefs")
    attachment_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="attachmentRefs"
    )

    # Selected Skill and tool projections with immutable version/digest evidence.
    resolved_skillset_ref: str | None = Field(None, alias="resolvedSkillsetRef")
    skill_projections: tuple[WorkspaceIntentAssetProjection, ...] = Field(
        default_factory=tuple, alias="skillProjections"
    )
    tool_projections: tuple[WorkspaceIntentAssetProjection, ...] = Field(
        default_factory=tuple, alias="toolProjections"
    )

    # Checkpoint / workspace-head / external-state restore refs. Artifact-backed
    # restore inputs and provider-native external-state refs remain distinct.
    restore_input_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="restoreInputRefs"
    )
    external_state_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="externalStateRefs"
    )

    # Repository read-only versus mutation authority.
    repository_mutation: bool = Field(False, alias="repositoryMutation")

    # Publish mode, terminal saved-work policy, and publication destination intent.
    publish_mode: str = Field("none", alias="publishMode", min_length=1, max_length=100)
    saved_work_policy: str | None = Field(None, alias="savedWorkPolicy", max_length=200)
    publication_destination: str | None = Field(
        None, alias="publicationDestination", max_length=400
    )

    # Required GitHub capabilities and credential-injection policy.
    required_capabilities: tuple[str, ...] = Field(
        default_factory=tuple, alias="requiredCapabilities"
    )
    credential_injection_policy: Literal["in_memory_only"] = Field(
        CREDENTIAL_INJECTION_IN_MEMORY_ONLY, alias="credentialInjectionPolicy"
    )

    # The canonical workspace identity. A typed locator, never a caller-authored
    # bind path or volume name.
    workspace_locator: WorkspaceLocator = Field(..., alias="workspaceLocator")

    @field_validator(
        "input_refs",
        "attachment_refs",
        "restore_input_refs",
        "external_state_refs",
        mode="before",
    )
    @classmethod
    def _normalize_ref_lists(cls, value: Any) -> list[str]:
        return _dedupe_refs(value)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _normalize_capabilities(cls, value: Any) -> list[str]:
        # Capabilities are case-insensitive identities; lowercase so equivalent
        # authored casing produces the same immutable intent digest.
        return _dedupe_refs(
            [str(item).strip().lower() for item in _dedupe_refs(value)]
        )

    @model_validator(mode="after")
    def _finalize(self) -> "WorkspaceIntentRecord":
        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=True)
        for leaf in _string_leaves(dumped):
            lowered = leaf.lower()
            if any(marker in lowered for marker in _CREDENTIAL_VALUE_MARKERS):
                raise ValueError(
                    f"{WORKSPACE_INTENT_UNSAFE_INPUT}: workspace intent must not "
                    "carry a raw credential body"
                )
            if any(marker in lowered for marker in _UNSAFE_VALUE_MARKERS):
                raise ValueError(
                    f"{WORKSPACE_INTENT_UNSAFE_INPUT}: workspace intent must not "
                    "carry Docker-daemon authority or socket transports"
                )
        computed = self.compute_digest()
        if self.intent_digest is None:
            # The model is frozen; stamp the finalized digest through the base
            # setter, the one sanctioned mutation during construction.
            object.__setattr__(self, "intent_digest", computed)
        elif self.intent_digest != computed:
            raise ValueError(
                f"{WORKSPACE_INTENT_DIGEST_MISMATCH}: intentDigest does not match "
                "the governing workspace-intent values"
            )
        return self

    def _fingerprint_payload(self) -> dict[str, Any]:
        """Canonical, identity-independent content used to derive the digest.

        ``createdAt`` and ``intentDigest`` are excluded so equivalent authored
        requests — including a retry of the same request — always produce the
        same immutable digest.
        """

        dumped = self.model_dump(by_alias=True, mode="json", exclude_none=False)
        dumped.pop("intentDigest", None)
        dumped.pop("createdAt", None)
        return dumped

    def compute_digest(self) -> str:
        """Return the deterministic ``sha256:`` digest of the governing values."""

        encoded = json.dumps(
            self._fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def workspace_locator_payload(self) -> dict[str, Any]:
        """Return the typed locator as an alias-keyed mapping for host prep."""

        return self.workspace_locator.model_dump(by_alias=True, mode="json")

    def evidence(self) -> dict[str, Any]:
        """Bounded, credential-free, path-safe compilation evidence.

        Suitable for a durable lifecycle event and Workflow Detail. Repository
        identity is exposed only for remote sources; a local source is redacted
        so raw worker-local paths never leak.
        """

        repository_evidence: str | None
        if self.repository_kind == "local":
            repository_evidence = "[local-source]"
        else:
            repository_evidence = self.repository
        return {
            "schemaVersion": self.schema_version,
            "producerVersion": self.producer_version,
            "intentDigest": self.intent_digest,
            "workflowId": self.workflow_id,
            "stepExecutionId": self.step_execution_id,
            "repository": repository_evidence,
            "repositoryKind": self.repository_kind,
            "sourceCommit": self.checkout_commit,
            "startingBranch": self.starting_branch,
            "targetBranch": self.target_branch,
            "publishMode": self.publish_mode,
            "savedWorkPolicy": self.saved_work_policy,
            "publicationDestination": self.publication_destination,
            "repositoryMutation": self.repository_mutation,
            "requiredCapabilities": list(self.required_capabilities),
            "credentialInjectionPolicy": self.credential_injection_policy,
            "locatorKind": self.workspace_locator.kind,
            "resolvedSkillsetRef": self.resolved_skillset_ref,
            "attachmentRefCount": len(self.attachment_refs),
            "inputRefCount": len(self.input_refs),
            "skillProjectionCount": len(self.skill_projections),
            "toolProjectionCount": len(self.tool_projections),
            "restoreInputRefCount": len(self.restore_input_refs),
            "externalStateRefCount": len(self.external_state_refs),
            "skillProjectionDigests": [
                projection.digest
                for projection in self.skill_projections
                if projection.digest
            ],
            "toolProjectionDigests": [
                projection.digest
                for projection in self.tool_projections
                if projection.digest
            ],
        }


def assert_no_runtime_shortcut_keys(payload: Any) -> None:
    """Fail closed if authored input smuggles a worker/Docker-daemon shortcut.

    Raises :class:`ValueError` tagged with :data:`WORKSPACE_INTENT_UNSAFE_INPUT`
    when a caller-authored mapping carries an absolute bind source, a Docker
    socket/volume, an arbitrary host id, or another runtime-specific shortcut
    key that would substitute for the canonical workspace contract.
    """

    if isinstance(payload, dict):
        for key, nested in payload.items():
            normalized = _normalized_key(key)
            if normalized in _UNSAFE_SHORTCUT_KEYS:
                raise ValueError(
                    f"{WORKSPACE_INTENT_UNSAFE_INPUT}: authored workspace input "
                    f"must not carry the runtime-specific key {key!r}"
                )
            # Recurse into authored structure, but never into the sanctioned
            # typed identity or an opaque portable-capability input payload, whose
            # generic keys grant no runtime authority.
            if normalized not in _SHORTCUT_SCAN_SKIP_KEYS:
                assert_no_runtime_shortcut_keys(nested)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            assert_no_runtime_shortcut_keys(item)
