"""Execution plan payload + envelope (sections 16, 4.5, 4.6, 16.2).

Plan records pre-host decisions only. No credential generations, host ids, lease refs, volumes, env.
Plan digest is non-self-referential: payload bytes hashed, ref stored outside payload.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from moonmind.omnigent.harness_platform.support import SupportKeyPayload

from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBinding,
    ModelAuthorityBinding,
    RepositoryAuthorityBinding,
    is_repository_authority,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
)


_MAX_PLAN_PAYLOAD_BYTES = 256 * 1024
_MAX_PLAN_COLLECTION_ITEMS = 256
_MAX_PLAN_NESTING_DEPTH = 16
_MAX_PLAN_STRING_BYTES = 16 * 1024

_FORBIDDEN_PLAN_KEYS = {
    # Post-acquisition and live runtime authority belongs in the runtime binding.
    "credentialgeneration",
    "credentialruntimeref",
    "providerleaseref",
    "hostid",
    "omnigenthostid",
    "hostleaseref",
    "hostleasegeneration",
    "hostbindingref",
    "runtimebindingref",
    "replacementgeneration",
    "cleanupref",
    # Secret bodies and common credential spellings must never enter the plan.
    "apikey",
    "accesskey",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "authorization",
    "bearer",
    "clientsecret",
    "cookie",
    "credentials",
    "password",
    "privatekey",
    "secret",
    "secretbody",
    "sessioncookie",
    "skillbody",
    "token",
    # Mutable host realization belongs in the runtime binding.
    "bindsource",
    "dockersocket",
    "hostpath",
    "mountsource",
    "volumename",
    "workerpath",
    "workspacepath",
    "callerhostid",
}


def _normalized_plan_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _validate_plan_data(obj: Any, *, path: str = "$", depth: int = 0) -> None:
    """Reject live authority, secret bodies, and unbounded embedded values."""

    if depth > _MAX_PLAN_NESTING_DEPTH:
        raise ValueError(
            f"plan payload exceeds maximum nesting depth at {path}"
        )
    if isinstance(obj, dict):
        if len(obj) > _MAX_PLAN_COLLECTION_ITEMS:
            raise ValueError(f"plan payload mapping is too large at {path}")
        for key, value in obj.items():
            normalized_key = _normalized_plan_key(key)
            if normalized_key in _FORBIDDEN_PLAN_KEYS:
                raise ValueError(
                    f"plan payload must not contain {key} at {path}"
                )
            _validate_plan_data(value, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        if len(obj) > _MAX_PLAN_COLLECTION_ITEMS:
            raise ValueError(f"plan payload collection is too large at {path}")
        for index, value in enumerate(obj):
            _validate_plan_data(value, path=f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(obj, str) and len(obj.encode("utf-8")) > _MAX_PLAN_STRING_BYTES:
        raise ValueError(f"plan payload string is too large at {path}")


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    qualifiedId: str | None = Field(default=None, alias="qualifiedId")
    effort: str | None = None
    routeRef: str | None = Field(default=None, alias="routeRef")
    normalizedOptions: dict[str, Any] = Field(
        default_factory=dict, alias="normalizedOptions"
    )
    modelConfigDigest: str = Field(alias="modelConfigDigest")


class ExecutionAuthority(BaseModel):
    """Immutable product-boundary inputs that the plan was compiled from.

    Bodies deliberately remain in the artifact system.  The plan carries only
    opaque references and content digests so Temporal admission can prove that
    retries, continuations, remediation, and checkpoint branches are using the
    same authored authority without copying instructions into workflow history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authoredRequestRef: str = Field(alias="authoredRequestRef")
    authoredRequestDigest: str = Field(alias="authoredRequestDigest")
    taskInputSnapshotRef: str = Field(alias="taskInputSnapshotRef")
    taskInputSnapshotDigest: str = Field(alias="taskInputSnapshotDigest")
    repositoryIntentRef: str = Field(alias="repositoryIntentRef")
    continuationPolicyRef: str = Field(alias="continuationPolicyRef")
    remediationPolicyRef: str = Field(alias="remediationPolicyRef")
    checkpointPolicyRef: str = Field(alias="checkpointPolicyRef")
    publicationPolicyRef: str = Field(alias="publicationPolicyRef")
    timingPolicyRef: str = Field(alias="timingPolicyRef")
    failurePolicyRef: str = Field(alias="failurePolicyRef")


