"""Versioned runtime-provider rollout policy for the Omnigent product migration.

Source issue: MoonLadderStudios/MoonMind#3833.
Design source: ``docs/Omnigent/PrimaryRuntimeProviderStrategy.md`` sections 9-11
and ``docs/Omnigent/RuntimeProviderRollout.md``.

This module owns **one** deployment-owned answer to the question "may this exact
runtime-provider combination be a default for new work, an explicit-only choice,
a labeled compatibility path, or nothing at all?".

Three rules make the policy safe to change at runtime:

* **Exact dimensions only.** A rule matches by field equality over the declared
  compatibility dimensions (harness implementation, Agent Profile compatibility
  class, Provider Profile runtime and provider class, Host Class, runtime pack,
  credential materializer, launch policy, host mode, architecture, model
  configuration class, and execution realizer). There is no display-name,
  substring, or prefix routing anywhere in the resolver.
* **Frozen into admitted authority.** :func:`freeze_rollout_record` produces the
  compact record that the execution plan persists. Changing the live policy
  afterwards can never reinterpret an admitted plan or a Temporal history.
* **Never a silent fallback.** A denied or unavailable target produces an
  explicit reason code. It never substitutes another harness, realizer, host
  mode, Provider Profile, or model.

A combination that no rule matches resolves to :data:`RolloutState.explicit_only`
with reason ``combination_not_registered``: a missing support row leaves the
relevant path explicit rather than promoting it, which is the fail-closed
direction for a *default-selection* policy.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION = (
    "moonmind.omnigent-runtime-provider-rollout/v1"
)

#: Deployment-owned JSON override for the built-in policy.
RUNTIME_PROVIDER_ROLLOUT_ENV = "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT"
#: Deployment-owned comma-separated rollback control list.
RUNTIME_PROVIDER_ROLLBACK_ENV = "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK"
#: Deployment-owned comma-separated canary cohort membership for this deployment.
RUNTIME_PROVIDER_CANARY_COHORTS_ENV = (
    "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_CANARY_COHORTS"
)

#: Explicit "this dimension is not pinned by this rule" selector value.
ANY_DIMENSION = "*"
#: Explicit "this dimension does not exist for this path" combination value.
NOT_APPLICABLE = "not-applicable"


class RolloutState(StrEnum):
    """Per-combination rollout state.

    Ordered from least to most promoted so ranking is a property of the
    vocabulary rather than a scattered comparison table.
    """

    disabled = "disabled"
    retired_for_new_work = "retired_for_new_work"
    direct_compatibility_only = "direct_compatibility_only"
    explicit_only = "explicit_only"
    canary = "canary"
    preferred = "preferred"
    new_work_default = "new_work_default"


#: Promotion rank. Higher wins when choosing the default target for new work.
_STATE_RANK: dict[RolloutState, int] = {
    RolloutState.disabled: 0,
    RolloutState.retired_for_new_work: 1,
    RolloutState.direct_compatibility_only: 2,
    RolloutState.explicit_only: 3,
    RolloutState.canary: 4,
    RolloutState.preferred: 5,
    RolloutState.new_work_default: 6,
}

#: States that may be preselected as the default target for new work.
_DEFAULT_ELIGIBLE_STATES = frozenset(
    {RolloutState.preferred, RolloutState.new_work_default}
)
#: States an authoring surface may still offer as an explicit choice.
_EXPLICIT_SELECTABLE_STATES = frozenset(
    {
        RolloutState.explicit_only,
        RolloutState.canary,
        RolloutState.preferred,
        RolloutState.new_work_default,
        RolloutState.direct_compatibility_only,
    }
)

#: States that may still be executed. ``retired_for_new_work`` is not offered by
#: any authoring surface, but recorded authority must remain executable so a
#: rerun, replay, cleanup, or active execution keeps its recorded realizer.
_EXECUTABLE_STATES = frozenset(
    _EXPLICIT_SELECTABLE_STATES | {RolloutState.retired_for_new_work}
)


def state_admits_new_authoring(state: RolloutState) -> bool:
    """Return whether an authoring surface may offer ``state`` as a choice."""

    return RolloutState(state) in _EXPLICIT_SELECTABLE_STATES


def state_admits_execution(state: RolloutState) -> bool:
    """Return whether ``state`` may still compile and execute a plan."""

    return RolloutState(state) in _EXECUTABLE_STATES


class RuntimeProviderPathClass(StrEnum):
    """Which runtime architecture generation a target belongs to."""

    generic_omnigent = "generic_omnigent"
    legacy_profile_bound_omnigent = "legacy_profile_bound_omnigent"
    direct_compatibility = "direct_compatibility"


class RuntimeProviderRollbackControl(StrEnum):
    """Independently-operable rollback controls (issue #3833 required work 8).

    Every control affects **future admission only**. None of them transfers
    ownership of an active execution, rewrites recorded plan authority, or
    substitutes another runtime for a denied selection.
    """

    stop_new_generic_codex_admission = "stop_new_generic_codex_admission"
    stop_new_generic_claude_admission = "stop_new_generic_claude_admission"
    stop_new_opencode_shared_image_admission = (
        "stop_new_opencode_shared_image_admission"
    )
    restore_legacy_or_direct_default = "restore_legacy_or_direct_default"
    disable_native_interactive_chat = "disable_native_interactive_chat"
    stop_all_new_omnigent_work = "stop_all_new_omnigent_work"


#: Closed reason vocabulary. Every denial names exactly one of these.
class RolloutReason(StrEnum):
    combination_not_registered = "combination_not_registered"
    rollout_disabled = "rollout_disabled"
    rollout_explicit_only = "rollout_explicit_only"
    rollout_canary = "rollout_canary"
    rollout_canary_cohort_excluded = "rollout_canary_cohort_excluded"
    rollout_preferred = "rollout_preferred"
    rollout_new_work_default = "rollout_new_work_default"
    direct_compatibility_only = "direct_compatibility_only"
    retired_for_new_work = "retired_for_new_work"
    support_evidence_missing = "support_evidence_missing"
    support_evidence_stale = "support_evidence_stale"
    target_not_launch_ready = "target_not_launch_ready"
    model_not_qualified = "model_not_qualified"
    architecture_unsupported = "architecture_unsupported"
    host_mode_unavailable = "host_mode_unavailable"
    provider_profile_unavailable = "provider_profile_unavailable"
    rollback_new_admission_stopped = "rollback_new_admission_stopped"
    rollback_legacy_default_restored = "rollback_legacy_default_restored"
    rollback_all_omnigent_stopped = "rollback_all_omnigent_stopped"


class RuntimeProviderCombination(BaseModel):
    """One exact runtime-provider compatibility combination.

    Every dimension is a required, non-empty, exact identity. Paths that do not
    own a dimension record :data:`NOT_APPLICABLE` rather than an empty string so
    the combination key stays unambiguous.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    harness_id: str = Field(alias="harnessId")
    harness_implementation_ref: str = Field(alias="harnessImplementationRef")
    agent_profile_compatibility_class: str = Field(
        alias="agentProfileCompatibilityClass"
    )
    provider_runtime_id: str = Field(alias="providerRuntimeId")
    provider_class: str = Field(alias="providerClass")
    host_class_ref: str = Field(alias="hostClassRef")
    runtime_pack_ref: str = Field(alias="runtimePackRef")
    credential_materializer_ref: str = Field(alias="credentialMaterializerRef")
    launch_policy_ref: str = Field(alias="launchPolicyRef")
    host_mode: str = Field(alias="hostMode")
    architecture: str = Field(alias="architecture")
    model_configuration_class: str = Field(alias="modelConfigurationClass")
    execution_realizer_ref: str = Field(alias="executionRealizerRef")
    path_class: RuntimeProviderPathClass = Field(alias="pathClass")

    @model_validator(mode="after")
    def _validate_exact(self) -> "RuntimeProviderCombination":
        for name in _COMBINATION_DIMENSIONS:
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"combination dimension {name} is required")
            if value == ANY_DIMENSION:
                raise ValueError(
                    f"combination dimension {name} must be exact, not {ANY_DIMENSION!r}"
                )
        return self

    def key(self) -> str:
        return compute_runtime_provider_combination_key(self)


