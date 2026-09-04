"""Versioned runtime-provider rollout policy for Omnigent default promotion.

Source issue: MoonLadderStudios/MoonMind#3833.
Design source: ``docs/Omnigent/PrimaryRuntimeProviderStrategy.md`` sections 9-11.

One deployment-owned policy selects defaults by exact compatibility
dimensions. Every authoring surface (Workflow Create, presets, schedules,
edit/rerun, retry-as-fresh, Checkpoint Branch, remediation, linked
continuation, API/MCP submissions) resolves through
:func:`select_authoring_target` so no surface reconstructs a default from
environment variables or a hard-coded runtime map.

Rules enforced here:

* routing is by exact combination key only -- never by display name or
  runtime substring;
* the rollout decision and generation are immutable admitted authority
  persisted with the execution plan; changing policy never reinterprets an
  existing execution or Temporal history;
* missing or stale support evidence fails closed with an exact reason;
* an explicit Omnigent selection never silently falls back;
* direct and legacy paths are compatibility-only labels, not equal defaults;
* canary and rollback operate independently per exact combination and affect
  future admission only.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLLOUT_POLICY_VERSION = "moonmind.omnigent-runtime-provider-rollout/v1"
ROLLOUT_POLICY_ENV = "MOONMIND_OMNIGENT_ROLLOUT_POLICY_REF"

_CANONICAL_RUNTIME_ID = "external/omnigent"


class RolloutState(StrEnum):
    """Per-combination rollout state (brief governing behavior)."""

    DISABLED = "disabled"
    EXPLICIT_ONLY = "explicit_only"
    CANARY = "canary"
    PREFERRED = "preferred"
    NEW_WORK_DEFAULT = "new_work_default"
    DIRECT_COMPATIBILITY_ONLY = "direct_compatibility_only"
    RETIRED_FOR_NEW_WORK = "retired_for_new_work"


class AuthoringSurface(StrEnum):
    """Every surface that must share one selection/admission boundary."""

    WORKFLOW_CREATE = "workflow_create"
    PRESET = "preset"
    PRESET_EXPANSION = "preset_expansion"
    SCHEDULE = "schedule"
    SCHEDULE_OCCURRENCE = "schedule_occurrence"
    EDIT = "edit"
    RERUN = "rerun"
    RETRY_FRESH = "retry_fresh"
    BRANCH_CREATE = "branch_create"
    BRANCH_CONTINUE = "branch_continue"
    BRANCH_FORK = "branch_fork"
    REMEDIATION = "remediation"
    LINKED_CONTINUATION = "linked_continuation"
    API = "api"
    MCP = "mcp"


class RolloutAdmissionError(ValueError):
    """Fail-closed admission denial with an exact actionable reason."""


# Product intention -> harness implementation. Exact map only: callers pass a
# product intention, never a display label or runtime substring.
PRODUCT_INTENTION_HARNESS: dict[str, str] = {
    "codex": "codex-native",
    "claude": "claude-native",
    "opencode": "opencode-native",
}

# Canonical friendly labels. Friendly labels never create new top-level
# runtime ids: Omnigent-backed targets submit ``external/omnigent`` while
# direct compatibility targets keep their direct runtime id.
TARGET_IDENTITY_LABELS: dict[tuple[str, str], str] = {
    ("codex-native", "generic-omnigent-host@1"): "Codex via generic Omnigent",
    ("codex-native", "codex-profile-bound@1"): "Codex via legacy profile-bound Omnigent",
    ("codex-native", "direct"): "Direct Codex compatibility",
    ("claude-native", "generic-omnigent-host@1"): "Claude Code via generic Omnigent",
    ("claude-native", "direct"): "Direct Claude compatibility",
    ("opencode-native", "generic-omnigent-host@1"): "OpenCode via generic Omnigent",
}

_DIRECT_COMPAT_RUNTIME: dict[str, str] = {
    "codex-native": "codex_cli",
    "claude-native": "claude_code",
}

_LOW_CARD_SURFACES = frozenset(item.value for item in AuthoringSurface)
_LOW_CARD_STATES = frozenset(item.value for item in RolloutState)
_LOW_CARD_DECISIONS = frozenset(
    {"admitted_default", "admitted_explicit", "denied", "compatibility_only"}
)


class RolloutCombination(BaseModel):
    """Exact compatibility dimensions for one support combination.

    Every field is an exact versioned ref or class -- never a display name or
    runtime substring. ``ownerCohort`` is the optional canary cohort owner.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    harness_implementation: str = Field(alias="harnessImplementation")
    agent_profile_class: str = Field(alias="agentProfileClass")
    provider_runtime: str = Field(alias="providerRuntime")
    provider_class: str = Field(alias="providerClass")
    host_class: str = Field(alias="hostClass")
    runtime_pack: str = Field(alias="runtimePack")
    credential_materializer: str = Field(alias="credentialMaterializer")
    launch_policy: str = Field(alias="launchPolicy")
    host_mode: str = Field(alias="hostMode")
    architecture: str = Field(alias="architecture")
    model_config_class: str = Field(alias="modelConfigClass")
    execution_realizer: str = Field(alias="executionRealizer")
    support_evidence_ref: str = Field(alias="supportEvidenceRef")
    owner_cohort: str | None = Field(default=None, alias="ownerCohort")

    @model_validator(mode="after")
    def validate_exact_refs(self) -> "RolloutCombination":
        for field_name in (
            "harness_implementation",
            "agent_profile_class",
            "provider_runtime",
            "provider_class",
            "host_class",
            "runtime_pack",
            "credential_materializer",
            "launch_policy",
            "host_mode",
            "architecture",
            "model_config_class",
            "execution_realizer",
            "support_evidence_ref",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        return self


def combination_key(combination: RolloutCombination | Mapping[str, Any]) -> str:
    """Return the stable exact-combination key (never display-name derived)."""

    if isinstance(combination, RolloutCombination):
        data = combination.model_dump(by_alias=True, mode="json")
    else:
        data = dict(combination)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return "omnigent-rollout-combination:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()


class RolloutEntry(BaseModel):
    """One versioned per-combination rollout row."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    combination: RolloutCombination
    state: RolloutState
    evidence_fresh: bool = Field(alias="evidenceFresh")
    evidence_reason: str | None = Field(default=None, alias="evidenceReason")
    launch_ready: bool = Field(default=True, alias="launchReady")
    canary_cohorts: tuple[str, ...] = Field(default_factory=tuple, alias="canaryCohorts")
    last_canary_at: str | None = Field(default=None, alias="lastCanaryAt")
    rollback_available: bool = Field(default=True, alias="rollbackAvailable")

    @property
    def key(self) -> str:
        return combination_key(self.combination)


class RolloutPolicy(BaseModel):
    """Deployment-owned versioned rollout policy document."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["moonmind.omnigent-runtime-provider-rollout/v1"] = Field(
        ROLLOUT_POLICY_VERSION, alias="schemaVersion"
    )
    generation: int = Field(ge=1)
    entries: tuple[RolloutEntry, ...] = ()

    @model_validator(mode="after")
    def validate_unique_combinations(self) -> "RolloutPolicy":
        keys = [entry.key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("rollout policy has duplicate combinations")
        return self

    def entry_for_key(self, key: str) -> RolloutEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None


def empty_rollout_policy(*, generation: int = 1) -> RolloutPolicy:
    """Return a fail-closed policy: every unknown combination is unavailable."""

    return RolloutPolicy.model_validate(
        {"schemaVersion": ROLLOUT_POLICY_VERSION, "generation": generation, "entries": []}
    )


def load_rollout_policy(
    *,
    path: str | None = None,
    env: Mapping[str, Any] | None = None,
) -> RolloutPolicy:
    """Load the deployment-owned rollout policy document or fail closed."""

    values = os.environ if env is None else env
    ref = (path or str(values.get(ROLLOUT_POLICY_ENV, "") or "")).strip()
    if not ref:
        return empty_rollout_policy()
    try:
        with open(ref, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutAdmissionError(
            f"rollout policy unavailable: {ref}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RolloutAdmissionError("rollout policy document must be an object")
    try:
        return RolloutPolicy.model_validate(raw)
    except ValueError as exc:
        raise RolloutAdmissionError(f"rollout policy invalid: {exc}") from exc


def rollout_state_for(
    policy: RolloutPolicy, combination: RolloutCombination
) -> tuple[RolloutState, int, RolloutEntry | None]:
    """Return the (state, generation, entry) governing an exact combination."""

    entry = policy.entry_for_key(combination_key(combination))
    if entry is None:
        return RolloutState.DISABLED, policy.generation, None
    return entry.state, policy.generation, entry


class AuthoringSelection(BaseModel):
    """One authored target choice at the shared admission boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    surface: AuthoringSurface
    combination: RolloutCombination
    explicit: bool = True
    owner_cohort: str | None = Field(default=None, alias="ownerCohort")


class AdmittedAuthority(BaseModel):
    """Immutable rollout authority frozen into the execution plan."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    rollout_policy_version: str = Field(alias="rolloutPolicyVersion")
    rollout_generation: int = Field(alias="rolloutGeneration")
    rollout_state: RolloutState = Field(alias="rolloutState")
    rollout_combination_key: str = Field(alias="rolloutCombinationKey")
    execution_realizer: str = Field(alias="executionRealizer")
    canonical_runtime_id: str = Field(alias="canonicalRuntimeId")
    target_label: str = Field(alias="targetLabel")
    default_selection: bool = Field(alias="defaultSelection")


def target_identity_label(harness_id: str, realizer_ref: str) -> str:
    """Return the truthful friendly label for an exact (harness, realizer)."""

    try:
        return TARGET_IDENTITY_LABELS[(harness_id, realizer_ref)]
    except KeyError as exc:
        raise RolloutAdmissionError(
            f"unknown target identity: {harness_id} via {realizer_ref}"
        ) from exc


def is_compatibility_path(harness_id: str, realizer_ref: str) -> bool:
    """Whether a target is a compatibility-only path, not a promoted default."""

    return realizer_ref in {"direct", "codex-profile-bound@1"}


def canonical_runtime_id_for(harness_id: str, realizer_ref: str) -> str:
    """Return the submitted runtime id without creating new top-level ids."""

    if realizer_ref == "direct":
        return _DIRECT_COMPAT_RUNTIME[harness_id]
    return _CANONICAL_RUNTIME_ID


def _deny(reason: str) -> RolloutAdmissionError:
    return RolloutAdmissionError(reason)


def admit_authoring_selection(
    *,
    policy: RolloutPolicy,
    selection: AuthoringSelection,
) -> AdmittedAuthority:
    """Admit one authored selection at the shared boundary or fail closed.

    Every authoring surface calls this function -- no surface may reconstruct
    a default from environment variables or a hard-coded runtime map. Denial
    raises :class:`RolloutAdmissionError` with the exact unavailable reason;
    it never substitutes another runtime (no silent fallback).
    """

    entry = policy.entry_for_key(combination_key(selection.combination))
    if entry is None:
        raise _deny(
            "rollout_combination_unknown: no rollout row for this exact "
            "support combination"
        )
    if not entry.evidence_fresh:
        reason = entry.evidence_reason or "support evidence missing or stale"
        raise _deny(f"support_evidence_unavailable: {reason}")
    if not entry.launch_ready:
        raise _deny("target_not_launch_ready for this exact combination")

    state = entry.state
    harness = _harness_id_from_implementation(
        selection.combination.harness_implementation
    )
    realizer = selection.combination.execution_realizer

    if state is RolloutState.DISABLED:
        raise _deny("rollout_disabled for this exact combination")
    if state is RolloutState.RETIRED_FOR_NEW_WORK:
        raise _deny("retired_for_new_work: explicit replacement is required")
    if state is RolloutState.DIRECT_COMPATIBILITY_ONLY and realizer != "direct":
        raise _deny(
            "direct_compatibility_only: only the direct compatibility path "
            "is admissible for this combination"
        )
    if state is RolloutState.CANARY:
        cohort = selection.owner_cohort or selection.combination.owner_cohort
        if not cohort or cohort not in entry.canary_cohorts:
            raise _deny("canary_cohort_not_allowlisted for this combination")
    if state is RolloutState.EXPLICIT_ONLY and not selection.explicit:
        raise _deny("explicit_selection_required for this combination")

    default_selection = (
        state in {RolloutState.PREFERRED, RolloutState.NEW_WORK_DEFAULT}
        and not selection.explicit
    )
    return AdmittedAuthority.model_validate(
        {
            "rolloutPolicyVersion": ROLLOUT_POLICY_VERSION,
            "rolloutGeneration": policy.generation,
            "rolloutState": state,
            "rolloutCombinationKey": entry.key,
            "executionRealizer": realizer,
            "canonicalRuntimeId": canonical_runtime_id_for(harness, realizer),
            "targetLabel": target_identity_label(harness, realizer),
            "defaultSelection": default_selection,
        }
    )


def _harness_id_from_implementation(implementation: str) -> str:
    """Map an exact harness implementation ref to its harness id.

    Exact-prefix match on the implementation family only -- never a display
    name or runtime substring.
    """

    lowered = implementation.strip().lower()
    for harness_id in ("codex-native", "claude-native", "opencode-native"):
        if lowered == harness_id or lowered.startswith(harness_id + "@") or lowered.startswith(
            harness_id + ":"
        ):
            return harness_id
    raise _deny(
        f"unknown harness implementation: {implementation}: "
        "routing by display name or runtime substring is forbidden"
    )


def resolve_default_target(
    *,
    policy: RolloutPolicy,
    product_intention: str,
    surface: AuthoringSurface,
    combination_template: Mapping[str, Any],
    owner_cohort: str | None = None,
) -> AdmittedAuthority:
    """Resolve the promoted Omnigent default for a product intention.

    ``product_intention`` is one of ``codex``/``claude``/``opencode`` mapped
    through :data:`PRODUCT_INTENTION_HARNESS`. A promoted (``preferred`` or
    ``new_work_default``) row preselects the qualified Omnigent Agent Profile
    target while the submitted canonical identity remains
    ``external/omnigent``. Anything else fails closed with the exact reason.
    """

    normalized = product_intention.strip().lower()
    if normalized not in PRODUCT_INTENTION_HARNESS:
        raise _deny(
            f"unknown product intention: {product_intention!r}: "
            "routing by display name or runtime substring is forbidden"
        )
    template = dict(combination_template)
    selection = AuthoringSelection.model_validate(
        {
            "surface": surface,
            "combination": template,
            "explicit": False,
            "ownerCohort": owner_cohort,
        }
    )
    admitted = admit_authoring_selection(policy=policy, selection=selection)
    if admitted.rollout_state not in {
        RolloutState.PREFERRED,
        RolloutState.NEW_WORK_DEFAULT,
    }:
        raise _deny(
            f"no promoted default for {normalized}: state is "
            f"{admitted.rollout_state.value}; explicit selection is required"
        )
    if admitted.canonical_runtime_id != _CANONICAL_RUNTIME_ID:
        raise _deny("promoted default must submit external/omnigent")
    return admitted


def select_authoring_target(
    *,
    policy: RolloutPolicy,
    surface: AuthoringSurface,
    combination: RolloutCombination | Mapping[str, Any],
    explicit: bool,
    owner_cohort: str | None = None,
) -> AdmittedAuthority:
    """Shared selection/admission entrypoint for every authoring surface."""

    if surface not in AuthoringSurface:
        raise _deny(f"unknown authoring surface: {surface!r}")
    payload = (
        combination.model_dump(by_alias=True, mode="json")
        if isinstance(combination, RolloutCombination)
        else dict(combination)
    )
    selection = AuthoringSelection.model_validate(
        {
            "surface": surface,
            "combination": payload,
            "explicit": bool(explicit),
            "ownerCohort": owner_cohort,
        }
    )
    return admit_authoring_selection(policy=policy, selection=selection)


def preserve_or_upgrade_target(
    *,
    recorded: AdmittedAuthority,
    policy: RolloutPolicy,
    combination: RolloutCombination,
    upgrade_requested: bool,
) -> AdmittedAuthority:
    """Preserve immutable authority on edit/rerun/schedule or upgrade explicitly.

    The recorded plan and realizer are retained unless the caller explicitly
    requests an upgrade to a currently qualified target. Editing a historical
    workflow never silently replaces harness, profile, model, policy, Host
    Class, runtime pack, materializer, or realizer: a changed immutable
    dimension without ``upgrade_requested`` raises.
    """

    if combination_key(combination) == recorded.rollout_combination_key and (
        not upgrade_requested
    ):
        return recorded
    if not upgrade_requested:
        raise _deny(
            "immutable_target_changed_without_explicit_upgrade: editing a "
            "historical workflow must reuse its recorded target or request "
            "an explicit upgrade"
        )
    selection = AuthoringSelection.model_validate(
        {
            "surface": AuthoringSurface.RERUN,
            "combination": combination.model_dump(by_alias=True, mode="json"),
            "explicit": True,
            "ownerCohort": combination.owner_cohort,
        }
    )
    upgraded = admit_authoring_selection(policy=policy, selection=selection)
    return upgraded


def schedule_revision_for_default_change(
    *, current_revision: int, default_changed: bool
) -> int:
    """Return the schedule revision after a default change.

    Changing a schedule's default creates a new schedule revision; unchanged
    defaults pin the recorded target version.
    """

    if current_revision < 1:
        raise _deny("schedule revision must be positive")
    return current_revision + 1 if default_changed else current_revision


def apply_rollback(
    *,
    policy: RolloutPolicy,
    combination_key_value: str,
    target_state: RolloutState,
) -> RolloutPolicy:
    """Roll back one exact combination to a new state in a new generation.

    Rollback changes future admission only: it bumps the policy generation and
    never mutates already-admitted authority. Supported rollback targets stop
    new generic admission, restore an explicitly supported legacy/direct
    default, or disable new Omnigent work without substituting another
    runtime.
    """

    if target_state in {RolloutState.PREFERRED, RolloutState.NEW_WORK_DEFAULT}:
        raise _deny("rollback must not promote; it moves to a less open state")
    entries: list[RolloutEntry] = []
    found = False
    for entry in policy.entries:
        if entry.key == combination_key_value:
            found = True
            entries.append(
                RolloutEntry.model_validate(
                    {**entry.model_dump(by_alias=True, mode="json"), "state": target_state}
                )
            )
        else:
            entries.append(entry)
    if not found:
        raise _deny("rollback_combination_unknown")
    return RolloutPolicy.model_validate(
        {
            "schemaVersion": ROLLOUT_POLICY_VERSION,
            "generation": policy.generation + 1,
            "entries": [item.model_dump(by_alias=True, mode="json") for item in entries],
        }
    )


def migration_status_view(
    *,
    policy: RolloutPolicy,
    evidence_ages: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the operator-visible per-combination migration view.

    The view carries rollout state, generation, default status, exact refs,
    evidence age/expiry, last canary, rollback availability, and compatibility
    status. It never exposes credentials, provider-session ids, raw host
    paths, or internal endpoint authority.
    """

    evidence_ages = evidence_ages or {}
    view: list[dict[str, Any]] = []
    for entry in policy.entries:
        combo = entry.combination.model_dump(by_alias=True, mode="json")
        age = dict(evidence_ages.get(entry.key, {}))
        view.append(
            {
                "rolloutCombinationKey": entry.key,
                "rolloutState": entry.state.value,
                "rolloutGeneration": policy.generation,
                "isDefault": entry.state
                in {RolloutState.PREFERRED, RolloutState.NEW_WORK_DEFAULT},
                "harnessImplementation": combo["harnessImplementation"],
                "agentProfileClass": combo["agentProfileClass"],
                "providerRuntime": combo["providerRuntime"],
                "providerClass": combo["providerClass"],
                "hostClass": combo["hostClass"],
                "runtimePack": combo["runtimePack"],
                "credentialMaterializer": combo["credentialMaterializer"],
                "launchPolicy": combo["launchPolicy"],
                "hostMode": combo["hostMode"],
                "architecture": combo["architecture"],
                "modelConfigClass": combo["modelConfigClass"],
                "executionRealizer": combo["executionRealizer"],
                "evidenceFresh": entry.evidence_fresh,
                "evidenceAge": age.get("age"),
                "evidenceExpiresAt": age.get("expiresAt"),
                "lastCanaryAt": entry.last_canary_at,
                "rollbackAvailable": entry.rollback_available,
                "compatibilityPath": is_compatibility_path(
                    _harness_id_from_implementation(
                        combo["harnessImplementation"]
                    ),
                    combo["executionRealizer"],
                ),
            }
        )
    return view


_ROLLOUT_TELEMETRY: Counter[str] = Counter()


def _telemetry_key(
    *, harness: str, realizer_class: str, decision: str, surface: str
) -> str:
    if harness not in {"codex-native", "claude-native", "opencode-native", "unknown"}:
        harness = "unknown"
    if realizer_class not in {"generic", "legacy", "direct", "unknown"}:
        realizer_class = "unknown"
    if decision not in _LOW_CARD_DECISIONS:
        decision = "denied"
    if surface not in _LOW_CARD_SURFACES:
        surface = "api"
    return "|".join((harness, realizer_class, decision, surface))


def realizer_class_for(realizer_ref: str) -> str:
    if realizer_ref == "generic-omnigent-host@1":
        return "generic"
    if realizer_ref == "direct":
        return "direct"
    return "legacy"


def record_rollout_decision(
    *,
    harness: str,
    realizer_ref: str,
    decision: str,
    surface: AuthoringSurface | str,
) -> None:
    """Record a bounded low-cardinality migration telemetry event.

    Labels exclude user, workflow, session, profile, repository, and
    credential identity.
    """

    surface_value = (
        surface.value if isinstance(surface, AuthoringSurface) else str(surface)
    )
    _ROLLOUT_TELEMETRY[
        _telemetry_key(
            harness=harness,
            realizer_class=realizer_class_for(realizer_ref),
            decision=decision,
            surface=surface_value,
        )
    ] += 1


def get_rollout_telemetry() -> dict[str, int]:
    """Return the current low-cardinality migration counters."""

    return dict(_ROLLOUT_TELEMETRY)


def reset_rollout_telemetry() -> None:
    _ROLLOUT_TELEMETRY.clear()


def admitted_authority_from_plan(plan_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project persisted rollout authority from an execution plan payload."""

    return {
        "rolloutPolicyVersion": plan_payload.get("rolloutPolicyVersion"),
        "rolloutGeneration": plan_payload.get("rolloutGeneration"),
        "rolloutState": plan_payload.get("rolloutState"),
        "executionRealizerRef": plan_payload.get("executionRealizerRef"),
    }


def assert_history_not_reinterpreted(
    *, recorded: Mapping[str, Any], current_policy: RolloutPolicy
) -> None:
    """Prove a current policy change does not reinterpret recorded authority."""

    generation = recorded.get("rolloutGeneration")
    if generation is not None and int(generation) > int(current_policy.generation):
        raise _deny("recorded rollout generation is newer than current policy")


def rollout_evidence_checked_at(*, now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


__all__ = [
    "ROLLOUT_POLICY_VERSION",
    "ROLLOUT_POLICY_ENV",
    "AuthoringSurface",
    "AuthoringSelection",
    "AdmittedAuthority",
    "PRODUCT_INTENTION_HARNESS",
    "TARGET_IDENTITY_LABELS",
    "RolloutAdmissionError",
    "RolloutCombination",
    "RolloutEntry",
    "RolloutPolicy",
    "combination_key",
    "empty_rollout_policy",
    "load_rollout_policy",
    "rollout_state_for",
    "target_identity_label",
    "is_compatibility_path",
    "canonical_runtime_id_for",
    "admit_authoring_selection",
    "resolve_default_target",
    "select_authoring_target",
    "preserve_or_upgrade_target",
    "schedule_revision_for_default_change",
    "apply_rollback",
    "migration_status_view",
    "realizer_class_for",
    "record_rollout_decision",
    "get_rollout_telemetry",
    "reset_rollout_telemetry",
    "admitted_authority_from_plan",
    "assert_history_not_reinterpreted",
    "rollout_evidence_checked_at",
]
