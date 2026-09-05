"""Code-owned retirement inventory and guard for legacy Omnigent authority paths.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835.

A legacy Omnigent execution, persistence, routing, startup, configuration, or
compatibility path may be deleted only after every retirement criterion that
applies to it passes. This module keeps that inventory in code (not in a mutable
architecture document) with implementation ownership and a machine-checkable
reference per path, and provides guard helpers that:

* fail when a path is marked removed while an applicable criterion is unmet;
* fail when a retained path's machine-checkable surface no longer resolves (a
  deletion/registry removal must fail CI while the path is still required);
* fail when the repository contains a legacy surface with no retirement row; and
* refuse removal eligibility while an active owner, replay, historical-read,
  rollback, or retention dependency remains.

#3835 adds the retirement *class* to every row. Classification is what makes the
staged convergence machine-checkable instead of a document checklist: the class
decides whether new admission is still allowed, the earliest removal stage the
row can be included in, and which dependencies must drain first.

It also records the temporary supervisor rollout flags and their retirement
trigger so a rollout flag can never silently become a permanent alternate
architecture.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.retirement_surfaces import (
    discover_legacy_surfaces,
    parse_surface_ref,
    surface_exists,
)
from moonmind.omnigent.workflow_chat_acceptance import (
    validate_workflow_chat_acceptance_manifest,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle: rollback imports this module
    from moonmind.omnigent.session_supervisor_rollback import (
        RollbackExerciseDecision,
    )

RETIREMENT_CONTRACT_VERSION = "moonmind.omnigent-legacy-retirement/v2"


class RetirementClass(str, Enum):
    """The ten mutually-exclusive retirement classes from issue #3835.

    The order is the staged convergence order. Everything at or after
    :data:`NEW_ADMISSION_DISABLED` refuses new plan admission;
    :data:`ROLLBACK_ONLY` admits new work only under an explicitly permitted
    rollback generation.
    """

    ACTIVE_PRODUCT_PATH = "active_product_path"
    NEW_ADMISSION_DISABLED = "new_admission_disabled"
    ROLLBACK_ONLY = "rollback_only"
    ACTIVE_EXECUTION_SUPPORT = "active_execution_support"
    CLEANUP_ONLY = "cleanup_only"
    TEMPORAL_REPLAY_ONLY = "temporal_replay_only"
    HISTORICAL_READ_ONLY = "historical_read_only"
    MIGRATION_TOOL = "migration_tool"
    ELIGIBLE_FOR_REMOVAL = "eligible_for_removal"
    REMOVED = "removed"


# Classes that still admit new work. Everything else must not appear as the
# target of a newly compiled execution plan.
_NEW_ADMISSION_CLASSES: frozenset[RetirementClass] = frozenset(
    {RetirementClass.ACTIVE_PRODUCT_PATH, RetirementClass.ROLLBACK_ONLY}
)


class RemovalStage(IntEnum):
    """The ordered removal stages from issue #3835 required work section 7.

    A removal PR names one stage. A row may only be included in a removal at or
    after its ``earliest_removal_stage``, which is what keeps a historical
    reader from being deleted in the same change as a product selector.
    """

    PRODUCT_SELECTORS = 1
    NEW_WRITE_API_PATHS = 2
    COMPOSITION_ROOT_REGISTRATIONS = 3
    STARTUP_AND_COMPOSE = 4
    IMAGE_AND_ENVIRONMENT_ALIASES = 5
    OAUTH_HOST_ORCHESTRATION = 6
    LAUNCH_ONLY_CODE = 7
    REPLAY_WRAPPERS = 8
    HISTORICAL_READERS = 9


class ComponentFamily(str, Enum):
    """The component families issue #3835 required work section 1 enumerates."""

    DIRECT_CODEX_LAUNCH_AND_SESSION = "direct_codex_launch_and_session"
    DIRECT_CLAUDE_LAUNCH_AND_SESSION = "direct_claude_launch_and_session"
    PROFILE_BOUND_REALIZER = "profile_bound_realizer"
    PROFILE_BOUND_EXECUTION = "profile_bound_execution"
    OAUTH_HOST_RUNTIME = "oauth_host_runtime"
    STARTUP_AND_COMPOSE = "startup_and_compose"
    HOST_CLASS_POLICY_AND_IMAGE_REFS = "host_class_policy_and_image_refs"
    ENVIRONMENT_AND_PERSISTED_BOOTSTRAP = "environment_and_persisted_bootstrap"
    BRIDGE_COMPATIBILITY = "bridge_compatibility"
    PROVIDER_PROFILE_CAPACITY = "provider_profile_capacity"
    CHECKPOINT_REMEDIATION_PUBLICATION_CLEANUP = (
        "checkpoint_remediation_publication_cleanup"
    )
    API_AND_UI_SELECTION = "api_and_ui_selection"
    TEMPORAL_REGISTRATIONS = "temporal_registrations"
    PATCH_AND_WORKER_VERSION_BRANCHES = "patch_and_worker_version_branches"
    SERIALIZED_SCHEMA_AND_ROWS = "serialized_schema_and_rows"
    FIXTURES_MATRICES_AND_RUNBOOKS = "fixtures_matrices_and_runbooks"


class RuntimeGeneration(str, Enum):
    """Which implementation generation owns a row.

    Issue #3835 required work section 9 forbids forcing simultaneous removal of
    direct Codex and direct Claude, so the retirement decision is evaluated per
    generation rather than for "legacy" as a whole.
    """

    DIRECT_CODEX = "direct_codex"
    DIRECT_CLAUDE = "direct_claude"
    CODEX_PROFILE_BOUND = "codex_profile_bound"
    SHARED_LEGACY_SUBSTRATE = "shared_legacy_substrate"


class ActiveOwnerKind(str, Enum):
    """The active-owner classes that must drain (section 3)."""

    TEMPORAL_WORKFLOW = "temporal_workflow"
    AGENT_RUN_OR_OMNIGENT_SESSION = "agent_run_or_omnigent_session"
    PROVIDER_PROFILE_LEASE = "provider_profile_lease"
    HOST_BINDING_OR_LEASE = "host_binding_or_lease"
    CREDENTIAL_CONSUMER = "credential_consumer"
    STATIC_OR_ON_DEMAND_HOST = "static_or_on_demand_host"
    PENDING_PUBLICATION = "pending_publication"
    PENDING_CHECKPOINT_OR_REMEDIATION = "pending_checkpoint_or_remediation"
    INCOMPLETE_CLEANUP_OR_JANITOR = "incomplete_cleanup_or_janitor"


class RetirementCriterion(str, Enum):
    """The machine-checkable retirement criteria from issues #3712 and #3835."""

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


class RetirementGuardError(RuntimeError):
    """Raised when a retirement guard invariant is violated."""


class LegacyAdmissionRejected(RuntimeError):
    """Raised when new work selects a path whose class no longer admits it."""

    def __init__(self, message: str, *, path_id: str, reason_code: str) -> None:
        super().__init__(message)
        self.path_id = path_id
        self.reason_code = reason_code