class AdmissionAuthority(BaseModel):
    """Immutable evidence required before a new session may be admitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # MoonLadderStudios/MoonMind#4560: ordinary admission is
    # certificate-independent; strict admission requires certification.
    # The trusted admission/settings boundary chooses the effective mode --
    # workflow-authored input can never downgrade strict or forge admission.
    # Plans persisted before this field existed always carried certified
    # evidence, so the default preserves their in-flight strict
    # interpretation: missing metadata never silently bypasses validation.
    admissionMode: Literal["ordinary", "strict"] = Field(
        default="strict", alias="admissionMode"
    )
    supportEvidenceRef: str = Field(default="", alias="supportEvidenceRef")
    supportEvidenceDigest: str = Field(default="", alias="supportEvidenceDigest")
    # Which evidence tier backs admission. Plans persisted before this field
    # existed always carried protected-tier evidence, so the default preserves
    # their in-flight interpretation. ``uncertified`` names explicitly
    # certificate-independent ordinary admission -- never a claim that
    # execution has already succeeded.
    supportTier: Literal["supported", "deployment_qualified", "uncertified"] = Field(
        default="supported", alias="supportTier"
    )
    featureGeneration: str = Field(alias="featureGeneration")
    replayCompatibilityVersion: str = Field(alias="replayCompatibilityVersion")
    rollbackPolicyVersion: str = Field(alias="rollbackPolicyVersion")

    @model_validator(mode="after")
    def validate_authority(self) -> "AdmissionAuthority":
        if self.admissionMode == "strict":
            if not self.supportEvidenceRef.startswith("artifact:"):
                raise ValueError("supportEvidenceRef must be artifact-backed")
            if not self.supportEvidenceDigest.startswith("sha256:"):
                raise ValueError("supportEvidenceDigest must be a sha256 digest")
            if self.supportTier == "uncertified":
                raise ValueError("strict admission requires certified evidence")
        else:
            # Ordinary admission: uncertified execution carries empty refs and
            # the uncertified tier truthfully. When an optional certificate is
            # present as a truthful observation it must still be well-formed,
            # but its absence never vetoes ordinary execution.
            if not self.supportEvidenceRef and not self.supportEvidenceDigest:
                if self.supportTier != "uncertified":
                    raise ValueError(
                        "uncertified ordinary admission must use the uncertified tier"
                    )
            else:
                if not self.supportEvidenceRef.startswith("artifact:"):
                    raise ValueError("supportEvidenceRef must be artifact-backed")
                if not self.supportEvidenceDigest.startswith("sha256:"):
                    raise ValueError("supportEvidenceDigest must be a sha256 digest")
                if self.supportTier == "uncertified" and self.supportEvidenceRef:
                    raise ValueError(
                        "uncertified tier must not carry an evidence reference"
                    )
        for field_name in (
            "featureGeneration",
            "replayCompatibilityVersion",
            "rollbackPolicyVersion",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        return self

    @model_serializer(mode="wrap")
    def serialize_authority(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Keep strict plans wire-identical to the pre-#4560 authority shape.

        Retained readers predate ``admissionMode`` and reject unknown fields
        via ``extra="forbid"``. A strict plan already carries the historical
        meaning (certified refs + a certified tier), so omitting the default
        ``"strict"`` marker keeps it parseable by the retained fleet during
        a rolling upgrade. Ordinary plans keep the marker: retained readers
        fail closed on the new shape instead of misreading uncertified
        admission as certified.
        """

        payload = handler(self)
        if self.admissionMode == "strict":
            payload.pop("admissionMode", None)
        return payload