#: Declared dimension order. Used for keys, matching, and specificity ranking.
_COMBINATION_DIMENSIONS: tuple[str, ...] = (
    "harness_id",
    "harness_implementation_ref",
    "agent_profile_compatibility_class",
    "provider_runtime_id",
    "provider_class",
    "host_class_ref",
    "runtime_pack_ref",
    "credential_materializer_ref",
    "launch_policy_ref",
    "host_mode",
    "architecture",
    "model_configuration_class",
    "execution_realizer_ref",
    "path_class",
)

COMBINATION_DIMENSIONS: tuple[str, ...] = _COMBINATION_DIMENSIONS


def compute_runtime_provider_combination_key(
    combination: "RuntimeProviderCombination | Mapping[str, Any]",
) -> str:
    """Return the stable digest identity of one exact combination."""

    if isinstance(combination, RuntimeProviderCombination):
        data = combination.model_dump(by_alias=True, mode="json")
    else:
        data = {
            key: value
            for key, value in dict(combination).items()
        }
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"omnigent-runtime-provider-combination:sha256:{digest}"


class RolloutCohort(BaseModel):
    """Exact canary allowlists for one rule.

    An empty tuple means "this dimension is not restricted". A non-empty tuple
    admits only the listed exact values; anything else is excluded with
    ``rollout_canary_cohort_excluded``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    owner_cohorts: tuple[str, ...] = Field(default=(), alias="ownerCohorts")
    agent_profile_refs: tuple[str, ...] = Field(default=(), alias="agentProfileRefs")
    provider_profile_refs: tuple[str, ...] = Field(
        default=(), alias="providerProfileRefs"
    )
    harness_implementation_refs: tuple[str, ...] = Field(
        default=(), alias="harnessImplementationRefs"
    )
    host_class_refs: tuple[str, ...] = Field(default=(), alias="hostClassRefs")
    launch_policy_refs: tuple[str, ...] = Field(default=(), alias="launchPolicyRefs")
    models: tuple[str, ...] = ()
    architectures: tuple[str, ...] = ()
    host_modes: tuple[str, ...] = Field(default=(), alias="hostModes")

    def excluded_dimension(
        self, context: "RolloutSelectionContext"
    ) -> str | None:
        """Return the first allowlist dimension that excludes ``context``."""

        checks: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
            ("ownerCohorts", self.owner_cohorts, tuple(context.owner_cohorts)),
            (
                "agentProfileRefs",
                self.agent_profile_refs,
                (context.agent_profile_ref,) if context.agent_profile_ref else (),
            ),
            (
                "providerProfileRefs",
                self.provider_profile_refs,
                (context.provider_profile_ref,)
                if context.provider_profile_ref
                else (),
            ),
            (
                "harnessImplementationRefs",
                self.harness_implementation_refs,
                (context.harness_implementation_ref,)
                if context.harness_implementation_ref
                else (),
            ),
            (
                "hostClassRefs",
                self.host_class_refs,
                (context.host_class_ref,) if context.host_class_ref else (),
            ),
            (
                "launchPolicyRefs",
                self.launch_policy_refs,
                (context.launch_policy_ref,) if context.launch_policy_ref else (),
            ),
            ("models", self.models, (context.model,) if context.model else ()),
            (
                "architectures",
                self.architectures,
                (context.architecture,) if context.architecture else (),
            ),
            (
                "hostModes",
                self.host_modes,
                (context.host_mode,) if context.host_mode else (),
            ),
        )
        for name, allowed, observed in checks:
            if not allowed:
                continue
            if not observed or not set(observed) & set(allowed):
                return name
        return None


class RolloutRule(BaseModel):
    """One versioned rollout decision over an exact dimension selector.

    ``selector`` holds the same dimension names as
    :class:`RuntimeProviderCombination`. A dimension set to :data:`ANY_DIMENSION`
    is not pinned by this rule; every other value must match exactly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    target_id: str = Field(alias="targetId")
    label: str
    selector: dict[str, str]
    state: RolloutState
    generation: int = Field(ge=1)
    #: Maximum support-evidence age, in seconds, for a promoted state.
    evidence_max_age_seconds: int | None = Field(
        default=None, alias="evidenceMaxAgeSeconds", ge=1
    )
    #: A promoted state requires support evidence unless this is ``False``.
    requires_support_evidence: bool = Field(
        default=True, alias="requiresSupportEvidence"
    )
    canary: RolloutCohort = Field(default_factory=RolloutCohort)
    #: Whether ``restore_legacy_or_direct_default`` may promote this row.
    legacy_default_restorable: bool = Field(
        default=False, alias="legacyDefaultRestorable"
    )
    description: str = ""

    @model_validator(mode="after")
    def _validate_selector(self) -> "RolloutRule":
        if not str(self.target_id).strip():
            raise ValueError("rule targetId is required")
        unknown = set(self.selector) - set(_COMBINATION_DIMENSIONS)
        if unknown:
            raise ValueError(
                f"rule {self.target_id} declares unknown selector dimensions: "
                f"{sorted(unknown)}"
            )
        for name, value in self.selector.items():
            if not str(value).strip():
                raise ValueError(
                    f"rule {self.target_id} selector dimension {name} is empty"
                )
        return self

    @property
    def specificity(self) -> int:
        """Number of exactly-pinned dimensions. Higher wins during matching."""

        return sum(
            1
            for name in _COMBINATION_DIMENSIONS
            if self.selector.get(name, ANY_DIMENSION) != ANY_DIMENSION
        )

    def matches(self, combination: RuntimeProviderCombination) -> bool:
        for name in _COMBINATION_DIMENSIONS:
            expected = self.selector.get(name, ANY_DIMENSION)
            if expected == ANY_DIMENSION:
                continue
            if str(getattr(combination, name)) != str(expected):
                return False
        return True

    @property
    def path_class(self) -> RuntimeProviderPathClass | None:
        raw = self.selector.get("path_class", ANY_DIMENSION)
        if raw == ANY_DIMENSION:
            return None
        return RuntimeProviderPathClass(raw)

    @property
    def harness_id(self) -> str | None:
        raw = self.selector.get("harness_id", ANY_DIMENSION)
        return None if raw == ANY_DIMENSION else str(raw)