class LegacyPathRecord(BaseModel):
    """One retirable legacy component with its class, dependencies, and evidence.

    Every field #3835 required work section 1 demands per item is recorded here:
    ``owner``, ``retirement_class``, ``new_admission_source``,
    ``active_resource_dependencies``, ``replay_dependency`` /
    ``historical_read_dependency``, ``rollback_dependency``,
    ``applicable_criteria`` (required evidence), ``earliest_removal_stage``, and
    ``removal_guard_test``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    owner: str
    description: str
    family: ComponentFamily
    generation: RuntimeGeneration
    retirement_class: RetirementClass = Field(alias="retirementClass")
    machine_checkable_ref: str = Field(alias="machineCheckableRef")
    surfaces: tuple[str, ...] = Field(default=(), alias="surfaces")
    # The concrete code path that can still create new records or admit new work
    # through this component. Empty exactly when the class no longer admits.
    new_admission_source: str = Field("", alias="newAdmissionSource")
    active_resource_dependencies: frozenset[ActiveOwnerKind] = Field(
        default_factory=frozenset, alias="activeResourceDependencies"
    )
    replay_dependency: bool = Field(False, alias="replayDependency")
    historical_read_dependency: bool = Field(False, alias="historicalReadDependency")
    rollback_dependency: bool = Field(False, alias="rollbackDependency")
    # Exact rollback generations permitted to re-admit new work through this
    # path. Exact membership only — never a prefix or substring match.
    rollback_generations: frozenset[str] = Field(
        default_factory=frozenset, alias="rollbackGenerations"
    )
    applicable_criteria: frozenset[RetirementCriterion] = Field(
        alias="applicableCriteria"
    )
    earliest_removal_stage: RemovalStage = Field(alias="earliestRemovalStage")
    removal_guard_test: str = Field(alias="removalGuardTest")

    @property
    def removed(self) -> bool:
        """Whether the implementation is gone (the terminal retirement class)."""

        return self.retirement_class is RetirementClass.REMOVED

    @property
    def admits_new_work(self) -> bool:
        return self.retirement_class in _NEW_ADMISSION_CLASSES

    @property
    def requires_rollback_generation(self) -> bool:
        """Whether admitting new work needs an explicit rollback generation."""

        return self.retirement_class is RetirementClass.ROLLBACK_ONLY

    @model_validator(mode="after")
    def _validate(self) -> "LegacyPathRecord":
        for ref in (self.machine_checkable_ref, *self.surfaces):
            parse_surface_ref(ref)
        if self.machine_checkable_ref not in self.surfaces:
            raise ValueError(
                f"{self.path_id}: surfaces must include the machine-checkable ref"
            )
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError(f"{self.path_id}: duplicate surface refs")
        if not self.removal_guard_test.strip():
            raise ValueError(f"{self.path_id}: removal_guard_test is required")

        admits = self.admits_new_work
        if admits and not self.new_admission_source.strip():
            raise ValueError(
                f"{self.path_id}: class {self.retirement_class.value!r} still "
                "admits new work, so its new-admission source must be named"
            )
        if not admits and self.new_admission_source.strip():
            raise ValueError(
                f"{self.path_id}: class {self.retirement_class.value!r} does not "
                "admit new work, so it must not name a new-admission source"
            )
        if (
            self.retirement_class is RetirementClass.ROLLBACK_ONLY
            and not self.rollback_generations
        ):
            raise ValueError(
                f"{self.path_id}: rollback_only requires an exact permitted "
                "rollback generation allowlist"
            )
        if self.rollback_generations and not self.rollback_dependency:
            raise ValueError(
                f"{self.path_id}: permitted rollback generations imply a rollback "
                "dependency"
            )
        if (
            self.retirement_class is RetirementClass.TEMPORAL_REPLAY_ONLY
            and not self.replay_dependency
        ):
            raise ValueError(
                f"{self.path_id}: temporal_replay_only requires a replay dependency"
            )
        if (
            self.retirement_class is RetirementClass.HISTORICAL_READ_ONLY
            and not self.historical_read_dependency
        ):
            raise ValueError(
                f"{self.path_id}: historical_read_only requires a historical-read "
                "dependency"
            )
        if (
            self.retirement_class is RetirementClass.CLEANUP_ONLY
            and ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR
            not in self.active_resource_dependencies
        ):
            raise ValueError(
                f"{self.path_id}: cleanup_only requires an incomplete-cleanup "
                "active dependency"
            )
        if self.retirement_class in (
            RetirementClass.ELIGIBLE_FOR_REMOVAL,
            RetirementClass.REMOVED,
        ):
            if (
                self.active_resource_dependencies
                or self.replay_dependency
                or self.historical_read_dependency
                or self.rollback_dependency
            ):
                raise ValueError(
                    f"{self.path_id}: class {self.retirement_class.value!r} cannot "
                    "carry an active, replay, historical-read, or rollback "
                    "dependency"
                )
        return self


class RetirementDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    allowed: bool
    unmet_criteria: tuple[RetirementCriterion, ...] = Field(alias="unmetCriteria")


class LegacyAdmissionDecision(BaseModel):
    """Whether new work may still be admitted through one legacy path."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    allowed: bool
    reason_code: str = Field(alias="reasonCode")
    retirement_class: RetirementClass = Field(alias="retirementClass")
    rollback_generation: str | None = Field(None, alias="rollbackGeneration")
    contract_version: str = Field(
        RETIREMENT_CONTRACT_VERSION, alias="contractVersion"
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class RemovalEligibility(BaseModel):
    """Whether one row may be included in a removal at a given stage."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    stage: RemovalStage
    eligible: bool
    blockers: tuple[str, ...]
    unmet_criteria: tuple[RetirementCriterion, ...] = Field(alias="unmetCriteria")
    contract_version: str = Field(
        RETIREMENT_CONTRACT_VERSION, alias="contractVersion"
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class RetentionWindows(BaseModel):
    """Whether the retention windows a row may depend on are still open.

    Fail-closed: the defaults keep every window open, so a caller that forgets to
    supply evidence never accidentally makes a row removal-eligible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    replay_window_open: bool = Field(True, alias="replayWindowOpen")
    historical_read_window_open: bool = Field(True, alias="historicalReadWindowOpen")
    rollback_window_open: bool = Field(True, alias="rollbackWindowOpen")
    rollback_exercise_recorded: bool = Field(False, alias="rollbackExerciseRecorded")


# The code-owned inventory. Nothing is removed yet; the migration/canary/replay
# evidence in this cohort has not proven replacement coverage, and #3833 has not
# promoted the qualified generic rows. Machine-checkable refs name a concrete
# surface (a ``python:module:symbol``, Compose service, startup script, image
# variable, or realizer identity) so deleting the still-required implementation
# fails the guard even when an empty stub is left behind.
RETIREMENT_INVENTORY: tuple[LegacyPathRecord, ...] = (
    # ---------------------------------------------------------------- direct Codex
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_codex_launch",
        owner="omnigent-runtime-plane",
        description=(
            "Direct Codex managed launch strategy: the runtime the workflow "
            "launcher selects for the ``codex_cli`` runtime id."
        ),
        family=ComponentFamily.DIRECT_CODEX_LAUNCH_AND_SESSION,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.runtime.strategies.codex_cli:"
            "CodexCliStrategy"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.strategies.codex_cli:"
            "CodexCliStrategy",
            "runtime-strategy:codex_cli",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies:RUNTIME_STRATEGIES"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.CREDENTIAL_CONSUMER,
                ActiveOwnerKind.PENDING_PUBLICATION,
                ActiveOwnerKind.PENDING_CHECKPOINT_OR_REMEDIATION,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        historicalReadDependency=True,
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.PRODUCT_SELECTORS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_direct_and_profile_bound_generations_are_independently_decided"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_codex_session_runtime",
        owner="omnigent-runtime-plane",
        description="Direct Codex managed session runtime and app-server client.",
        family=ComponentFamily.DIRECT_CODEX_LAUNCH_AND_SESSION,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.runtime.codex_session_runtime:"
            "CodexManagedSessionRuntime"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.codex_session_runtime:"
            "CodexManagedSessionRuntime",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies.codex_cli:CodexCliStrategy"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.PENDING_CHECKPOINT_OR_REMEDIATION,
            }
        ),
        replayDependency=True,
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.LAUNCH_ONLY_CODE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_codex_session_adapter",
        owner="omnigent-runtime-plane",
        description="Direct Codex session adapter used by the managed launcher.",
        family=ComponentFamily.DIRECT_CODEX_LAUNCH_AND_SESSION,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.workflows.adapters.codex_session_adapter:"
            "CodexSessionAdapter"
        ),
        surfaces=(
            "python:moonmind.workflows.adapters.codex_session_adapter:"
            "CodexSessionAdapter",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies.codex_cli:CodexCliStrategy"
        ),
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION}
        ),
        replayDependency=True,
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.LAUNCH_ONLY_CODE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_codex_bridge_compat",
        owner="omnigent-control-plane",
        description=(
            "Direct Codex compatibility bridge producer that stamps "
            "``codex_direct_compat`` provenance onto persisted session events."
        ),
        family=ComponentFamily.BRIDGE_COMPATIBILITY,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.activity_runtime:"
            "TemporalAgentRuntimeActivities"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.activity_runtime:"
            "TemporalAgentRuntimeActivities",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies.codex_cli:CodexCliStrategy"
        ),
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION}
        ),
        replayDependency=True,
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.HISTORICAL_READERS,
        removalGuardTest=(
            "tests/unit/omnigent/test_direct_compat_historical_reads.py::"
            "test_persisted_direct_compat_session_reads_without_live_runtime"
        ),
    ),
    # --------------------------------------------------------------- direct Claude
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_claude_launch",
        owner="omnigent-runtime-plane",
        description=(
            "Direct Claude Code managed launch strategy and session ownership "
            "for the ``claude_code`` runtime id."
        ),
        family=ComponentFamily.DIRECT_CLAUDE_LAUNCH_AND_SESSION,
        generation=RuntimeGeneration.DIRECT_CLAUDE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.runtime.strategies.claude_code:"
            "ClaudeCodeStrategy"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.strategies.claude_code:"
            "ClaudeCodeStrategy",
            "runtime-strategy:claude_code",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies:RUNTIME_STRATEGIES"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.CREDENTIAL_CONSUMER,
                ActiveOwnerKind.PENDING_PUBLICATION,
                ActiveOwnerKind.PENDING_CHECKPOINT_OR_REMEDIATION,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        historicalReadDependency=True,
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.PRODUCT_SELECTORS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_direct_and_profile_bound_generations_are_independently_decided"
        ),
    ),
    # -------------------------------------------------------- Codex profile-bound
    LegacyPathRecord(
        pathId="omnigent.legacy.profile_bound_realizer",
        owner="omnigent-control-plane",
        description=(
            "``codex-profile-bound@1`` execution realizer registration and "
            "trusted planner selection."
        ),
        family=ComponentFamily.PROFILE_BOUND_REALIZER,
        generation=RuntimeGeneration.CODEX_PROFILE_BOUND,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.realizers.codex_profile_bound:"
            "CodexProfileBoundRealizer"
        ),
        surfaces=(
            "python:moonmind.omnigent.realizers.codex_profile_bound:"
            "CodexProfileBoundRealizer",
            "realizer:codex-profile-bound@1",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.planner:select_execution_realizer"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.PROVIDER_PROFILE_LEASE,
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
                ActiveOwnerKind.PENDING_PUBLICATION,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        historicalReadDependency=True,
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.PRODUCT_SELECTORS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_profile_bound_admission_follows_the_code_owned_class"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.profile_bound_execution",
        owner="omnigent-control-plane",
        description="Legacy profile-bound execution coordinator and routing.",
        family=ComponentFamily.PROFILE_BOUND_EXECUTION,
        generation=RuntimeGeneration.CODEX_PROFILE_BOUND,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.profile_bound_execution:"
            "OmnigentProfileBoundExecutionCoordinator"
        ),
        surfaces=(
            "python:moonmind.omnigent.profile_bound_execution:"
            "OmnigentProfileBoundExecutionCoordinator",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.realizers.codex_profile_bound:CodexProfileBoundRealizer"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.PROVIDER_PROFILE_LEASE,
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        historicalReadDependency=True,
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.BROWSER_TO_HOST_ACCEPTANCE_PASSED},
        earliestRemovalStage=RemovalStage.LAUNCH_ONLY_CODE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_blocked_while_active_leases_or_hosts_remain"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.oauth_host_runtime",
        owner="omnigent-runtime-plane",
        description=(
            "Legacy OAuth host runtime that assembles Codex host launch, mount, "
            "credential-volume, and egress attestation argument vectors."
        ),
        family=ComponentFamily.OAUTH_HOST_RUNTIME,
        generation=RuntimeGeneration.CODEX_PROFILE_BOUND,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.oauth_host_runtime:OmnigentOAuthHostRuntime"
        ),
        surfaces=(
            "python:moonmind.omnigent.oauth_host_runtime:OmnigentOAuthHostRuntime",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.profile_bound_execution:"
            "OmnigentProfileBoundExecutionCoordinator"
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
                ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST,
                ActiveOwnerKind.CREDENTIAL_CONSUMER,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.OAUTH_HOST_ORCHESTRATION,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_blocked_while_active_leases_or_hosts_remain"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.oauth_host_janitor",
        owner="omnigent-runtime-plane",
        description=(
            "Reclamation authority for legacy OAuth host containers, volumes, "
            "and leases. Owns cleanup after new admission stops."
        ),
        family=ComponentFamily.CHECKPOINT_REMEDIATION_PUBLICATION_CLEANUP,
        generation=RuntimeGeneration.CODEX_PROFILE_BOUND,
        retirementClass=RetirementClass.CLEANUP_ONLY,
        machineCheckableRef=(
            "python:moonmind.omnigent.oauth_host_janitor:OmnigentOAuthHostJanitor"
        ),
        surfaces=(
            "python:moonmind.omnigent.oauth_host_janitor:OmnigentOAuthHostJanitor",
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
                ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST,
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
            }
        ),
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.OAUTH_HOST_ORCHESTRATION,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_cleanup_only_path_blocks_removal_until_janitor_authority_drains"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.oauth_session_activities",
        owner="omnigent-control-plane",
        description=(
            "Temporal Activity registrations that stage, revalidate, and clean "
            "up legacy OAuth host sessions."
        ),
        family=ComponentFamily.TEMPORAL_REGISTRATIONS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_EXECUTION_SUPPORT,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.activities.oauth_session_cleanup:"
            "oauth_session_cleanup_stale"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.activities.oauth_session_cleanup:"
            "oauth_session_cleanup_stale",
            "python:moonmind.workflows.temporal.activities.oauth_session_activities:"
            "oauth_session_register_profile",
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
                ActiveOwnerKind.CREDENTIAL_CONSUMER,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        replayDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.COMPOSITION_ROOT_REGISTRATIONS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_blocked_while_active_leases_or_hosts_remain"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.provider_profile_capacity_consumer",
        owner="omnigent-control-plane",
        description=(
            "Provider Profile capacity/lease consumer shared by the legacy "
            "profile-bound coordinator and the generic realizer."
        ),
        family=ComponentFamily.PROVIDER_PROFILE_CAPACITY,
        generation=RuntimeGeneration.CODEX_PROFILE_BOUND,
        retirementClass=RetirementClass.ACTIVE_EXECUTION_SUPPORT,
        machineCheckableRef=(
            "python:moonmind.omnigent.provider_leases:"
            "OmnigentProviderLeaseCoordinator"
        ),
        surfaces=(
            "python:moonmind.omnigent.provider_leases:"
            "OmnigentProviderLeaseCoordinator",
        ),
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.PROVIDER_PROFILE_LEASE,
                ActiveOwnerKind.CREDENTIAL_CONSUMER,
                ActiveOwnerKind.INCOMPLETE_CLEANUP_OR_JANITOR,
            }
        ),
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.COMPOSITION_ROOT_REGISTRATIONS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_blocked_while_active_leases_or_hosts_remain"
        ),
    ),
    # ------------------------------------------------------- startup and Compose
    LegacyPathRecord(
        pathId="omnigent.legacy.codex_static_host_startup",
        owner="omnigent-deployment",
        description=(
            "Codex-specific static host startup, health, and init scripts kept "
            "as thin wrappers over the consolidated generic entrypoint (#3834)."
        ),
        family=ComponentFamily.STARTUP_AND_COMPOSE,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef="script:start-codex-oauth-host.sh",
        surfaces=(
            "script:start-codex-oauth-host.sh",
            "script:check-codex-oauth-host.sh",
            "script:init-codex-oauth-host.sh",
            "compose-service:omnigent-host-codex",
            "compose-service:omnigent-host-codex-init",
            "compose-profile:omnigent-host-codex",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host-codex",
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST,
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
            }
        ),
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.STARTUP_AND_COMPOSE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.claude_static_host_startup",
        owner="omnigent-deployment",
        description=(
            "Claude-specific static host startup and health scripts kept as thin "
            "wrappers over the consolidated generic entrypoint (#3834)."
        ),
        family=ComponentFamily.STARTUP_AND_COMPOSE,
        generation=RuntimeGeneration.DIRECT_CLAUDE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef="script:start-claude-oauth-host.sh",
        surfaces=(
            "script:start-claude-oauth-host.sh",
            "script:check-claude-oauth-host.sh",
            "compose-service:omnigent-host-claude",
            "compose-service:omnigent-host-claude-init",
            "compose-profile:omnigent-host-claude",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host-claude",
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST,
                ActiveOwnerKind.HOST_BINDING_OR_LEASE,
            }
        ),
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.STARTUP_AND_COMPOSE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.projection_host_startup",
        owner="omnigent-deployment",
        description=(
            "Pre-consolidation ``omnigent-host`` Compose service, its "
            "runner-projection entrypoint, and health script. Still resolves the "
            "legacy image variable rather than the shared-image expression."
        ),
        family=ComponentFamily.STARTUP_AND_COMPOSE,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef="script:start-host-with-projections.sh",
        surfaces=(
            "script:start-host-with-projections.sh",
            "script:check-runner-projections.sh",
            "compose-service:omnigent-host",
            "compose-profile:omnigent-host",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host",
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST}
        ),
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.STARTUP_AND_COMPOSE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    # ------------------------------------------ image and environment identities
    LegacyPathRecord(
        pathId="omnigent.legacy.host_image_variable_alias",
        owner="omnigent-deployment",
        description=(
            "Legacy generic host image variables honored as a bounded alias when "
            "the canonical shared-image variable is unset."
        ),
        family=ComponentFamily.HOST_CLASS_POLICY_AND_IMAGE_REFS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.harness_platform.static_hosts:"
            "LEGACY_HOST_IMAGE_ENV"
        ),
        surfaces=(
            "python:moonmind.omnigent.harness_platform.static_hosts:"
            "LEGACY_HOST_IMAGE_ENV",
            "env:OMNIGENT_HOST_IMAGE_REF",
            "env:OMNIGENT_HOST_IMAGE",
            "env:OMNIGENT_HOST_IMAGE_TAG",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.static_hosts:"
            "resolve_static_host_image_ref"
        ),
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.IMAGE_AND_ENVIRONMENT_ALIASES,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_obsolete_configuration_fails_with_an_actionable_message"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.opencode_host_image_alias",
        owner="omnigent-deployment",
        description=(
            "OpenCode-specific shared-image variable and the "
            "``omnigent-host-opencode`` image alias it names."
        ),
        family=ComponentFamily.HOST_CLASS_POLICY_AND_IMAGE_REFS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_OPENCODE_HOST_IMAGE_ENV"
        ),
        surfaces=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_OPENCODE_HOST_IMAGE_ENV",
            "env:OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.host_classes:"
            "get_opencode_host_image_ref"
        ),
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST}
        ),
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.IMAGE_AND_ENVIRONMENT_ALIASES,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_obsolete_configuration_fails_with_an_actionable_message"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.pi_host_image_alias",
        owner="omnigent-deployment",
        description="Per-provider Pi host image variable predating the shared image.",
        family=ComponentFamily.HOST_CLASS_POLICY_AND_IMAGE_REFS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_PI_HOST_IMAGE_ENV"
        ),
        surfaces=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_PI_HOST_IMAGE_ENV",
            "env:OMNIGENT_PI_HOST_IMAGE_REF",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.host_classes:get_pi_host_image_ref"
        ),
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.STATIC_OR_ON_DEMAND_HOST}
        ),
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.IMAGE_AND_ENVIRONMENT_ALIASES,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_obsolete_configuration_fails_with_an_actionable_message"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.persisted_bootstrap_image_fields",
        owner="omnigent-deployment",
        description=(
            "Persisted bootstrap state fields that carry the per-provider host "
            "image refs resolved before the shared-image authority existed."
        ),
        family=ComponentFamily.ENVIRONMENT_AND_PERSISTED_BOOTSTRAP,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.bootstrap.models:"
            "ResolvedOmnigentDeploymentState"
        ),
        surfaces=(
            "python:moonmind.omnigent.bootstrap.models:"
            "ResolvedOmnigentDeploymentState",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.bootstrap.image_resolution:"
            "publish_resolved_omnigent_images"
        ),
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.IMAGE_AND_ENVIRONMENT_ALIASES,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    # -------------------------------------------------------- bridge and UI compat
    LegacyPathRecord(
        pathId="omnigent.legacy.bridge_persistence",
        owner="omnigent-control-plane",
        description=(
            "Legacy OmnigentBridgeSession persistence and event index "
            "(overloaded bridge row superseded by the canonical session "
            "aggregate)."
        ),
        family=ComponentFamily.SERIALIZED_SCHEMA_AND_ROWS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore"
        ),
        surfaces=(
            "python:moonmind.omnigent.bridge_store:OmnigentBridgeSessionStore",
        ),
        newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
        activeResourceDependencies=frozenset(
            {ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION}
        ),
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.HISTORICAL_READERS,
        removalGuardTest=(
            "tests/unit/omnigent/test_direct_compat_historical_reads.py::"
            "test_persisted_direct_compat_session_reads_without_live_runtime"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.bridge_execution",
        owner="omnigent-control-plane",
        description="Legacy Omnigent session execution driver.",
        family=ComponentFamily.BRIDGE_COMPATIBILITY,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef="python:moonmind.omnigent.execute:run_omnigent_execution",
        surfaces=("python:moonmind.omnigent.execute:run_omnigent_execution",),
        newAdmissionSource="moonmind.omnigent.execute:run_omnigent_execution",
        activeResourceDependencies=frozenset(
            {
                ActiveOwnerKind.AGENT_RUN_OR_OMNIGENT_SESSION,
                ActiveOwnerKind.TEMPORAL_WORKFLOW,
            }
        ),
        replayDependency=True,
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.CUMULATIVE_REMEDIATION_PASSED},
        earliestRemovalStage=RemovalStage.NEW_WRITE_API_PATHS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_blocked_while_active_leases_or_hosts_remain"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.native_ui_compat",
        owner="omnigent-control-plane",
        description="Legacy native chat / Workflow Detail compatibility projection.",
        family=ComponentFamily.API_AND_UI_SELECTION,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.native_ui_compat:classify_native_ui_http"
        ),
        surfaces=(
            "python:moonmind.omnigent.native_ui_compat:classify_native_ui_http",
        ),
        newAdmissionSource=(
            "api_service.api.routers.omnigent_bridge:_bridge_event_payload"
        ),
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA
        | {RetirementCriterion.NATIVE_CHAT_ACCEPTANCE_PASSED},
        earliestRemovalStage=RemovalStage.HISTORICAL_READERS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_generation_historical_reads.py::"
            "test_no_generation_is_relabeled_as_generic_on_read"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.codex_cutover_selection",
        owner="omnigent-control-plane",
        description="Legacy Codex-through-Omnigent cutover runtime selection.",
        family=ComponentFamily.API_AND_UI_SELECTION,
        generation=RuntimeGeneration.DIRECT_CODEX,
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        machineCheckableRef=(
            "python:moonmind.omnigent.cutover:validate_matrix_artifact"
        ),
        surfaces=("python:moonmind.omnigent.cutover:validate_matrix_artifact",),
        newAdmissionSource="moonmind.omnigent.cutover:select_runtime",
        rollbackDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.PRODUCT_SELECTORS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_direct_and_profile_bound_generations_are_independently_decided"
        ),
    ),
    # ------------------------------------------------------- migration tooling
    LegacyPathRecord(
        pathId="omnigent.legacy.session_migration_inventory",
        owner="omnigent-control-plane",
        description=(
            "Migration inventory that classifies persisted legacy session rows "
            "for the cutover. Exists only to move records, never to run work."
        ),
        family=ComponentFamily.SERIALIZED_SCHEMA_AND_ROWS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.MIGRATION_TOOL,
        machineCheckableRef=(
            "python:moonmind.omnigent.session_migration_inventory:classify_record"
        ),
        surfaces=(
            "python:moonmind.omnigent.session_migration_inventory:classify_record",
        ),
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.HISTORICAL_READERS,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_removal_stage_ordering_is_enforced"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.managed_session_replay_patches",
        owner="omnigent-control-plane",
        description=(
            "Versioned workflow patch branches that keep direct managed-session "
            "checkpoint and remediation-continuation histories replayable on the "
            "target worker build."
        ),
        family=ComponentFamily.PATCH_AND_WORKER_VERSION_BRANCHES,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.TEMPORAL_REPLAY_ONLY,
        machineCheckableRef=(
            "python:moonmind.workflows.temporal.workflows.run:"
            "RUN_REMEDIATION_CONTINUE_MANAGED_SESSION_PATCH"
        ),
        surfaces=(
            "python:moonmind.workflows.temporal.workflows.run:"
            "RUN_REMEDIATION_CONTINUE_MANAGED_SESSION_PATCH",
            "python:moonmind.workflows.temporal.workflows.run:"
            "RUN_MANAGED_SESSION_CHECKPOINT_LOCATOR_PATCH",
        ),
        replayDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.REPLAY_WRAPPERS,
        removalGuardTest=(
            "tests/unit/workflows/temporal/test_legacy_generation_replay.py::"
            "test_direct_and_profile_bound_generation_histories_replay"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.static_host_startup_runbook",
        owner="omnigent-deployment",
        description=(
            "Static-host startup consolidation runbook and rollback procedure "
            "handed off by #3834; superseded by this code-owned inventory."
        ),
        family=ComponentFamily.FIXTURES_MATRICES_AND_RUNBOOKS,
        generation=RuntimeGeneration.SHARED_LEGACY_SUBSTRATE,
        retirementClass=RetirementClass.HISTORICAL_READ_ONLY,
        machineCheckableRef=(
            "file:services/omnigent/scripts/STATIC_HOST_STARTUP_INVENTORY.md"
        ),
        surfaces=(
            "file:services/omnigent/scripts/STATIC_HOST_STARTUP_INVENTORY.md",
        ),
        historicalReadDependency=True,
        applicableCriteria=_BASE_CRITERIA,
        earliestRemovalStage=RemovalStage.STARTUP_AND_COMPOSE,
        removalGuardTest=(
            "tests/unit/omnigent/test_legacy_retirement.py::"
            "test_every_inventory_surface_still_resolves"
        ),
    ),
)


