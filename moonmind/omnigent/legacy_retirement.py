"""Code-owned retirement inventory and guard for legacy Omnigent authority paths.

Source issue: MoonLadderStudios/MoonMind#3712.

A legacy Omnigent execution, persistence, routing, or compatibility path may be
deleted only after every retirement criterion that applies to it passes. This
module keeps that inventory in code (not in a mutable architecture document, per
the issue's "Retirement criteria" section) with implementation ownership and a
machine-checkable reference per path, and provides guard helpers that:

* fail when a path is marked retired while an applicable criterion is unmet; and
* fail when a not-yet-retired path's machine-checkable reference no longer
  resolves (a deletion/registry removal must fail CI while the path is still
  required).

It also records the temporary supervisor rollout flags and their retirement
trigger so a rollout flag can never silently become a permanent alternate
architecture.
"""

from __future__ import annotations

import importlib
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

RETIREMENT_CONTRACT_VERSION = "moonmind.omnigent-legacy-retirement/v1"


class RetirementCriterion(str, Enum):
    """The machine-checkable retirement criteria from issue #3712."""

    NO_NEW_RECORDS_USE_IT = "no_new_records_use_it"
    NO_ACTIVE_OR_CLEANUP_USE = "no_active_session_or_cleanup_uses_it"
    ALL_SUPPORTED_HISTORIES_REPLAY = "all_supported_histories_replay"
    DETERMINISTIC_CONFORMANCE_PASSED = "deterministic_conformance_passed"
    EXACT_IMAGE_CONFORMANCE_PASSED = "exact_image_conformance_passed"
    PROTECTED_PROVIDER_CANARY_PASSED = "protected_provider_canary_passed"
    NATIVE_CHAT_ACCEPTANCE_PASSED = "native_chat_acceptance_passed"  # #3642
    CUMULATIVE_REMEDIATION_PASSED = "cumulative_remediation_passed"  # #3480
    BROWSER_TO_HOST_ACCEPTANCE_PASSED = "browser_to_host_acceptance_passed"  # #3508
    ROLLBACK_WITHOUT_PATH_EXERCISED = "rollback_without_path_exercised"
    HISTORICAL_READS_AVAILABLE = "historical_reads_available"
    RETENTION_POLICY_PERMITS_DELETION = "retention_policy_permits_deletion"


# Criteria every legacy path must satisfy before deletion, independent of which
# feature surfaces it touches.
_BASE_CRITERIA: frozenset[RetirementCriterion] = frozenset(
    {
        RetirementCriterion.NO_NEW_RECORDS_USE_IT,
        RetirementCriterion.NO_ACTIVE_OR_CLEANUP_USE,
        RetirementCriterion.ALL_SUPPORTED_HISTORIES_REPLAY,
        RetirementCriterion.DETERMINISTIC_CONFORMANCE_PASSED,
        RetirementCriterion.EXACT_IMAGE_CONFORMANCE_PASSED,
        RetirementCriterion.PROTECTED_PROVIDER_CANARY_PASSED,
        RetirementCriterion.ROLLBACK_WITHOUT_PATH_EXERCISED,
        RetirementCriterion.HISTORICAL_READS_AVAILABLE,
        RetirementCriterion.RETENTION_POLICY_PERMITS_DELETION,
    }
)