class RolloutSelectionContext(BaseModel):
    """Readiness and cohort inputs a rollout decision fails closed against."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    owner_cohorts: tuple[str, ...] = Field(default=(), alias="ownerCohorts")
    agent_profile_ref: str = Field(default="", alias="agentProfileRef")
    provider_profile_ref: str = Field(default="", alias="providerProfileRef")
    harness_implementation_ref: str = Field(
        default="", alias="harnessImplementationRef"
    )
    host_class_ref: str = Field(default="", alias="hostClassRef")
    launch_policy_ref: str = Field(default="", alias="launchPolicyRef")
    model: str = ""
    architecture: str = ""
    host_mode: str = Field(default="", alias="hostMode")

    support_evidence_ref: str = Field(default="", alias="supportEvidenceRef")
    support_evidence_age_seconds: float | None = Field(
        default=None, alias="supportEvidenceAgeSeconds"
    )
    support_evidence_expired: bool = Field(
        default=False, alias="supportEvidenceExpired"
    )
    launch_ready: bool = Field(default=True, alias="launchReady")
    model_qualified: bool = Field(default=True, alias="modelQualified")
    architecture_supported: bool = Field(default=True, alias="architectureSupported")
    host_mode_available: bool = Field(default=True, alias="hostModeAvailable")
    provider_profile_available: bool = Field(
        default=True, alias="providerProfileAvailable"
    )


class RolloutDecision(BaseModel):
    """Resolved, immutable rollout decision for one exact combination."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    policy_version: str = Field(alias="policyVersion")
    policy_generation: int = Field(alias="policyGeneration")
    combination_key: str = Field(alias="combinationKey")
    target_id: str = Field(alias="targetId")
    label: str
    path_class: RuntimeProviderPathClass = Field(alias="pathClass")
    #: State declared by the matched rule, before rollback controls.
    authored_state: RolloutState = Field(alias="authoredState")
    #: Effective state after readiness gates and rollback controls.
    state: RolloutState
    rule_generation: int = Field(alias="ruleGeneration")
    reason_code: RolloutReason = Field(alias="reasonCode")
    unavailable_reasons: tuple[RolloutReason, ...] = Field(
        default=(), alias="unavailableReasons"
    )
    rollback_controls_applied: tuple[RuntimeProviderRollbackControl, ...] = Field(
        default=(), alias="rollbackControlsApplied"
    )

    @property
    def default_eligible(self) -> bool:
        return self.state in _DEFAULT_ELIGIBLE_STATES

    @property
    def explicit_selection_allowed(self) -> bool:
        return state_admits_new_authoring(self.state)

    @property
    def compatibility_path(self) -> bool:
        return self.path_class is not RuntimeProviderPathClass.generic_omnigent

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(by_alias=True, mode="json")
        payload["defaultEligible"] = self.default_eligible
        payload["explicitSelectionAllowed"] = self.explicit_selection_allowed
        payload["compatibilityPath"] = self.compatibility_path
        return payload