class ObsoleteConfiguration(BaseModel):
    """One configuration identity that is on its way out of the product.

    Issue #3835 required work section 10 requires an *actionable* startup failure
    for obsolete configuration during its deprecation window rather than silently
    ignoring it, and outright rejection afterwards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    variable: str
    retirement_path_id: str = Field(alias="retirementPathId")
    replacement: str
    # False while the variable is still honored (nothing to warn about yet).
    # True once the deployment must stop supplying it.
    deprecated: bool = False
    removed: bool = False
    guidance: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "ObsoleteConfiguration":
        if self.removed and not self.deprecated:
            raise ValueError(
                f"{self.variable}: a removed variable must pass through its "
                "deprecation window first"
            )
        if self.deprecated and not self.guidance.strip():
            raise ValueError(
                f"{self.variable}: a deprecated variable needs actionable guidance"
            )
        return self


# Every legacy configuration identity that will produce an actionable startup
# failure once its owning retirement row stops admitting new work. Nothing is
# deprecated yet: the aliases below are still the supported way to pin a prior
# image while the rollback window is open (#3833 has not promoted the qualified
# generic rows).
OBSOLETE_CONFIGURATION: tuple[ObsoleteConfiguration, ...] = (
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE_TAG",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_TAG",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.opencode_host_image_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_PI_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.pi_host_image_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
    ),
)


class ObsoleteConfigurationError(RuntimeError):
    """Raised at startup when a deployment supplies obsolete configuration."""


class ArchitectureBoundaryException(BaseModel):
    """One bounded, documented exemption from an enforced module boundary.

    Source issue: MoonLadderStudios/MoonMind#3711 (required work 6). An
    exemption is only acceptable while a named legacy path in
    :data:`RETIREMENT_INVENTORY` still owns the coupled behavior, so every
    exception names the ``pathId`` whose retirement criteria retire it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    exception_id: str = Field(alias="exceptionId")
    module: str
    rule: str
    reason: str
    retirement_path_id: str = Field(alias="retirementPathId")