class LegacyPathRecord(BaseModel):
    """One retirable legacy Omnigent path with its applicable criteria."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    owner: str
    description: str
    machine_checkable_ref: str = Field(alias="machineCheckableRef")
    applicable_criteria: frozenset[RetirementCriterion] = Field(
        alias="applicableCriteria"
    )
    retired: bool = False


class RetirementDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    allowed: bool
    unmet_criteria: tuple[RetirementCriterion, ...] = Field(alias="unmetCriteria")


class RetirementGuardError(RuntimeError):
    """Raised when a retirement guard invariant is violated."""


# The code-owned inventory. Nothing is retired yet; the migration/canary/replay
# evidence in this cohort has not proven replacement coverage. Machine-checkable
# refs are importable module paths so the guard fails if one is deleted while the
# path is still required.
RETIREMENT_INVENTORY: tuple[LegacyPathRecord, ...] = (
    LegacyPathRecord(
        pathId="omnigent.legacy.bridge_persistence",
        owner="omnigent-control-plane",
        description=(
            "Legacy OmnigentBridgeSession persistence and event index "
            "(overloaded bridge row superseded by the canonical session "
            "aggregate)."
        ),
        machineCheckableRef="moonmind.omnigent.bridge_store",
        applicableCriteria=_BASE_CRITERIA,
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.bridge_execution",
        owner="omnigent-control-plane",
        description="Legacy Omnigent session execution driver.",
        machineCheckableRef="moonmind.omnigent.execute",
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.CUMULATIVE_REMEDIATION_PASSED},
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.profile_bound_execution",
        owner="omnigent-control-plane",
        description="Legacy profile-bound execution coordinator and routing.",
        machineCheckableRef="moonmind.omnigent.profile_bound_execution",
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.BROWSER_TO_HOST_ACCEPTANCE_PASSED},
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.native_ui_compat",
        owner="omnigent-control-plane",
        description="Legacy native chat / Workflow Detail compatibility projection.",
        machineCheckableRef="moonmind.omnigent.native_ui_compat",
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.NATIVE_CHAT_ACCEPTANCE_PASSED},
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.codex_cutover_selection",
        owner="omnigent-control-plane",
        description="Legacy Codex-through-Omnigent cutover runtime selection.",
        machineCheckableRef="moonmind.omnigent.cutover",
        applicableCriteria=_BASE_CRITERIA,
    ),
)


# Temporary rollout flags introduced by this issue. Each maps to the trigger that
# permits its removal. Their presence here is what keeps them from becoming a
# permanent alternate architecture: the retirement test asserts every temporary
# flag has a declared retirement trigger.
LEGACY_RETIREMENT_COMPLETE = "legacy_retirement_complete"
TEMPORARY_ROLLOUT_FLAGS: dict[str, str] = {
    "omnigent_session_supervisor_enabled": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_shadow": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_generation": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_allowed_owner_ids": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_allowed_execution_profile_refs": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_allowed_launch_policy_refs": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_allowed_provider_profile_ids": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_rollback_mode": LEGACY_RETIREMENT_COMPLETE,
}


def evaluate_retirement(
    path: LegacyPathRecord, passed_criteria: frozenset[RetirementCriterion] | set
) -> RetirementDecision:
    """Return whether ``path`` may be retired given the passing criteria."""

    passed = frozenset(passed_criteria)
    unmet = tuple(
        sorted(
            (c for c in path.applicable_criteria if c not in passed),
            key=lambda c: c.value,
        )
    )
    return RetirementDecision(
        pathId=path.path_id, allowed=not unmet, unmetCriteria=unmet
    )


def _ref_resolves(ref: str) -> bool:
    """Whether a machine-checkable module reference still imports."""

    try:
        importlib.import_module(ref)
        return True
    except Exception:  # noqa: BLE001 - any import failure means the ref is gone
        return False


def assert_retirement_guard(
    inventory: tuple[LegacyPathRecord, ...] = RETIREMENT_INVENTORY,
    *,
    passed_by_path: dict[str, frozenset[RetirementCriterion]] | None = None,
) -> None:
    """Enforce the two retirement-guard invariants.

    * A path marked ``retired`` must have every applicable criterion passing.
    * A not-yet-retired path's machine-checkable reference must still resolve.

    Raises :class:`RetirementGuardError` on violation.
    """

    passed_by_path = passed_by_path or {}
    for path in inventory:
        if path.retired:
            decision = evaluate_retirement(
                path, passed_by_path.get(path.path_id, frozenset())
            )
            if not decision.allowed:
                raise RetirementGuardError(
                    f"legacy path {path.path_id!r} is marked retired but has "
                    f"unmet criteria: {[c.value for c in decision.unmet_criteria]}"
                )
        elif not _ref_resolves(path.machine_checkable_ref):
            raise RetirementGuardError(
                f"legacy path {path.path_id!r} is still required but its "
                f"machine-checkable reference {path.machine_checkable_ref!r} no "
                "longer resolves"
            )


def assert_temporary_flags_have_retirement(
    flags: dict[str, str] = TEMPORARY_ROLLOUT_FLAGS,
) -> None:
    """Every temporary rollout flag must declare a non-empty retirement trigger."""

    for flag, trigger in flags.items():
        if not str(trigger or "").strip():
            raise RetirementGuardError(
                f"temporary rollout flag {flag!r} has no retirement trigger; it "
                "must not become a permanent alternate architecture"
            )


__all__ = [
    "RETIREMENT_CONTRACT_VERSION",
    "RetirementCriterion",
    "LegacyPathRecord",
    "RetirementDecision",
    "RetirementGuardError",
    "RETIREMENT_INVENTORY",
    "TEMPORARY_ROLLOUT_FLAGS",
    "LEGACY_RETIREMENT_COMPLETE",
    "evaluate_retirement",
    "assert_retirement_guard",
    "assert_temporary_flags_have_retirement",
]