class RuntimeProviderRolloutPolicy(BaseModel):
    """One deployment-owned, versioned rollout policy document."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    policy_version: str = Field(
        RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION, alias="policyVersion"
    )
    generation: int = Field(1, ge=1)
    rules: tuple[RolloutRule, ...] = ()
    rollback_controls: tuple[RuntimeProviderRollbackControl, ...] = Field(
        default=(), alias="rollbackControls"
    )
    canary_cohorts: tuple[str, ...] = Field(default=(), alias="canaryCohorts")

    @model_validator(mode="after")
    def _validate_policy(self) -> "RuntimeProviderRolloutPolicy":
        if self.policy_version != RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION:
            raise ValueError(
                "unsupported runtime-provider rollout policyVersion: "
                f"{self.policy_version!r}"
            )
        seen: set[str] = set()
        for rule in self.rules:
            if rule.target_id in seen:
                raise ValueError(f"duplicate rollout targetId {rule.target_id!r}")
            seen.add(rule.target_id)
        return self

    def match(
        self, combination: RuntimeProviderCombination
    ) -> RolloutRule | None:
        """Return the most specific matching rule, or ``None``."""

        matches = [rule for rule in self.rules if rule.matches(combination)]
        if not matches:
            return None
        # Deterministic: most pinned dimensions first, then declaration order.
        best = max(
            enumerate(matches),
            key=lambda item: (item[1].specificity, -item[0]),
        )
        return best[1]

    def rule(self, target_id: str) -> RolloutRule | None:
        return next(
            (rule for rule in self.rules if rule.target_id == target_id), None
        )

    def rules_for(
        self,
        *,
        harness_id: str | None = None,
        path_class: RuntimeProviderPathClass | None = None,
    ) -> tuple[RolloutRule, ...]:
        return tuple(
            rule
            for rule in self.rules
            if (harness_id is None or rule.harness_id == harness_id)
            and (path_class is None or rule.path_class is path_class)
        )


# --- Rollback ---------------------------------------------------------------


def _rollback_blocks(
    control: RuntimeProviderRollbackControl,
    *,
    harness_id: str | None,
    path_class: RuntimeProviderPathClass | None,
) -> bool:
    """Return whether ``control`` stops new admission for this row.

    Matching is by exact harness identity and path class — never by display
    name or runtime substring.
    """

    if control is RuntimeProviderRollbackControl.stop_all_new_omnigent_work:
        return path_class is not RuntimeProviderPathClass.direct_compatibility
    if control is RuntimeProviderRollbackControl.stop_new_generic_codex_admission:
        return (
            harness_id == "codex-native"
            and path_class is RuntimeProviderPathClass.generic_omnigent
        )
    if control is RuntimeProviderRollbackControl.stop_new_generic_claude_admission:
        return (
            harness_id == "claude-native"
            and path_class is RuntimeProviderPathClass.generic_omnigent
        )
    if (
        control
        is RuntimeProviderRollbackControl.stop_new_opencode_shared_image_admission
    ):
        return (
            harness_id == "opencode-native"
            and path_class is RuntimeProviderPathClass.generic_omnigent
        )
    return False


def parse_rollback_controls(
    value: object,
) -> tuple[RuntimeProviderRollbackControl, ...]:
    """Parse a comma-separated control list; unknown values fail fast."""

    if value is None:
        return ()
    if isinstance(value, str):
        raw_items: Sequence[Any] = [
            item.strip() for item in value.split(",") if item.strip()
        ]
    elif isinstance(value, Iterable):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:  # pragma: no cover - defensive
        raise ValueError(f"unsupported rollback control input: {value!r}")
    controls: list[RuntimeProviderRollbackControl] = []
    for item in raw_items:
        normalized = str(item).lower().replace("-", "_")
        try:
            control = RuntimeProviderRollbackControl(normalized)
        except ValueError as exc:
            raise ValueError(
                "unsupported Omnigent runtime-provider rollback control: "
                f"{item!r}"
            ) from exc
        if control not in controls:
            controls.append(control)
    return tuple(controls)


def native_interactive_chat_allowed(
    policy: RuntimeProviderRolloutPolicy,
) -> bool:
    """Return whether native interactive chat may be offered for new work.

    Historical reads, diagnostics, and evidence are never affected.
    """

    return not (
        RuntimeProviderRollbackControl.disable_native_interactive_chat
        in policy.rollback_controls
        or RuntimeProviderRollbackControl.stop_all_new_omnigent_work
        in policy.rollback_controls
    )


# --- Resolution -------------------------------------------------------------


_STATE_REASON: dict[RolloutState, RolloutReason] = {
    RolloutState.disabled: RolloutReason.rollout_disabled,
    RolloutState.retired_for_new_work: RolloutReason.retired_for_new_work,
    RolloutState.direct_compatibility_only: RolloutReason.direct_compatibility_only,
    RolloutState.explicit_only: RolloutReason.rollout_explicit_only,
    RolloutState.canary: RolloutReason.rollout_canary,
    RolloutState.preferred: RolloutReason.rollout_preferred,
    RolloutState.new_work_default: RolloutReason.rollout_new_work_default,
}


def _readiness_denials(
    rule: RolloutRule, context: RolloutSelectionContext
) -> tuple[RolloutReason, ...]:
    """Return every readiness input that blocks a promoted state."""

    denials: list[RolloutReason] = []
    if rule.requires_support_evidence:
        if not context.support_evidence_ref:
            denials.append(RolloutReason.support_evidence_missing)
        elif context.support_evidence_expired:
            denials.append(RolloutReason.support_evidence_stale)
        elif (
            rule.evidence_max_age_seconds is not None
            and context.support_evidence_age_seconds is not None
            and context.support_evidence_age_seconds
            > float(rule.evidence_max_age_seconds)
        ):
            denials.append(RolloutReason.support_evidence_stale)
    if not context.launch_ready:
        denials.append(RolloutReason.target_not_launch_ready)
    if not context.model_qualified:
        denials.append(RolloutReason.model_not_qualified)
    if not context.architecture_supported:
        denials.append(RolloutReason.architecture_unsupported)
    if not context.host_mode_available:
        denials.append(RolloutReason.host_mode_unavailable)
    if not context.provider_profile_available:
        denials.append(RolloutReason.provider_profile_unavailable)
    return tuple(denials)


def _apply_rollback_overlay(
    *,
    rule: RolloutRule,
    policy: RuntimeProviderRolloutPolicy,
    state: RolloutState,
    reason: RolloutReason,
) -> tuple[RolloutState, tuple[RuntimeProviderRollbackControl, ...], RolloutReason]:
    """Apply the policy's rollback controls to one row's state.

    Rollback matching depends only on the exact harness identity and path class
    a rule pins, so the same overlay serves both the per-execution decision and
    the authoring catalog. Every control changes *future admission* only.
    """

    applied: list[RuntimeProviderRollbackControl] = []
    for control in policy.rollback_controls:
        if _rollback_blocks(
            control, harness_id=rule.harness_id, path_class=rule.path_class
        ):
            applied.append(control)
            state = RolloutState.disabled
            reason = (
                RolloutReason.rollback_all_omnigent_stopped
                if control
                is RuntimeProviderRollbackControl.stop_all_new_omnigent_work
                else RolloutReason.rollback_new_admission_stopped
            )

    if (
        RuntimeProviderRollbackControl.restore_legacy_or_direct_default
        in policy.rollback_controls
        and RuntimeProviderRollbackControl.stop_all_new_omnigent_work
        not in policy.rollback_controls
    ):
        applied.append(
            RuntimeProviderRollbackControl.restore_legacy_or_direct_default
        )
        if rule.path_class is RuntimeProviderPathClass.generic_omnigent:
            if state in _DEFAULT_ELIGIBLE_STATES:
                state = RolloutState.explicit_only
                reason = RolloutReason.rollback_legacy_default_restored
        elif rule.legacy_default_restorable:
            # Only an explicitly supported legacy/direct row may become the
            # restored default; otherwise the control never invents one.
            state = RolloutState.new_work_default
            reason = RolloutReason.rollback_legacy_default_restored
    return state, tuple(dict.fromkeys(applied)), reason


def effective_rule_state(
    rule: RolloutRule, policy: RuntimeProviderRolloutPolicy
) -> tuple[RolloutState, tuple[RuntimeProviderRollbackControl, ...], RolloutReason]:
    """Return one rule's state after the policy's active rollback controls.

    Authoring surfaces use this so an operator-visible catalog reflects the
    active rollback without needing a per-execution readiness context.
    """

    return _apply_rollback_overlay(
        rule=rule,
        policy=policy,
        state=rule.state,
        reason=_STATE_REASON[rule.state],
    )


def resolve_rollout_decision(
    *,
    policy: RuntimeProviderRolloutPolicy,
    combination: RuntimeProviderCombination,
    context: RolloutSelectionContext | None = None,
) -> RolloutDecision:
    """Resolve the effective rollout decision for one exact combination."""

    ctx = context or RolloutSelectionContext()
    rule = policy.match(combination)
    combination_key = combination.key()
    if rule is None:
        # A missing support row leaves the path explicit; it is never promoted.
        return RolloutDecision(
            policyVersion=policy.policy_version,
            policyGeneration=policy.generation,
            combinationKey=combination_key,
            targetId="unregistered",
            label="Unregistered runtime-provider combination",
            pathClass=combination.path_class,
            authoredState=RolloutState.explicit_only,
            state=RolloutState.explicit_only,
            ruleGeneration=0,
            reasonCode=RolloutReason.combination_not_registered,
            unavailableReasons=(RolloutReason.combination_not_registered,),
        )

    authored_state = rule.state
    state = authored_state
    reason = _STATE_REASON[state]
    unavailable: list[RolloutReason] = []
    applied: list[RuntimeProviderRollbackControl] = []

    # 1. Canary cohort membership. Exact allowlists only.
    if state is RolloutState.canary:
        excluded = rule.canary.excluded_dimension(ctx)
        if excluded is not None:
            state = RolloutState.explicit_only
            reason = RolloutReason.rollout_canary_cohort_excluded
            unavailable.append(RolloutReason.rollout_canary_cohort_excluded)

    # 2. Readiness fails closed for promoted states only. An explicit-only or
    #    compatibility row stays selectable and reports its own reasons.
    if state in _DEFAULT_ELIGIBLE_STATES or state is RolloutState.canary:
        denials = _readiness_denials(rule, ctx)
        if denials:
            unavailable.extend(denials)
            state = RolloutState.explicit_only
            reason = denials[0]

    # 3. Rollback controls change future admission only.
    state, rollback_applied, reason = _apply_rollback_overlay(
        rule=rule, policy=policy, state=state, reason=reason
    )
    applied.extend(rollback_applied)
    if reason in {
        RolloutReason.rollback_new_admission_stopped,
        RolloutReason.rollback_all_omnigent_stopped,
    }:
        # Only a *blocking* rollback is an unavailability. Restoring a legacy or
        # direct default changes which row is preferred; it does not make the
        # demoted row unavailable.
        unavailable.append(reason)

    if state not in _EXPLICIT_SELECTABLE_STATES:
        # A non-selectable outcome always names its blocking reason so callers
        # never have to infer one from the state alone.
        unavailable.append(reason)

    return RolloutDecision(
        policyVersion=policy.policy_version,
        policyGeneration=policy.generation,
        combinationKey=combination_key,
        targetId=rule.target_id,
        label=rule.label,
        pathClass=combination.path_class,
        authoredState=authored_state,
        state=state,
        ruleGeneration=rule.generation,
        reasonCode=reason,
        unavailableReasons=tuple(dict.fromkeys(unavailable)),
        rollbackControlsApplied=tuple(dict.fromkeys(applied)),
    )


def rollout_state_rank(state: RolloutState) -> int:
    """Return the promotion rank of ``state``. Higher is more promoted."""

    return _STATE_RANK[RolloutState(state)]


def freeze_rollout_record(decision: RolloutDecision) -> dict[str, Any]:
    """Return the compact record persisted with an immutable execution plan.

    Only versioned, low-cardinality decision authority is frozen. Changing the
    live policy later cannot reinterpret a plan that already carries this
    record.
    """

    return {
        "policyVersion": decision.policy_version,
        "policyGeneration": decision.policy_generation,
        "combinationKey": decision.combination_key,
        "targetId": decision.target_id,
        "pathClass": str(decision.path_class),
        "state": str(decision.state),
        "ruleGeneration": decision.rule_generation,
        "reasonCode": str(decision.reason_code),
    }


# --- Deployment-owned policy resolution --------------------------------------


_GENERIC_REALIZER = "generic-omnigent-host@1"
_LEGACY_CODEX_REALIZER = "codex-profile-bound@1"


def _rule(
    *,
    target_id: str,
    label: str,
    state: RolloutState,
    selector: dict[str, str],
    generation: int = 1,
    requires_support_evidence: bool = True,
    legacy_default_restorable: bool = False,
    description: str = "",
) -> RolloutRule:
    """Build one built-in rollout row.

    ``requires_support_evidence`` defaults to ``True``, matching
    :class:`RolloutRule`, so a built-in row is promoted for new work only while
    the deployment holds current support evidence for that exact combination.
    The readiness context is supplied at plan compilation
    (``compile_execution_plan``); a promoted row with missing, expired, or
    over-age evidence is demoted to ``explicit_only`` with the exact reason
    rather than frozen into an execution plan as a default.
    """

    return RolloutRule.model_validate(
        {
            "targetId": target_id,
            "label": label,
            "selector": selector,
            "state": state,
            "generation": generation,
            "requiresSupportEvidence": requires_support_evidence,
            "legacyDefaultRestorable": legacy_default_restorable,
            "description": description,
        }
    )


def default_runtime_provider_rollout_policy(
    *, env: Mapping[str, Any] | None = None
) -> RuntimeProviderRolloutPolicy:
    """Return the built-in policy for this deployment's qualification state.

    The built-in rows reproduce the deployment's current qualification gates
    (``MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED``,
    ``MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED``,
    ``MOONMIND_OMNIGENT_OPENCODE_ENABLED``) as one versioned per-combination
    policy instead of scattered boolean checks.
    """

    from moonmind.omnigent.settings import (
        generic_claude_qualified,
        generic_codex_qualified,
        opencode_support_enabled,
    )

    source = env if env is not None else os.environ
    codex_generic = generic_codex_qualified(env=source)
    claude_generic = generic_claude_qualified(env=source)
    opencode = opencode_support_enabled(env=source)

    rules = (
        _rule(
            target_id="codex.generic-omnigent",
            label="Codex via generic Omnigent",
            state=(
                RolloutState.new_work_default
                if codex_generic
                else RolloutState.disabled
            ),
            selector={
                "harness_id": "codex-native",
                "runtime_pack_ref": "codex-native-pack@1",
                "execution_realizer_ref": _GENERIC_REALIZER,
                "path_class": RuntimeProviderPathClass.generic_omnigent,
            },
            description=(
                "Shared-image Codex on the generic Omnigent host realizer."
            ),
        ),
        _rule(
            target_id="codex.legacy-profile-bound-omnigent",
            label="Codex via legacy profile-bound Omnigent",
            state=(
                RolloutState.retired_for_new_work
                if codex_generic
                else RolloutState.new_work_default
            ),
            selector={
                "harness_id": "codex-native",
                "execution_realizer_ref": _LEGACY_CODEX_REALIZER,
                "path_class": (
                    RuntimeProviderPathClass.legacy_profile_bound_omnigent
                ),
            },
            legacy_default_restorable=True,
            description=(
                "Retained profile-bound Codex coordinator. Compatibility path "
                "once the generic Codex row is promoted."
            ),
        ),
        _rule(
            target_id="claude.generic-omnigent",
            label="Claude Code via generic Omnigent",
            state=(
                RolloutState.new_work_default
                if claude_generic
                else RolloutState.disabled
            ),
            selector={
                "harness_id": "claude-native",
                "runtime_pack_ref": "claude-native-pack@1",
                "execution_realizer_ref": _GENERIC_REALIZER,
                "path_class": RuntimeProviderPathClass.generic_omnigent,
            },
            description=(
                "Shared-image Claude Code on the generic Omnigent host realizer."
            ),
        ),
        _rule(
            target_id="opencode.generic-omnigent",
            label="OpenCode via generic Omnigent",
            state=(
                RolloutState.new_work_default if opencode else RolloutState.disabled
            ),
            selector={
                "harness_id": "opencode-native",
                "runtime_pack_ref": "opencode-native-pack@1",
                "execution_realizer_ref": _GENERIC_REALIZER,
                "path_class": RuntimeProviderPathClass.generic_omnigent,
            },
            description="Shared-image OpenCode on the generic Omnigent host.",
        ),
        _rule(
            target_id="codex.direct",
            label="Direct Codex compatibility",
            state=RolloutState.direct_compatibility_only,
            selector={
                "provider_runtime_id": "codex_cli",
                "path_class": RuntimeProviderPathClass.direct_compatibility,
            },
            legacy_default_restorable=True,
            description=(
                "Direct managed Codex runtime. Migration compatibility path."
            ),
        ),
        _rule(
            target_id="claude.direct",
            label="Direct Claude compatibility",
            state=RolloutState.direct_compatibility_only,
            selector={
                "provider_runtime_id": "claude_code",
                "path_class": RuntimeProviderPathClass.direct_compatibility,
            },
            legacy_default_restorable=True,
            description=(
                "Direct managed Claude Code runtime. Migration compatibility path."
            ),
        ),
    )

    return RuntimeProviderRolloutPolicy.model_validate(
        {
            "policyVersion": RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
            "generation": 1,
            "rules": rules,
            "rollbackControls": parse_rollback_controls(
                source.get(RUNTIME_PROVIDER_ROLLBACK_ENV)
            ),
            "canaryCohorts": tuple(
                item.strip()
                for item in str(
                    source.get(RUNTIME_PROVIDER_CANARY_COHORTS_ENV) or ""
                ).split(",")
                if item.strip()
            ),
        }
    )


def load_runtime_provider_rollout_policy(
    *, env: Mapping[str, Any] | None = None
) -> RuntimeProviderRolloutPolicy:
    """Return the deployment-owned policy.

    ``MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT`` may carry a complete policy
    document. Invalid configuration fails fast rather than silently reverting to
    the built-in policy, because a misread rollout document would change which
    combination becomes a product default.
    """

    source = env if env is not None else os.environ
    raw = str(source.get(RUNTIME_PROVIDER_ROLLOUT_ENV) or "").strip()
    if not raw:
        return default_runtime_provider_rollout_policy(env=source)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "invalid Omnigent runtime-provider rollout policy: not valid JSON"
        ) from exc
    if not isinstance(document, Mapping):
        raise ValueError(
            "invalid Omnigent runtime-provider rollout policy: expected an object"
        )
    payload = dict(document)
    payload.setdefault("policyVersion", RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION)
    if "rollbackControls" not in payload:
        payload["rollbackControls"] = parse_rollback_controls(
            source.get(RUNTIME_PROVIDER_ROLLBACK_ENV)
        )
    return RuntimeProviderRolloutPolicy.model_validate(payload)


__all__ = [
    "ANY_DIMENSION",
    "COMBINATION_DIMENSIONS",
    "NOT_APPLICABLE",
    "RUNTIME_PROVIDER_CANARY_COHORTS_ENV",
    "RUNTIME_PROVIDER_ROLLBACK_ENV",
    "RUNTIME_PROVIDER_ROLLOUT_ENV",
    "RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION",
    "RolloutCohort",
    "RolloutDecision",
    "RolloutReason",
    "RolloutRule",
    "RolloutSelectionContext",
    "RolloutState",
    "RuntimeProviderCombination",
    "RuntimeProviderPathClass",
    "RuntimeProviderRollbackControl",
    "RuntimeProviderRolloutPolicy",
    "compute_runtime_provider_combination_key",
    "default_runtime_provider_rollout_policy",
    "effective_rule_state",
    "freeze_rollout_record",
    "load_runtime_provider_rollout_policy",
    "native_interactive_chat_allowed",
    "parse_rollback_controls",
    "resolve_rollout_decision",
    "rollout_state_rank",
    "state_admits_execution",
    "state_admits_new_authoring",
]