# The complete set of enforced-boundary exemptions. Adding one requires naming
# the legacy path that owns its removal; the architecture contract fails when an
# exemption has no owning retirement path.
ARCHITECTURE_BOUNDARY_EXCEPTIONS: tuple[ArchitectureBoundaryException, ...] = (
    ArchitectureBoundaryException(
        exceptionId="omnigent.exception.catalog_router_materializer_descriptor",
        module="api_service/api/routers/omnigent_catalog.py",
        rule="router_has_no_credential_or_host_lifecycle_import",
        reason=(
            "Generic execution-target readiness is still projected inside the "
            "route handler and reads the credential materializer descriptor "
            "registry for a provider's materializer ref. The lookup is a pure "
            "name resolution and performs no materialization side effect."
        ),
        retirementPathId="omnigent.legacy.native_ui_compat",
    ),
    ArchitectureBoundaryException(
        exceptionId="omnigent.exception.profile_bound_coordinator_default_ports",
        module="moonmind/omnigent/profile_bound_execution.py",
        rule="application_receives_infrastructure_at_composition_boundary",
        reason=(
            "The replay-visible Codex coordinator selects its production "
            "Provider Profile, policy, and attempt adapters when a caller "
            "omits them, so existing histories keep the same deployment "
            "adapters instead of a second execution path."
        ),
        retirementPathId="omnigent.legacy.profile_bound_execution",
    ),
    ArchitectureBoundaryException(
        exceptionId="omnigent.exception.oauth_host_runtime_raw_container_commands",
        module="moonmind/omnigent/oauth_host_runtime.py",
        rule="adapter_issues_no_raw_container_command",
        reason=(
            "The retained Codex host lifecycle still assembles its own Docker "
            "and Compose argument vectors for launch, mount, credential-volume, "
            "and egress attestation. Container/volume inventory and reclamation "
            "already moved to host_services/legacy_host_containers.py behind "
            "OmnigentHostContainerInventoryPort; the launch path stays here "
            "until the replay-visible coordinator retires, because moving it "
            "would change the launch argument vector that in-flight histories "
            "were started with."
        ),
        retirementPathId="omnigent.legacy.oauth_host_runtime",
    ),
)


