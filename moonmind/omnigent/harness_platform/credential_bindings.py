"""Versioned credential-binding sets (sections 11, 12).

- Provider Profile remains durable authority (imported, not reimplemented)
- Binding set has stable id + immutable version + digest; plans carry exact ref
- Plan selects Provider Profile+materializer; runtime binding records generation after lease
- Capacity is min across profile, materializer, host, policy, backend
- All provider leases acquired in deterministic order by Provider Profile id, released reverse

MoonLadderStudios/MoonMind#4009 extends the envelope with a closed
ModelAuthorityBinding/RepositoryAuthorityBinding union. Model bindings
preserve their profile/materializer meaning. Repository bindings refer to
admitted source/collaboration/destination access snapshots and the
repository delivery contract. The historical v1 digest algorithm is frozen
byte-for-byte; v2 digest input is domain-separated with authority
discriminators so a new writer can never emit repository authority that an
old reader interprets as model authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BINDING_REF_RE = re.compile(r"^omnigent-credential-bindings:[a-z0-9._-]+@\d+#sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SNAPSHOT_REF_RE = re.compile(r"^repository-access-snapshot:sha256:[0-9a-f]{64}$")

SCHEMA_DOMAIN = "moonmind.omnigent-credential-bindings"
SCHEMA_V1 = "moonmind.omnigent-credential-bindings.v1"
SCHEMA_V2 = "moonmind.omnigent-credential-bindings.v2"
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_V1, SCHEMA_V2)

MODEL_AUTHORITY_KIND = "provider_profile"
REPOSITORY_AUTHORITY_KIND = "repository_connection"

#: Closed repository roles (source, collaboration, destination). A source
#: read grant can never be relabeled as publication authority: role validity
#: is checked per declared slot at plan admission.
REPOSITORY_ROLES = ("source_read", "collaboration", "destination_write")

#: The repository delivery contract owns acquisition/issuance (#4007).
#: Repository bindings never reference model materializers and never enter
#: ProviderProfileManager or the pre-Activity model-capacity ticket.
REPOSITORY_DELIVERY_REFS = ("repository-broker@1",)


class CredentialBinding(BaseModel):
    """Historical v1 model-only binding. Frozen: the v1 decoder and digest
    below verify recorded bytes exactly as originally written."""

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


class ModelAuthorityBinding(BaseModel):
    """v2 model authority: admitted Provider Profile + materializer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorityKind: Literal["provider_profile"] = Field(alias="authorityKind")
    providerProfileRef: str = Field(alias="providerProfileRef")
    materializerRef: str = Field(alias="materializerRef")

    @model_validator(mode="after")
    def validate(self) -> "ModelAuthorityBinding":
        if not self.providerProfileRef.strip():
            raise ValueError("providerProfileRef required")
        if not self.materializerRef.strip() or "@" not in self.materializerRef:
            raise ValueError("materializerRef must be id@version")
        return self