class RuntimeProviderRolloutRecord(BaseModel):
    """Frozen runtime-provider rollout authority for one admitted plan.

    Source: MoonLadderStudios/MoonMind#3833. Changing the live rollout policy
    after admission can never reinterpret this execution: the plan carries the
    exact combination key, the rule generation, and the policy generation that
    admitted it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policyVersion: str = Field(alias="policyVersion")
    policyGeneration: int = Field(alias="policyGeneration", ge=0)
    combinationKey: str = Field(alias="combinationKey")
    targetId: str = Field(alias="targetId")
    pathClass: str = Field(alias="pathClass")
    state: str
    ruleGeneration: int = Field(alias="ruleGeneration", ge=0)
    reasonCode: str = Field(alias="reasonCode")

    @model_validator(mode="after")
    def validate_record(self) -> "RuntimeProviderRolloutRecord":
        if not self.combinationKey.startswith(
            "omnigent-runtime-provider-combination:sha256:"
        ):
            raise ValueError("combinationKey must be a combination digest")
        for field_name in (
            "policyVersion",
            "targetId",
            "pathClass",
            "state",
            "reasonCode",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        return self


class OmnigentExecutionPlanPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-execution-plan-payload.v1", alias="schemaVersion"
    )
    authority: ExecutionAuthority | None = None
    admissionAuthority: AdmissionAuthority | None = Field(
        default=None, alias="admissionAuthority"
    )
    endpointRef: str = Field(alias="endpointRef")
    agentProfileSnapshotRef: str = Field(alias="agentProfileSnapshotRef")
    harnessCatalogRef: str = Field(alias="harnessCatalogRef")
    harnessId: str = Field(alias="harnessId")
    harnessImplementationRef: str = Field(alias="harnessImplementationRef")
    agentSource: dict[str, Any] = Field(alias="agentSource")
    credentialBindingSetRef: str = Field(alias="credentialBindingSetRef")
    # Union payload (MoonLadderStudios/MoonMind#4009): legacy v1 plans carry
    # model-only CredentialBinding entries; plans admitting repository
    # authority carry v2 entries with explicit authorityKind discriminators.
    # Old v1-only readers reject union entries via extra="forbid" instead of
    # misinterpreting repository authority as model authority.
    credentialBindings: dict[
        str, CredentialBinding | ModelAuthorityBinding | RepositoryAuthorityBinding
    ] = Field(alias="credentialBindings")
    # Compact validated repository snapshot refs (slot -> access snapshot).
    # Pre-host decisions only: acquired secret revision/issuance, use
    # ownership, generations, materialized paths, and cleanup handles live
    # at the runtime boundary, never here. Omitted when no repository
    # authority is admitted so historical v1 bytes keep their digest.
    repositoryAuthorityRefs: dict[str, str] | None = Field(
        default=None, alias="repositoryAuthorityRefs"
    )
    hostClassRef: str = Field(alias="hostClassRef")
    hostImageRef: str | None = Field(default=None, alias="hostImageRef")
    omnigentHostBuildDigest: str | None = Field(
        default=None, alias="omnigentHostBuildDigest"
    )
    hostArchitecture: str | None = Field(default=None, alias="hostArchitecture")
    # Read persisted plans written with this field, preserving their digest.
    # New writers use harnessCatalogRef for version evidence so v1 readers do
    # not need an additional payload field (including a null default).
    omnigentVersion: str | None = Field(default=None, alias="omnigentVersion")
    launchPolicyRef: str = Field(alias="launchPolicyRef")
    executionRealizerRef: str = Field(alias="executionRealizerRef")
    modelConfig: ModelConfig = Field(alias="model")
    resolvedSkills: dict[str, Any] = Field(alias="resolvedSkills")
    resolvedTools: dict[str, Any] = Field(default_factory=dict, alias="resolvedTools")
    classAdmissionDecision: dict[str, Any] = Field(alias="classAdmissionDecision")
    runtimeValidationRequirements: tuple[str, ...] = Field(
        alias="runtimeValidationRequirements"
    )
    workspaceIntentRef: str = Field(alias="workspaceIntentRef")
    workspaceMutation: str = Field("allowed", alias="workspaceMutation")
    capturePolicyRef: str | None = Field(default=None, alias="capturePolicyRef")
    capturePolicy: dict[str, Any] = Field(default_factory=dict, alias="capturePolicy")
    policySnapshotRef: str = Field(alias="policySnapshotRef")
    policySnapshotDigest: str | None = Field(
        default=None, alias="policySnapshotDigest"
    )
    effectiveLaunchSnapshotRef: str | None = Field(
        default=None, alias="effectiveLaunchSnapshotRef"
    )
    effectiveLaunchSnapshotDigest: str | None = Field(
        default=None, alias="effectiveLaunchSnapshotDigest"
    )
    supportCombinationKey: str = Field(alias="supportCombinationKey")
    # Added as optional for replay compatibility with execution plans admitted
    # before MoonLadderStudios/MoonMind#3701 recorded the complete support
    # identity. New admissions always populate it.
    supportIdentity: SupportKeyPayload | None = Field(
        default=None, alias="supportIdentity"
    )
    # Optional for replay compatibility with plans admitted before
    # MoonLadderStudios/MoonMind#3833 froze the rollout decision. New
    # admissions always populate it.
    runtimeProviderRollout: RuntimeProviderRolloutRecord | None = Field(
        default=None, alias="runtimeProviderRollout"
    )

    @model_serializer(mode="wrap")
    def serialize_payload(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        if self.omnigentVersion is None:
            payload.pop("omnigentVersion", None)
        if self.repositoryAuthorityRefs is None:
            payload.pop("repositoryAuthorityRefs", None)
        return payload

    @model_validator(mode="after")
    def validate_no_forbidden(self) -> "OmnigentExecutionPlanPayload":
        if self.omnigentVersion is not None:
            from moonmind.omnigent.compatibility import compatibility_series

            if compatibility_series(self.omnigentVersion) is None:
                raise ValueError("omnigentVersion must identify a release series")
        if self.supportIdentity is not None:
            if (
                compute_support_combination_key(self.supportIdentity)
                != self.supportCombinationKey
            ):
                raise ValueError(
                    "supportCombinationKey does not match supportIdentity"
                )
            pinned = self.supportIdentity
            if (
                pinned.harnessImplementationRef != self.harnessImplementationRef
                or pinned.hostClassRef != self.hostClassRef
                or pinned.launchPolicyRef != self.launchPolicyRef
                or pinned.executionRealizerRef != self.executionRealizerRef
                or pinned.modelConfigDigest != self.modelConfig.modelConfigDigest
            ):
                raise ValueError(
                    "supportIdentity differs from admitted execution authority"
                )
        if self.workspaceMutation not in {
            "allowed",
            "read_only",
            "checkpoint_branch",
        }:
            raise ValueError("workspaceMutation is unsupported")
        if self.repositoryAuthorityRefs is not None:
            import re as _re

            _snapshot_re = _re.compile(
                r"^repository-access-snapshot:sha256:[0-9a-f]{64}$"
            )
            for slot, snapshot_ref in self.repositoryAuthorityRefs.items():
                binding = self.credentialBindings.get(slot)
                if binding is None or not is_repository_authority(binding):
                    raise ValueError(
                        f"repositoryAuthorityRefs slot {slot!r} is not an admitted "
                        "repository-authority binding"
                    )
                if not _snapshot_re.fullmatch(snapshot_ref):
                    raise ValueError(
                        f"repositoryAuthorityRefs slot {slot!r} must be a snapshot digest ref"
                    )
                admitted = getattr(binding, "repositoryAccessSnapshotRef", None)
                if admitted is not None and admitted != snapshot_ref:
                    raise ValueError(
                        f"repositoryAuthorityRefs slot {slot!r} conflicts with the "
                        "admitted binding snapshot"
                    )
        exact_launch_authority = (
            self.hostImageRef,
            self.omnigentHostBuildDigest,
            self.hostArchitecture,
            self.policySnapshotDigest,
            self.effectiveLaunchSnapshotRef,
            self.effectiveLaunchSnapshotDigest,
        )
        if any(value is not None for value in exact_launch_authority) and not all(
            value is not None for value in exact_launch_authority
        ):
            raise ValueError(
                "exact launch authority must be recorded atomically"
            )
        payload = self.model_dump(by_alias=True, mode="json")
        _validate_plan_data(payload)
        if len(canonical_payload_bytes(payload)) > _MAX_PLAN_PAYLOAD_BYTES:
            raise ValueError("plan payload exceeds maximum canonical size")
        return self


def canonical_payload_bytes(
    payload: OmnigentExecutionPlanPayload | dict[str, Any]
) -> bytes:
    if isinstance(payload, OmnigentExecutionPlanPayload):
        data = payload.model_dump(by_alias=True, mode="json")
    else:
        data = dict(payload)
    # Ensure no envelope fields inside payload
    data.pop("planRef", None)
    # These exact launch-authority fields were added to the v1 payload after
    # plans had already been persisted.  Missing values retain the historical
    # canonical representation so in-flight plans continue to verify; every
    # newly compiled plan supplies all of them.
    for optional_v1_field in (
        "hostImageRef",
        "omnigentHostBuildDigest",
        "hostArchitecture",
        "omnigentVersion",
        "policySnapshotDigest",
        "effectiveLaunchSnapshotRef",
        "effectiveLaunchSnapshotDigest",
        "admissionAuthority",
        "supportIdentity",
        "runtimeProviderRollout",
        "repositoryAuthorityRefs",
    ):
        if data.get(optional_v1_field) is None:
            data.pop(optional_v1_field, None)
    # Normalize: sorted keys, no whitespace, utf-8, normalized enums/null
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def compute_plan_ref(payload: OmnigentExecutionPlanPayload | dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()
    return f"omnigent-execution-plan:sha256:{digest}"


def compute_model_config_digest(
    *,
    qualifiedId: str | None,
    effort: str | None,
    routeRef: str | None,
    normalizedOptions: dict[str, Any],
) -> str:
    payload = {
        "qualifiedId": qualifiedId,
        "effort": effort,
        "routeRef": routeRef,
        "normalizedOptions": normalizedOptions,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class OmnigentExecutionPlanEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-execution-plan-envelope.v1", alias="schemaVersion"
    )
    planRef: str = Field(alias="planRef")
    payload: OmnigentExecutionPlanPayload

    @model_validator(mode="after")
    def validate_digest(self) -> "OmnigentExecutionPlanEnvelope":
        expected = compute_plan_ref(self.payload)
        if self.planRef != expected:
            raise ValueError(f"planRef digest mismatch: {self.planRef} != {expected}")
        return self


def create_execution_plan_envelope(
    payload: OmnigentExecutionPlanPayload | dict[str, Any]
) -> OmnigentExecutionPlanEnvelope:
    if isinstance(payload, dict):
        # Validate payload first
        parsed = OmnigentExecutionPlanPayload.model_validate(payload)
    else:
        parsed = payload
    ref = compute_plan_ref(parsed)
    return OmnigentExecutionPlanEnvelope.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-envelope.v1",
            "planRef": ref,
            "payload": parsed.model_dump(by_alias=True, mode="json"),
        }
    )


def bind_runtime_request_authority(
    envelope: OmnigentExecutionPlanEnvelope,
    *,
    resolved_skillset_ref: str | None,
    model: Any = None,
    effort: Any = None,
) -> OmnigentExecutionPlanEnvelope:
    """Bind late-resolved step authority before any runtime side effect.

    Skill snapshots are resolved by the deterministic Run workflow after API
    admission, and a step may author a model override.  Both decisions must be
    incorporated into a new immutable plan before the Activity acquires a
    Provider Profile lease or launches a host.
    """

    payload = envelope.payload.model_dump(by_alias=True, mode="json")
    changed = False

    requested_skill_ref = str(resolved_skillset_ref or "").strip() or None
    admitted_skills = dict(payload.get("resolvedSkills") or {})
    admitted_skill_ref = str(
        admitted_skills.get("resolvedSkillSetRef") or ""
    ).strip() or None
    if admitted_skill_ref and requested_skill_ref != admitted_skill_ref:
        raise HarnessPlatformError(
            "runtime request Skill snapshot differs from admitted authority",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
    if requested_skill_ref and admitted_skill_ref is None:
        skill_digest = "sha256:" + hashlib.sha256(
            requested_skill_ref.encode("utf-8")
        ).hexdigest()
        delivery_digest = hashlib.sha256(
            json.dumps(
                {
                    "resolvedSkillSetRef": requested_skill_ref,
                    "resolvedSkillSetDigest": skill_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        payload["resolvedSkills"] = {
            **admitted_skills,
            "resolvedSkillSetRef": requested_skill_ref,
            "resolvedSkillSetDigest": skill_digest,
            "skillDeliveryRef": f"skill-delivery:sha256:{delivery_digest}",
        }
        changed = True

    model_config = dict(payload["model"])
    requested_model = str(model or "").strip() or None
    requested_effort = str(effort or "").strip() or None
    effective_model = requested_model or model_config.get("qualifiedId")
    effective_effort = (
        requested_effort if effort is not None else model_config.get("effort")
    )
    if not effective_model:
        raise HarnessPlatformError(
            "an explicit model is required before Omnigent host acquisition",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    if (
        effective_model != model_config.get("qualifiedId")
        or effective_effort != model_config.get("effort")
    ):
        model_digest = compute_model_config_digest(
            qualifiedId=effective_model,
            effort=effective_effort,
            routeRef=model_config.get("routeRef"),
            normalizedOptions=dict(model_config.get("normalizedOptions") or {}),
        )
        payload["model"] = {
            **model_config,
            "qualifiedId": effective_model,
            "effort": effective_effort,
            "modelConfigDigest": model_digest,
        }
        support_identity = payload.get("supportIdentity")
        if not isinstance(support_identity, dict):
            raise HarnessPlatformError(
                "step model override requires complete support identity",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        updated_identity = {**support_identity, "modelConfigDigest": model_digest}
        parsed_identity = SupportKeyPayload.model_validate(updated_identity)
        payload["supportIdentity"] = parsed_identity.model_dump(
            by_alias=True, mode="json"
        )
        payload["supportCombinationKey"] = compute_support_combination_key(
            parsed_identity
        )
        changed = True

    return create_execution_plan_envelope(payload) if changed else envelope


def verify_execution_plan_envelope(
    envelope: dict[str, Any] | OmnigentExecutionPlanEnvelope,
) -> OmnigentExecutionPlanEnvelope:
    if isinstance(envelope, OmnigentExecutionPlanEnvelope):
        return envelope
    # Verify without mutation: canonicalize only payload and compare
    parsed = OmnigentExecutionPlanEnvelope.model_validate(envelope)
    return parsed


def execution_support_identity(
    envelope: OmnigentExecutionPlanEnvelope,
) -> dict[str, Any]:
    """Project exact, secret-free support evidence from admitted authority."""

    identity = envelope.payload.supportIdentity
    if identity is None:
        # Replay-visible plans admitted before the complete identity was
        # embedded remain readable, but cannot be mistaken for current
        # combination-qualified acceptance evidence.
        return {
            "supportCombinationKey": envelope.payload.supportCombinationKey,
            "identityComplete": False,
        }
    return {
        **identity.model_dump(by_alias=True, mode="json"),
        "supportCombinationKey": envelope.payload.supportCombinationKey,
        "identityComplete": True,
    }


def forbidden_plan_check(payload: dict[str, Any]) -> None:
    try:
        _validate_plan_data(payload)
        if len(canonical_payload_bytes(payload)) > _MAX_PLAN_PAYLOAD_BYTES:
            raise ValueError("plan payload exceeds maximum canonical size")
    except ValueError as exc:
        raise HarnessPlatformError(
            str(exc),
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        ) from exc


def reissue_ordinary_admission_for_saved_plan(
    payload: dict[str, Any] | OmnigentExecutionPlanPayload,
) -> OmnigentExecutionPlanEnvelope:
    """Reissue fresh ordinary admission for a pre-upgrade saved plan.

    MoonLadderStudios/MoonMind#4560 (R7): the next ordinary attempt of a
    pre-upgrade recurring schedule or saved plan must preserve schedule
    intent -- schedule ID, cadence, timezone, paused state, input, Profile
    and account selection, model, budgets, publication intent -- while
    obtaining fresh ordinary admission without the obsolete historical
    certificate. Every intent field is carried over byte-identically; only
    ``admissionAuthority`` is replaced with fresh uncertified ordinary
    authority at the current generations. The input is never mutated, so
    historical digests (the saved planRef, evidence refs) stay unchanged;
    the caller keeps the old envelope as history and persists the returned
    envelope as the new attempt.

    The trusted settings boundary owns the mode: under explicit strict
    certification this refuses instead of silently downgrading. Strict
    saved plans keep their consumer -- re-admission through the strict
    path with fresh evidence -- and that path is their exit condition.
    """

    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.omnigent.settings import omnigent_requires_certification
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    if omnigent_requires_certification():
        raise ValueError(
            "saved-plan ordinary reissue is unavailable under explicit strict "
            "certification: re-admit through the strict path with fresh evidence"
        )
    data = (
        payload.model_dump(by_alias=True, mode="json")
        if isinstance(payload, OmnigentExecutionPlanPayload)
        else copy.deepcopy(dict(payload))
    )
    data["admissionAuthority"] = AdmissionAuthority(
        admissionMode="ordinary",
        supportEvidenceRef="",
        supportEvidenceDigest="",
        supportTier="uncertified",
        featureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
        replayCompatibilityVersion=OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
    ).model_dump(by_alias=True, mode="json")
    return create_execution_plan_envelope(data)