def assert_architecture_exceptions_are_owned(
    exceptions: tuple[ArchitectureBoundaryException, ...] = (
        ARCHITECTURE_BOUNDARY_EXCEPTIONS
    ),
    inventory: tuple[LegacyPathRecord, ...] = RETIREMENT_INVENTORY,
) -> None:
    """Every boundary exemption must name a real legacy path and be bounded."""

    known = {path.path_id for path in inventory}
    seen: set[str] = set()
    for exception in exceptions:
        if exception.exception_id in seen:
            raise RetirementGuardError(
                f"duplicate architecture exception {exception.exception_id!r}"
            )
        seen.add(exception.exception_id)
        if exception.retirement_path_id not in known:
            raise RetirementGuardError(
                f"architecture exception {exception.exception_id!r} names "
                f"unknown retirement path {exception.retirement_path_id!r}"
            )
        if not str(exception.reason or "").strip():
            raise RetirementGuardError(
                f"architecture exception {exception.exception_id!r} has no "
                "documented reason"
            )


# Temporary rollout flags introduced by the supervisor migration. Each maps to
# the trigger that permits its removal. Their presence here is what keeps them
# from becoming a permanent alternate architecture: the retirement test asserts
# every temporary flag has a declared retirement trigger.
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

_INVENTORY_BY_ID: dict[str, LegacyPathRecord] = {
    path.path_id: path for path in RETIREMENT_INVENTORY
}