class RepositoryAuthorityBinding(BaseModel):
    """v2 repository authority: admitted access snapshot + delivery contract.

    Carries compact snapshot/policy refs and digests only. Acquired secret
    revision/issuance, use ownership, credential generation, materialized
    paths, and cleanup handles live at the runtime boundary defined by the
    issuance owner (#4007) and never enter this durable object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorityKind: Literal["repository_connection"] = Field(alias="authorityKind")
    connectionRef: str = Field(alias="connectionRef")
    repositoryAccessSnapshotRef: str = Field(alias="repositoryAccessSnapshotRef")
    materializerRef: str = Field(alias="materializerRef")
    repositoryRole: str = Field(alias="repositoryRole")

    @model_validator(mode="after")
    def validate(self) -> "RepositoryAuthorityBinding":
        if not self.connectionRef.strip():
            raise ValueError("connectionRef required")
        if not _SNAPSHOT_REF_RE.fullmatch(self.repositoryAccessSnapshotRef):
            raise ValueError("repositoryAccessSnapshotRef must be a snapshot digest ref")
        if self.materializerRef not in REPOSITORY_DELIVERY_REFS:
            raise ValueError(
                f"materializerRef must be a repository delivery contract {REPOSITORY_DELIVERY_REFS}"
            )
        if self.repositoryRole not in REPOSITORY_ROLES:
            raise ValueError(f"repositoryRole must be one of {REPOSITORY_ROLES}")
        _reject_secret_material(self.model_dump(by_alias=True, mode="json"))
        return self


#: Closed union of admitted binding authorities. Legacy v1 payloads carry
#: the bare model shape without a discriminator and keep their historical
#: interpretation; v2 payloads always carry an explicit authorityKind.
AuthorityBinding = CredentialBinding | ModelAuthorityBinding | RepositoryAuthorityBinding


def _binding_conflict(message: str) -> HarnessPlatformError:
    return HarnessPlatformError(
        message,
        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
    )


def _slot_unbound(message: str) -> HarnessPlatformError:
    return HarnessPlatformError(
        message,
        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
    )


_SECRET_MATERIAL_PATTERNS = ("ghp_", "ghs_", "github_pat_", "sk-", "-----BEGIN", "token=")


def _reject_secret_material(payload: dict[str, Any]) -> None:
    """Fail closed if raw credential material reaches the durable envelope."""
    blob = json.dumps(payload, default=str)
    for pattern in _SECRET_MATERIAL_PATTERNS:
        if pattern in blob:
            raise ValueError(f"binding payload must not carry secret material ({pattern})")


def _normalize_slot_alias(slot: str) -> str:
    return str(slot).strip().lower()


def _check_slot_aliases(bindings: dict[str, Any]) -> None:
    """Reject mixed conflicting aliases before acquisition.

    Two distinct raw slot keys that normalize to the same alias with
    differing payloads would let one declaration shadow another, so the
    set is rejected instead of picking a winner.
    """
    seen: dict[str, str] = {}
    for slot in bindings.keys():
        alias = _normalize_slot_alias(slot)
        canonical = json.dumps(
            bindings[slot].model_dump(by_alias=True, mode="json")
            if isinstance(bindings[slot], BaseModel)
            else dict(bindings[slot]),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if alias in seen and seen[alias] != canonical:
            raise _binding_conflict(
                f"credential binding set contains conflicting aliases for slot {alias!r}"
            )
        seen.setdefault(alias, canonical)


def _dispatch_raw_binding(slot: str, raw: Any) -> AuthorityBinding:
    """Parse one raw binding through the closed union.

    Dicts without ``authorityKind`` keep the historical v1 model meaning.
    Unknown kinds are rejected; they never fall through to model semantics.
    """
    if isinstance(raw, (CredentialBinding, ModelAuthorityBinding, RepositoryAuthorityBinding)):
        return raw
    if not isinstance(raw, dict):
        raise _binding_conflict(f"credential binding for slot {slot!r} must be a mapping")
    kind = raw.get("authorityKind")
    try:
        if kind is None:
            return CredentialBinding.model_validate(raw)
        if kind == MODEL_AUTHORITY_KIND:
            return ModelAuthorityBinding.model_validate(raw)
        if kind == REPOSITORY_AUTHORITY_KIND:
            return RepositoryAuthorityBinding.model_validate(raw)
    except ValueError as exc:
        raise _binding_conflict(
            f"credential binding for slot {slot!r} is invalid: {exc}"
        ) from exc
    raise _binding_conflict(
        f"credential binding for slot {slot!r} has unknown authorityKind {kind!r}"
    )


def _upgrade_legacy_model_binding(binding: AuthorityBinding) -> AuthorityBinding:
    """Normalize a legacy discriminator-free model binding to the v2
    canonical shape. New-write producers use one canonical contract; the
    historical v1 decoder path never calls this."""
    if isinstance(binding, CredentialBinding):
        return ModelAuthorityBinding.model_validate(
            {
                "authorityKind": MODEL_AUTHORITY_KIND,
                **binding.model_dump(by_alias=True, mode="json"),
            }
        )
    return binding


class CredentialBindingSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field("moonmind.omnigent-credential-bindings.v1", alias="schemaVersion")
    bindingSetId: str = Field(alias="bindingSetId")
    version: int = Field(ge=1)
    digest: str
    bindings: dict[str, CredentialBinding | ModelAuthorityBinding | RepositoryAuthorityBinding]

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_v2_bindings(cls, data: Any) -> Any:
        """Keep one canonical v2 contract on every construction path.

        Discriminator-free legacy model shapes inside a v2 set are
        upgraded to ModelAuthorityBinding before validation so direct
        model_validate and create_binding_set digest identically.
        Historical v1 sets never pass through here (schemaVersion-gated).
        """
        if isinstance(data, dict) and data.get("schemaVersion") == SCHEMA_V2:
            raw = data.get("bindings")
            if isinstance(raw, dict):
                upgraded: dict[str, Any] = {}
                for slot, binding in raw.items():
                    if isinstance(binding, CredentialBinding):
                        upgraded[slot] = {
                            "authorityKind": MODEL_AUTHORITY_KIND,
                            **binding.model_dump(by_alias=True, mode="json"),
                        }
                    elif isinstance(binding, dict) and "authorityKind" not in binding:
                        upgraded[slot] = {
                            "authorityKind": MODEL_AUTHORITY_KIND,
                            **binding,
                        }
                    else:
                        upgraded[slot] = binding
                data = {**data, "bindings": upgraded}
        return data

    @model_validator(mode="after")
    def validate_top(self) -> "CredentialBindingSet":
        if self.schemaVersion not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported binding schemaVersion {self.schemaVersion!r}"
            )
        if not _SAFE_ID_RE.fullmatch(self.bindingSetId):
            raise ValueError("invalid bindingSetId")
        if not _DIGEST_RE.fullmatch(self.digest):
            raise ValueError("digest must be sha256")
        if self.schemaVersion == SCHEMA_V1:
            for slot, binding in self.bindings.items():
                if not isinstance(binding, CredentialBinding):
                    raise ValueError(
                        f"v1 binding set must not carry {type(binding).__name__} at slot {slot!r}"
                    )
            expected = compute_binding_set_digest(self.bindingSetId, self.version, self.bindings)
        else:
            expected = compute_binding_set_digest_v2(
                self.bindingSetId, self.version, self.bindings
            )
        if expected != self.digest:
            raise ValueError(f"digest mismatch: expected {expected}")
        return self

    @property
    def ref(self) -> str:
        return f"omnigent-credential-bindings:{self.bindingSetId}@{self.version}#{self.digest}"


def compute_binding_set_digest(bindingSetId: str, version: int, bindings: dict[str, CredentialBinding] | dict[str, Any]) -> str:
    """Historical v1 digest. Frozen byte-for-byte: bindingSetId, version,
    and bindings only. schemaVersion is not part of the v1 digest input."""
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


def compute_binding_set_digest_v2(
    bindingSetId: str,
    version: int,
    bindings: dict[str, AuthorityBinding] | dict[str, Any],
) -> str:
    """v2 digest input: schema domain + version + authority discriminators.

    Domain separation guarantees a v1 digest can never collide with a v2
    digest for the same slot layout, and every binding contributes its
    explicit authorityKind discriminator.
    """
    normalized: dict[str, Any] = {}
    for slot, binding in bindings.items():
        if isinstance(binding, BaseModel):
            normalized[slot] = binding.model_dump(by_alias=True, mode="json")
        else:
            normalized[slot] = dict(binding)
    payload = {
        "schemaDomain": SCHEMA_DOMAIN,
        "schemaVersion": SCHEMA_V2,
        "bindingSetId": bindingSetId,
        "version": version,
        "bindings": normalized,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def decode_historical_v1_binding_set(data: dict[str, Any]) -> CredentialBindingSet:
    """Verify recorded v1 bytes with the original decoder/hash before any
    upgrade or normalization. Rejects non-v1 versions and any binding that
    is not the historical model-only shape."""
    if not isinstance(data, dict):
        raise _binding_conflict("historical binding set must be a mapping")
    if data.get("schemaVersion") != SCHEMA_V1:
        raise _binding_conflict(
            f"historical decoder requires {SCHEMA_V1}, got {data.get('schemaVersion')!r}"
        )
    raw_bindings = data.get("bindings")
    if not isinstance(raw_bindings, dict):
        raise _binding_conflict("historical binding set bindings must be a mapping")
    normalized: dict[str, CredentialBinding] = {}
    for slot, raw in raw_bindings.items():
        if isinstance(raw, dict) and "authorityKind" in raw:
            raise _binding_conflict(
                f"historical v1 slot {slot!r} must not carry authorityKind"
            )
        try:
            normalized[slot] = (
                raw
                if isinstance(raw, CredentialBinding)
                else CredentialBinding.model_validate(raw)
            )
        except ValueError as exc:
            raise _binding_conflict(
                f"historical v1 slot {slot!r} is invalid: {exc}"
            ) from exc
    expected = compute_binding_set_digest(
        str(data.get("bindingSetId")), int(data.get("version", 0)), normalized
    )
    if expected != data.get("digest"):
        raise _binding_conflict(
            f"historical v1 digest mismatch: expected {expected}"
        )
    return CredentialBindingSet.model_validate(
        {
            "schemaVersion": SCHEMA_V1,
            "bindingSetId": data.get("bindingSetId"),
            "version": data.get("version"),
            "digest": data.get("digest"),
            "bindings": {
                slot: binding.model_dump(by_alias=True, mode="json")
                for slot, binding in normalized.items()
            },
        }
    )


def create_binding_set(
    *,
    bindingSetId: str,
    version: int,
    bindings: dict[str, dict[str, Any] | AuthorityBinding],
    schema_version: str = SCHEMA_V1,
) -> CredentialBindingSet:
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise _binding_conflict(
            f"unsupported binding schemaVersion {schema_version!r}"
        )
    _check_slot_aliases(dict(bindings))
    normalized: dict[str, AuthorityBinding] = {}
    for slot, b in bindings.items():
        normalized[slot] = _dispatch_raw_binding(slot, b)
    if schema_version == SCHEMA_V1:
        for slot, binding in normalized.items():
            if not isinstance(binding, CredentialBinding):
                raise _binding_conflict(
                    f"v1 binding set must not carry repository authority at slot {slot!r}; "
                    f"use {SCHEMA_V2}"
                )
        digest = compute_binding_set_digest(bindingSetId, version, normalized)
    else:
        # One canonical v2 contract: discriminator-free legacy model shapes
        # are upgraded to ModelAuthorityBinding before digesting.
        normalized = {
            slot: _upgrade_legacy_model_binding(binding)
            for slot, binding in normalized.items()
        }
        digest = compute_binding_set_digest_v2(bindingSetId, version, normalized)
        _reject_secret_material(
            {slot: b.model_dump(by_alias=True, mode="json") for slot, b in normalized.items()}
        )
    return CredentialBindingSet.model_validate(
        {
            "schemaVersion": schema_version,
            "bindingSetId": bindingSetId,
            "version": version,
            "digest": digest,
            "bindings": {k: v.model_dump(by_alias=True, mode="json") for k, v in normalized.items()},
        }
    )


def create_binding_set_raw(data: dict[str, Any]) -> CredentialBindingSet:
    """Version-dispatched constructor for raw persisted/candidate payloads.

    Rejects unsupported versions, unknown authority kinds, and conflicting
    slot aliases before any acquisition side effect.
    """
    if not isinstance(data, dict):
        raise _binding_conflict("binding set must be a mapping")
    schema_version = data.get("schemaVersion", SCHEMA_V1)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise _binding_conflict(
            f"unsupported binding schemaVersion {schema_version!r}"
        )
    raw_bindings = data.get("bindings")
    if not isinstance(raw_bindings, dict):
        raise _binding_conflict("binding set bindings must be a mapping")
    _check_slot_aliases(raw_bindings)
    return create_binding_set(
        bindingSetId=str(data.get("bindingSetId")),
        version=int(data.get("version", 0)),
        bindings=dict(raw_bindings),
        schema_version=schema_version,
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


def is_model_authority(binding: Any) -> bool:
    """True for bindings that enter ProviderProfileManager and the
    pre-Activity model-capacity ticket (legacy v1 + v2 model)."""
    if isinstance(binding, (CredentialBinding, ModelAuthorityBinding)):
        return True
    if isinstance(binding, RepositoryAuthorityBinding):
        return False
    if isinstance(binding, dict):
        kind = binding.get("authorityKind")
        return kind is None or kind == MODEL_AUTHORITY_KIND
    return hasattr(binding, "providerProfileRef") and not hasattr(
        binding, "repositoryAccessSnapshotRef"
    )


def is_repository_authority(binding: Any) -> bool:
    """True for bindings that use repository issuance (#4007) and never
    inherit model cooldown/OAuth-home exclusivity."""
    if isinstance(binding, RepositoryAuthorityBinding):
        return True
    if isinstance(binding, (CredentialBinding, ModelAuthorityBinding)):
        return False
    if isinstance(binding, dict):
        return binding.get("authorityKind") == REPOSITORY_AUTHORITY_KIND
    return hasattr(binding, "repositoryAccessSnapshotRef")


def model_bindings_of(bindings: dict[str, Any]) -> dict[str, Any]:
    """Project only model-authority entries from a binding mapping.

    Consuming sites (planner validation, support checks, acquisition,
    delivery, attestation, retry, continuation, remediation, fan-out,
    cleanup) use this projection so repository slots can never be routed
    into model lookup or capacity validation.
    """
    return {slot: binding for slot, binding in bindings.items() if is_model_authority(binding)}


def repository_bindings_of(bindings: dict[str, Any]) -> dict[str, Any]:
    """Project only repository-authority entries from a binding mapping."""
    return {
        slot: binding for slot, binding in bindings.items() if is_repository_authority(binding)
    }


def model_authority_bindings(
    binding_set: CredentialBindingSet,
) -> dict[str, CredentialBinding | ModelAuthorityBinding]:
    """Return only model-authority bindings (legacy v1 + v2 model)."""
    return {
        slot: binding
        for slot, binding in binding_set.bindings.items()
        if isinstance(binding, (CredentialBinding, ModelAuthorityBinding))
    }


def repository_authority_bindings(
    binding_set: CredentialBindingSet,
) -> dict[str, RepositoryAuthorityBinding]:
    """Return only repository-authority bindings."""
    return {
        slot: binding
        for slot, binding in binding_set.bindings.items()
        if isinstance(binding, RepositoryAuthorityBinding)
    }


def model_profile_refs(binding_set: CredentialBindingSet) -> list[str]:
    """Provider Profile refs that enter ProviderProfileManager and the
    pre-Activity model-capacity ticket. Repository slots are excluded: one
    model profile plus two repository roles is not a multi-model plan."""
    return sorted(
        {
            binding.providerProfileRef
            for binding in model_authority_bindings(binding_set).values()
        }
    )


def model_materializer_refs(binding_set: CredentialBindingSet) -> list[str]:
    """Materializer refs that participate in model support/combination keys.
    Repository delivery contracts are excluded."""
    return sorted(
        {
            binding.materializerRef
            for binding in model_authority_bindings(binding_set).values()
        }
    )


def validate_binding_set_for_plan(
    *,
    binding_set: CredentialBindingSet,
    required_slots: list[str],
    declared_slots: list[str] | None = None,
    declared_repository_slots: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Validate a binding set against planner declarations.

    Repository slot declarations derive from the admitted
    workspace/tool/Skill/publication requirements, never from
    agent-supplied keys. Each declaration names the authority kind
    (always repository for these slots) plus allowed materializers and
    roles, so a source read grant cannot be relabeled as publication
    authority. Undeclared-slot rejection is preserved.
    """
    for slot in required_slots:
        if slot not in binding_set.bindings:
            raise _slot_unbound(f"credential slot {slot} unbound")
    # Reject bindings for undeclared slots (no extra privileged slots without profile authority)
    if declared_slots is not None:
        repo_declared = set((declared_repository_slots or {}).keys())
        extra = set(binding_set.bindings.keys()) - set(declared_slots) - repo_declared
        if extra:
            raise _slot_unbound(
                f"credential binding set contains undeclared slots: {sorted(extra)}",
            )
    repo_decls = declared_repository_slots or {}
    for slot, decl in repo_decls.items():
        binding = binding_set.bindings.get(slot)
        if binding is None:
            continue
        if not isinstance(binding, RepositoryAuthorityBinding):
            raise _slot_unbound(
                f"credential slot {slot} is declared repository authority but carries model authority"
            )
        allowed_materializers = decl.get("allowedMaterializers")
        if allowed_materializers is not None and (
            binding.materializerRef not in tuple(allowed_materializers)
        ):
            raise _slot_unbound(
                f"credential slot {slot} materializer {binding.materializerRef} not admitted"
            )
        allowed_roles = decl.get("allowedRoles")
        if allowed_roles is not None and (
            binding.repositoryRole not in tuple(allowed_roles)
        ):
            raise _slot_unbound(
                f"credential slot {slot} role {binding.repositoryRole} not admitted; "
                "a source read grant cannot be relabeled as publication authority"
            )
    if declared_slots is not None:
        for slot in declared_slots:
            binding = binding_set.bindings.get(slot)
            if binding is None:
                continue
            if isinstance(binding, RepositoryAuthorityBinding):
                raise _slot_unbound(
                    f"credential slot {slot} is declared model authority but carries repository authority"
                )


def validate_workspace_source_bindings(
    source_kind: str,
    binding_set: CredentialBindingSet,
    *,
    access_snapshot_ref: str | None = None,
) -> None:
    """Enforce workspace-source binding rules at plan admission.

    - ``scratch``: no repository binding.
    - ``anonymous``: explicit permitted access snapshot with no secret slot
      or dummy credential (repository bindings carry issuance, so none are
      admitted; the snapshot travels in the plan's repository refs).
    - ``save_only``: no destination-write role (a save-only task does not
      obtain hypothetical future destination write credentials; a later
      publication request re-admits its destination).
    """
    repo = repository_authority_bindings(binding_set)
    if source_kind == "scratch":
        if repo:
            raise _slot_unbound(
                f"scratch workspace must not carry repository bindings: {sorted(repo)}"
            )
        return
    if source_kind == "anonymous":
        if repo:
            raise _slot_unbound(
                "anonymous repository access carries the permitted access snapshot, "
                "never a secret slot or dummy credential"
            )
        if not access_snapshot_ref or not _SNAPSHOT_REF_RE.fullmatch(access_snapshot_ref):
            raise _slot_unbound(
                "anonymous repository access requires the explicit permitted access snapshot"
            )
        return
    if source_kind == "save_only":
        destination = [
            slot for slot, binding in repo.items()
            if binding.repositoryRole == "destination_write"
        ]
        if destination:
            raise _slot_unbound(
                f"save-only work must not hold destination credentials: {destination}"
            )
        return
    raise _slot_unbound(f"unknown workspace source kind {source_kind!r}")


class ChildRepositoryGrant(BaseModel):
    """Repository authority re-admitted for one child target and attempt.

    Parent-workflow visibility or a shared user owner alone is not
    permission. The child presents a demonstrably attenuated compatible
    snapshot bound to its own target and attempt; raw parent credentials
    and old issuance handles are never copied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    child_target_ref: str = Field(alias="childTargetRef")
    child_attempt_ref: str = Field(alias="childAttemptRef")
    parent_snapshot_ref: str = Field(alias="parentSnapshotRef")
    binding: RepositoryAuthorityBinding = Field(alias="binding")


def attenuate_repository_binding_for_child(
    *,
    parent_binding: RepositoryAuthorityBinding,
    child_target_ref: str,
    child_attempt_ref: str,
    child_snapshot_ref: str | None,
) -> ChildRepositoryGrant:
    """Re-admit parent repository authority for child work.

    Fails closed when the child offers no own snapshot, when it replays
    the parent snapshot verbatim (not attenuation), or when any ref
    smuggles raw credential material. Workspace restore cannot restore an
    authorization grant: callers must supply a freshly admitted snapshot.
    """
    if not (child_target_ref or "").strip():
        raise _binding_conflict("child target ref is required for inherited authority")
    if not (child_attempt_ref or "").strip():
        raise _binding_conflict("child attempt ref is required for inherited authority")
    if not child_snapshot_ref or not _SNAPSHOT_REF_RE.fullmatch(child_snapshot_ref):
        raise _binding_conflict(
            "child work requires its own admitted repository access snapshot; "
            "parent-workflow visibility is not permission"
        )
    if child_snapshot_ref == parent_binding.repositoryAccessSnapshotRef:
        raise _binding_conflict(
            "child snapshot must be demonstrably attenuated from the parent snapshot"
        )
    for ref_value in (child_target_ref, child_attempt_ref, child_snapshot_ref):
        for pattern in _SECRET_MATERIAL_PATTERNS:
            if pattern in str(ref_value):
                raise _binding_conflict(
                    "child repository refs must not carry raw credential material"
                )
    binding = RepositoryAuthorityBinding.model_validate(
        {
            "authorityKind": REPOSITORY_AUTHORITY_KIND,
            "connectionRef": parent_binding.connectionRef,
            "repositoryAccessSnapshotRef": child_snapshot_ref,
            "materializerRef": parent_binding.materializerRef,
            "repositoryRole": parent_binding.repositoryRole,
        }
    )
    return ChildRepositoryGrant.model_validate(
        {
            "childTargetRef": child_target_ref,
            "childAttemptRef": child_attempt_ref,
            "parentSnapshotRef": parent_binding.repositoryAccessSnapshotRef,
            "binding": binding.model_dump(by_alias=True, mode="json"),
        }
    )


def attenuated_child_grants_for(
    *,
    parent_binding_set: CredentialBindingSet,
    child_target_ref: str,
    child_attempt_ref: str,
    child_snapshot_refs: Mapping[str, str],
) -> dict[str, ChildRepositoryGrant]:
    """Compose attenuated child re-admission for every parent repo slot.

    Thin production composition over :func:`attenuate_repository_binding_for_child`
    for fan-out, continuation, remediation, and workspace-restore callers
    (MoonLadderStudios/MoonMind#4009 REQ-07). Each parent repository slot
    requires its own freshly admitted child snapshot; missing slots, verbatim
    parent-snapshot replay, and raw credential material all fail closed.
    Model-authority slots are never copied here: the child plan re-derives
    model authority through its own admission.
    """
    validate_child_snapshot_coverage(
        parent_binding_set=parent_binding_set,
        child_snapshot_refs=child_snapshot_refs,
    )
    grants: dict[str, ChildRepositoryGrant] = {}
    for slot, binding in repository_authority_bindings(parent_binding_set).items():
        child_snapshot = child_snapshot_refs.get(slot) if isinstance(child_snapshot_refs, Mapping) else None
        grants[slot] = attenuate_repository_binding_for_child(
            parent_binding=binding,
            child_target_ref=child_target_ref,
            child_attempt_ref=child_attempt_ref,
            child_snapshot_ref=child_snapshot,
        )
    return grants


def assert_worker_supports_binding_set(
    worker_authority_kinds: tuple[str, ...] | list[str],
    binding_set: CredentialBindingSet,
) -> None:
    """Capability/version admission barrier for new work.

    An incompatible old worker rejects new authority without
    global-credential fallback: a model-only worker refuses any set
    carrying repository authority.
    """
    kinds = {str(kind).strip().lower() for kind in worker_authority_kinds}
    if repository_authority_bindings(binding_set) and "repository" not in kinds:
        raise _binding_conflict(
            "worker supports model authority only and cannot consume repository authority"
        )


def plan_bindings_have_repository_authority(bindings: Any) -> bool:
    """Lightweight repository-authority probe for production call sites.

    Works on raw plan-payload binding mappings without requiring a full
    binding-set rebuild, so legacy/test plan shapes with model-only
    SimpleNamespace values probe as model-only instead of crashing. Any
    positive probe must still go through the versioned constructor before
    a grant or admission decision is made.
    """

    try:
        values = list(dict(bindings or {}).values())
    except Exception:
        return False
    return any(is_repository_authority(binding) for binding in values)


def required_worker_authority_kinds(
    binding_set: CredentialBindingSet,
) -> tuple[str, ...]:
    """Return the worker authority kinds a binding set requires.

    Model-only sets (legacy v1 or v2 model) require ``("model",)``; any set
    carrying repository authority requires ``("model", "repository")``.
    Production worker admission compares the worker's advertised kinds
    against this requirement via :func:`assert_worker_supports_binding_set`
    instead of re-deriving the rule per call site.
    """
    if repository_authority_bindings(binding_set):
        return ("model", "repository")
    return ("model",)


def validate_child_snapshot_coverage(    *,
    parent_binding_set: CredentialBindingSet,
    child_snapshot_refs: Mapping[str, str] | None,
) -> None:
    """Require exact attenuated-snapshot coverage for child inheritance.

    Every parent repository slot needs its own freshly admitted child
    snapshot, and unknown extra child snapshots are rejected so a caller
    cannot smuggle an undeclared grant into the child. Model slots are
    never part of this mapping: the child plan re-derives model authority
    through its own admission.
    """
    parent_slots = set(repository_authority_bindings(parent_binding_set).keys())
    supplied = dict(child_snapshot_refs or {})
    if not parent_slots:
        if supplied:
            raise _binding_conflict(
                f"child snapshots name unknown slots: {sorted(supplied)}"
            )
        return
    missing = sorted(slot for slot in parent_slots if slot not in supplied)
    if missing:
        raise _binding_conflict(
            "child work requires its own admitted repository access snapshot "
            f"for slots {missing}; parent-workflow visibility is not permission "
            f"({REPOSITORY_DECLARATION_OWNER} own declarations, "
            f"{REPOSITORY_ISSUANCE_OWNER} owns issuance)"
        )
    extra = sorted(slot for slot in supplied if slot not in parent_slots)
    if extra:
        raise _binding_conflict(
            f"child snapshots name unknown slots: {extra}"
        )


#: Explicit scope boundary for MoonLadderStudios/MoonMind#4009 plan slice 2.
#:
#: #4009 owns the versioned binding envelope only (closed union, frozen v1
#: digest, domain-separated v2 digest, per-slot kind/materializer/role
#: checks, workspace-source rules, child attenuation helper, worker
#: barrier, ownership-scoped release helper). Declaration production
#: (deriving admitted repository slots from workspace/tool/Skill/
#: publication requirements) and the production lifecycle that consumes
#: those declarations (anonymous-snapshot carriage, child re-admission at
#: fan-out/continuation/remediation, worker gating, partial-acquisition
#: cleanup) belong to the delivery/publication owners (#4011/#1090), and
#: repository issuance/acquisition belongs to #4007. This keeps the
#: envelope fail-closed until those owners supply trusted declarations
#: and issuance; agent-supplied slot keys never create declarations.
REPOSITORY_DECLARATION_OWNER = "delivery:#4011/publication:#1090"
REPOSITORY_ISSUANCE_OWNER = "issuance:#4007"


def derive_repository_slot_requirements(
    *,
    profile_document: Mapping[str, Any] | None = None,
    trusted_repository_declarations: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Derive admitted repository slot declarations for the planner.

    The derivation source is the admitted Agent Profile document (and, once
    supplied by the delivery owner, its trusted repository declarations).
    Binding-set slot keys -- which may carry agent-supplied names -- are
    never inspected here and can never create a declaration.

    Today no admitted profile field carries repository declarations, so the
    fail-closed default is ``{}``: every repository slot is rejected as
    undeclared until #4011/#1090 supply trusted declarations through
    ``trusted_repository_declarations``. Supplied declarations are checked
    against the closed role/materializer sets before they reach the
    planner.
    """
    _ = profile_document
    if not trusted_repository_declarations:
        return {}
    derived: dict[str, dict[str, Any]] = {}
    for slot, decl in dict(trusted_repository_declarations).items():
        slot_name = str(slot or "").strip()
        if not slot_name:
            raise _slot_unbound("repository slot declaration requires a slot name")
        decl_map = dict(decl) if isinstance(decl, Mapping) else {}
        allowed_roles = decl_map.get("allowedRoles")
        if allowed_roles is not None:
            roles = tuple(allowed_roles)
            for role in roles:
                if str(role) not in REPOSITORY_ROLES:
                    raise _slot_unbound(
                        f"credential slot {slot_name} role {role} not admitted"
                    )
        allowed_materializers = decl_map.get("allowedMaterializers")
        if allowed_materializers is not None:
            materializers = tuple(allowed_materializers)
            for materializer in materializers:
                if str(materializer) not in REPOSITORY_DELIVERY_REFS:
                    raise _slot_unbound(
                        f"credential slot {slot_name} materializer {materializer} not admitted"
                    )
        derived[slot_name] = {
            "allowedRoles": tuple(allowed_roles) if allowed_roles is not None else None,
            "allowedMaterializers": (
                tuple(allowed_materializers)
                if allowed_materializers is not None
                else None
            ),
        }
    return derived


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