def get_retirement_record(
    path_id: str, *, inventory: tuple[LegacyPathRecord, ...] | None = None
) -> LegacyPathRecord:
    """Return the inventory row for ``path_id``; an unknown id fails closed."""

    if inventory is None:
        record = _INVENTORY_BY_ID.get(path_id)
    else:
        record = next((p for p in inventory if p.path_id == path_id), None)
    if record is None:
        raise RetirementGuardError(f"unknown retirement path {path_id!r}")
    return record


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


_INVENTORY_BY_SURFACE: dict[str, LegacyPathRecord] = {
    ref: path for path in RETIREMENT_INVENTORY for ref in path.surfaces
}


def retirement_record_for_surface(
    surface_ref: str, *, inventory: tuple[LegacyPathRecord, ...] | None = None
) -> LegacyPathRecord | None:
    """Return the row that owns ``surface_ref``, or ``None`` when it is canonical.

    A surface with no row is a canonical (non-legacy) path — the generic
    realizer, the shared image variable — and is never subject to retirement
    admission control.
    """

    parse_surface_ref(surface_ref)
    if inventory is None:
        return _INVENTORY_BY_SURFACE.get(surface_ref)
    return next(
        (path for path in inventory if surface_ref in path.surfaces), None
    )


def assert_surface_admits_new_work(
    surface_ref: str,
    *,
    rollback_generation: str | None = None,
    rollback_exercise: RollbackExerciseDecision | None = None,
    inventory: tuple[LegacyPathRecord, ...] | None = None,
) -> LegacyAdmissionDecision | None:
    """Reject new work that selects a retired surface.

    This is the one boundary every new-admission source consults, so a trusted
    planner default, an alternate API client supplying an explicit selection, a
    schedule, and a preset are all held to the same code-owned retirement state.
    Returns ``None`` for canonical surfaces that carry no retirement row.
    """

    record = retirement_record_for_surface(surface_ref, inventory=inventory)
    if record is None:
        return None
    return assert_new_admission_allowed(
        record.path_id,
        rollback_generation=rollback_generation,
        rollback_exercise=rollback_exercise,
        inventory=inventory,
    )


def evaluate_new_admission(
    path: LegacyPathRecord,
    *,
    rollback_generation: str | None = None,
    rollback_exercise: RollbackExerciseDecision | None = None,
) -> LegacyAdmissionDecision:
    """Whether new work may be admitted through ``path``.

    ``rollback_only`` admits new work only under an exactly allowlisted rollback
    generation *and* a fresh, successful, exactly-scoped rollback exercise for
    this row. The generation alone is a global string: without the scoped
    exercise decision one allowlisted generation would re-admit every profile,
    Host Class, materializer, model, launch policy, host mode, architecture, and
    owner cohort using the path, and would stay valid after the exercise window
    expired. Every later class refuses, which is what stops new Agent Profiles,
    schedules, presets, and alternate API clients from creating new legacy work
    once the class advances — without touching execution, cancellation, cleanup,
    or reads for already-recorded plans.
    """

    generation = (rollback_generation or "").strip() or None
    if path.retirement_class is RetirementClass.ACTIVE_PRODUCT_PATH:
        return LegacyAdmissionDecision(
            pathId=path.path_id,
            allowed=True,
            reasonCode="active_product_path",
            retirementClass=path.retirement_class,
            rollbackGeneration=generation,
        )
    if path.retirement_class is RetirementClass.ROLLBACK_ONLY:
        if generation is None:
            reason = "rollback_generation_required"
        elif generation not in path.rollback_generations:
            reason = "rollback_generation_not_allowlisted"
        elif rollback_exercise is None:
            reason = "rollback_exercise_evidence_required"
        elif rollback_exercise.retirement_path_id != path.path_id:
            reason = "rollback_exercise_scope_mismatch"
        elif not rollback_exercise.satisfied:
            reason = f"rollback_exercise_unsatisfied:{rollback_exercise.reason_code}"
        else:
            return LegacyAdmissionDecision(
                pathId=path.path_id,
                allowed=True,
                reasonCode="rollback_generation_permitted",
                retirementClass=path.retirement_class,
                rollbackGeneration=generation,
            )
        return LegacyAdmissionDecision(
            pathId=path.path_id,
            allowed=False,
            reasonCode=reason,
            retirementClass=path.retirement_class,
            rollbackGeneration=generation,
        )
    return LegacyAdmissionDecision(
        pathId=path.path_id,
        allowed=False,
        reasonCode=f"new_admission_disabled:{path.retirement_class.value}",
        retirementClass=path.retirement_class,
        rollbackGeneration=generation,
    )


def assert_new_admission_allowed(
    path_id: str,
    *,
    rollback_generation: str | None = None,
    rollback_exercise: RollbackExerciseDecision | None = None,
    inventory: tuple[LegacyPathRecord, ...] | None = None,
) -> LegacyAdmissionDecision:
    """Fail fast when new work selects a path whose class no longer admits it."""

    record = get_retirement_record(path_id, inventory=inventory)
    decision = evaluate_new_admission(
        record,
        rollback_generation=rollback_generation,
        rollback_exercise=rollback_exercise,
    )
    if not decision.allowed:
        raise LegacyAdmissionRejected(
            f"legacy path {path_id!r} no longer admits new work "
            f"(class={decision.retirement_class.value}, "
            f"reason={decision.reason_code}); existing plans continue to "
            "execute, cancel, clean up, and read normally",
            path_id=path_id,
            reason_code=decision.reason_code,
        )
    return decision


def evaluate_removal_eligibility(
    path: LegacyPathRecord,
    *,
    stage: RemovalStage,
    drained_kinds: frozenset[ActiveOwnerKind] | set | None = None,
    passed_criteria: frozenset[RetirementCriterion] | set | None = None,
    retention: RetentionWindows | None = None,
) -> RemovalEligibility:
    """Whether ``path`` may be included in a removal PR at ``stage``.

    Fail-closed by construction: an active-owner class with no drain evidence is
    treated as still owning work, and every retention window defaults to open.
    """

    drained = frozenset(drained_kinds or frozenset())
    windows = retention or RetentionWindows()
    blockers: list[str] = []

    if stage < path.earliest_removal_stage:
        blockers.append(
            f"stage_too_early:{stage.name.lower()}<"
            f"{path.earliest_removal_stage.name.lower()}"
        )
    if path.admits_new_work:
        blockers.append(f"still_admits_new_work:{path.retirement_class.value}")
    for kind in sorted(
        path.active_resource_dependencies - drained, key=lambda k: k.value
    ):
        blockers.append(f"active_owner:{kind.value}")
    if path.replay_dependency and windows.replay_window_open:
        blockers.append("replay_window_open")
    if path.historical_read_dependency and windows.historical_read_window_open:
        blockers.append("historical_read_window_open")
    if path.rollback_dependency:
        if windows.rollback_window_open:
            blockers.append("rollback_window_open")
        if not windows.rollback_exercise_recorded:
            blockers.append("rollback_exercise_not_recorded")

    decision = evaluate_retirement(path, frozenset(passed_criteria or frozenset()))
    blockers.extend(
        f"unmet_criterion:{criterion.value}" for criterion in decision.unmet_criteria
    )

    return RemovalEligibility(
        pathId=path.path_id,
        stage=stage,
        eligible=not blockers,
        blockers=tuple(blockers),
        unmetCriteria=decision.unmet_criteria,
    )


class RemovalPlan(BaseModel):
    """One bounded, reviewable removal targeting exactly one stage.

    Issue #3835 required work section 7 forbids combining every removal into one
    unreviewable change and requires each removal PR to cite the inventory rows
    and passing guards it satisfies. A plan therefore names one stage and the
    rows it removes; :func:`evaluate_removal_plan` returns the citation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    stage: RemovalStage
    path_ids: tuple[str, ...] = Field(alias="pathIds")

    @model_validator(mode="after")
    def _validate(self) -> "RemovalPlan":
        if not self.path_ids:
            raise ValueError("a removal plan must name at least one retirement row")
        if len(set(self.path_ids)) != len(self.path_ids):
            raise ValueError("a removal plan must not name a retirement row twice")
        return self


class RemovalPlanReport(BaseModel):
    """The citation a removal PR carries: rows, guards, and remaining blockers."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    stage: RemovalStage
    allowed: bool
    eligible_path_ids: tuple[str, ...] = Field(alias="eligiblePathIds")
    blocked: tuple[RemovalEligibility, ...]
    required_guard_tests: tuple[str, ...] = Field(alias="requiredGuardTests")
    contract_version: str = Field(
        RETIREMENT_CONTRACT_VERSION, alias="contractVersion"
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


def evaluate_removal_plan(
    plan: RemovalPlan,
    *,
    inventory: tuple[LegacyPathRecord, ...] | None = None,
    drained_by_path: Mapping[str, frozenset[ActiveOwnerKind]] | None = None,
    passed_by_path: Mapping[str, frozenset[RetirementCriterion]] | None = None,
    retention_by_path: Mapping[str, RetentionWindows] | None = None,
) -> RemovalPlanReport:
    """Return the bounded citation for one staged removal.

    Fail-closed: a row with no supplied drain evidence, criteria, or retention
    evidence is evaluated against empty/open defaults and therefore blocks the
    plan rather than passing by omission.
    """

    drained_by_path = drained_by_path or {}
    passed_by_path = passed_by_path or {}
    retention_by_path = retention_by_path or {}

    eligible: list[str] = []
    blocked: list[RemovalEligibility] = []
    guards: list[str] = []
    for path_id in plan.path_ids:
        record = get_retirement_record(path_id, inventory=inventory)
        guards.append(record.removal_guard_test)
        eligibility = evaluate_removal_eligibility(
            record,
            stage=plan.stage,
            drained_kinds=drained_by_path.get(path_id),
            passed_criteria=passed_by_path.get(path_id),
            retention=retention_by_path.get(path_id),
        )
        if eligibility.eligible:
            eligible.append(path_id)
        else:
            blocked.append(eligibility)

    return RemovalPlanReport(
        stage=plan.stage,
        allowed=not blocked,
        eligiblePathIds=tuple(eligible),
        blocked=tuple(blocked),
        requiredGuardTests=tuple(sorted(set(guards))),
    )


def criteria_from_native_chat_acceptance(
    manifest: Mapping[str, Any] | None,
    *,
    evidence_root: Path,
    expected_commit: str | None = None,
    now: datetime | None = None,
) -> frozenset[RetirementCriterion]:
    """Project a #3642 acceptance manifest into passing retirement criteria.

    A retirement guard must be able to consume the published protected report
    without a human asserting the criterion. A missing, incomplete, failed,
    expired, or unresolvable manifest yields no criterion, so the guard stays
    fail-closed instead of inheriting an operator's interpretation.
    """

    if manifest is None:
        return frozenset()
    try:
        validate_workflow_chat_acceptance_manifest(
            manifest,
            evidence_root=evidence_root,
            expected_commit=expected_commit,
            now=now,
        )
    except (ConformanceContractError, ValueError):
        return frozenset()
    return frozenset({RetirementCriterion.NATIVE_CHAT_ACCEPTANCE_PASSED})


def assert_retirement_guard(
    inventory: tuple[LegacyPathRecord, ...] = RETIREMENT_INVENTORY,
    *,
    passed_by_path: dict[str, frozenset[RetirementCriterion]] | None = None,
    drained_by_path: Mapping[str, frozenset[ActiveOwnerKind]] | None = None,
    retention_by_path: Mapping[str, RetentionWindows] | None = None,
    removal_stage_by_path: Mapping[str, RemovalStage] | None = None,
) -> None:
    """Enforce the retirement-guard invariants.

    * A path classified ``removed`` must satisfy the *complete* typed removal
      evidence: the criteria it declares, drain evidence for every active-owner
      kind, the replay/historical-read/rollback retention windows, a recorded
      rollback exercise when it carries a rollback dependency, and a removal
      stage at or after its ``earliest_removal_stage``.
    * A retained path's surfaces must all still resolve.

    The removed branch delegates to :func:`evaluate_removal_plan` rather than
    asserting its own criteria subset, so a row cannot pass the guard while
    sessions or leases remain active or a replay/rollback window is still open.
    Every evidence mapping is fail-closed by omission: a row with no supplied
    drain or retention evidence is evaluated against empty/open defaults and
    therefore blocks.

    Raises :class:`RetirementGuardError` on violation.
    """

    passed_by_path = passed_by_path or {}
    drained_by_path = drained_by_path or {}
    retention_by_path = retention_by_path or {}
    removal_stage_by_path = removal_stage_by_path or {}
    seen: set[str] = set()
    for path in inventory:
        if path.path_id in seen:
            raise RetirementGuardError(f"duplicate retirement row {path.path_id!r}")
        seen.add(path.path_id)
        if path.removed:
            stage = removal_stage_by_path.get(
                path.path_id, path.earliest_removal_stage
            )
            report = evaluate_removal_plan(
                RemovalPlan(stage=stage, pathIds=(path.path_id,)),
                inventory=inventory,
                drained_by_path=drained_by_path,
                passed_by_path=passed_by_path,
                retention_by_path=retention_by_path,
            )
            if not report.allowed:
                blockers = tuple(
                    blocker
                    for eligibility in report.blocked
                    for blocker in eligibility.blockers
                )
                raise RetirementGuardError(
                    f"legacy path {path.path_id!r} is classified removed but its "
                    f"removal evidence is incomplete at stage "
                    f"{stage.name.lower()}: {list(blockers)}"
                )
            continue
        for ref in path.surfaces:
            if not surface_exists(ref):
                raise RetirementGuardError(
                    f"legacy path {path.path_id!r} is still classified "
                    f"{path.retirement_class.value!r} but its machine-checkable "
                    f"surface {ref!r} no longer resolves"
                )


def assert_inventory_is_complete(
    inventory: tuple[LegacyPathRecord, ...] = RETIREMENT_INVENTORY,
    *,
    discovered: frozenset[str] | None = None,
) -> None:
    """Every discovered legacy surface must map to exactly one retirement row.

    This is the guard that makes an *unclassified* new legacy dependency fail
    CI: registering a new non-generic realizer, direct managed runtime strategy,
    provider-specific host script, duplicate Compose service/profile, or legacy
    image variable adds a discovered surface with no owning row.
    """

    surfaces = (
        discover_legacy_surfaces() if discovered is None else frozenset(discovered)
    )
    owners: dict[str, list[str]] = {}
    for path in inventory:
        for ref in path.surfaces:
            owners.setdefault(ref, []).append(path.path_id)

    duplicated = {ref: ids for ref, ids in owners.items() if len(ids) > 1}
    if duplicated:
        raise RetirementGuardError(
            "retirement surfaces claimed by more than one row: "
            f"{ {ref: sorted(ids) for ref, ids in sorted(duplicated.items())} }"
        )

    unclassified = sorted(surfaces - set(owners))
    if unclassified:
        raise RetirementGuardError(
            "legacy surfaces have no retirement row (classify each with a "
            f"RetirementClass in RETIREMENT_INVENTORY): {unclassified}"
        )


def assert_obsolete_configuration(
    env: Mapping[str, str],
    *,
    configuration: tuple[ObsoleteConfiguration, ...] | None = None,
) -> tuple[str, ...]:
    """Fail startup on obsolete configuration; return deprecation warnings.

    During the deprecation window a supplied variable produces an actionable
    operator warning naming its replacement. After removal the same variable is
    rejected outright instead of being silently ignored.
    """

    if configuration is None:
        configuration = OBSOLETE_CONFIGURATION
    warnings: list[str] = []
    rejected: list[str] = []
    for entry in configuration:
        value = str(env.get(entry.variable, "") or "").strip()
        if not value:
            continue
        message = (
            f"{entry.variable} is obsolete; set {entry.replacement} instead "
            f"(retirement row {entry.retirement_path_id})."
        )
        if entry.removed:
            rejected.append(f"{message} {entry.guidance}".strip())
        elif entry.deprecated:
            warnings.append(f"{message} {entry.guidance}".strip())
    if rejected:
        raise ObsoleteConfigurationError(
            "obsolete Omnigent configuration is no longer honored: "
            + " ".join(rejected)
        )
    return tuple(warnings)


def enforce_obsolete_configuration_at_startup(
    log: Any,
    *,
    env: Mapping[str, str] | None = None,
    configuration: tuple[ObsoleteConfiguration, ...] | None = None,
) -> tuple[str, ...]:
    """The shared process-startup check for obsolete Omnigent configuration.

    Every process that consumes these settings calls this — the API and each
    separately restartable Temporal worker — so restarting or deploying one
    process without the other can neither skip the deprecation warning nor
    silently accept a variable another process rejects.

    Raises :class:`ObsoleteConfigurationError` on a removed variable; returns the
    deprecation warnings it logged.
    """

    warnings = assert_obsolete_configuration(
        os.environ if env is None else env, configuration=configuration
    )
    for warning in warnings:
        log.warning("Obsolete Omnigent configuration: %s", warning)
    return warnings


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
    "ARCHITECTURE_BOUNDARY_EXCEPTIONS",
    "ActiveOwnerKind",
    "ArchitectureBoundaryException",
    "ComponentFamily",
    "LEGACY_RETIREMENT_COMPLETE",
    "LegacyAdmissionDecision",
    "LegacyAdmissionRejected",
    "LegacyPathRecord",
    "OBSOLETE_CONFIGURATION",
    "ObsoleteConfiguration",
    "ObsoleteConfigurationError",
    "RETIREMENT_CONTRACT_VERSION",
    "RETIREMENT_INVENTORY",
    "RemovalEligibility",
    "RemovalPlan",
    "RemovalPlanReport",
    "RemovalStage",
    "RetentionWindows",
    "RetirementClass",
    "RetirementCriterion",
    "RetirementDecision",
    "RetirementGuardError",
    "RuntimeGeneration",
    "TEMPORARY_ROLLOUT_FLAGS",
    "assert_architecture_exceptions_are_owned",
    "assert_inventory_is_complete",
    "assert_new_admission_allowed",
    "assert_obsolete_configuration",
    "assert_retirement_guard",
    "assert_surface_admits_new_work",
    "assert_temporary_flags_have_retirement",
    "criteria_from_native_chat_acceptance",
    "enforce_obsolete_configuration_at_startup",
    "evaluate_new_admission",
    "evaluate_removal_eligibility",
    "evaluate_removal_plan",
    "evaluate_retirement",
    "get_retirement_record",
    "retirement_record_for_surface",
]
