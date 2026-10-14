"""Code-owned admission table for retained legacy Omnigent paths.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835,
MoonLadderStudios/MoonMind#3932.

Each row names a retained legacy surface that a new-admission boundary can still
select (a runtime strategy, a non-generic realizer, a provider-specific startup
script or Compose service, a legacy image variable, the embedded transport) or
that owns an enforced-boundary exemption, together with its retirement class.
The class decides only one thing: whether *new* work may still be admitted.
Already-recorded work keeps executing, cancelling, cleaning up, replaying, and
reading through its own owner regardless of the class.

The guard helpers here:

* reject new admission through a row whose class no longer admits it
  (plan compilation, runtime selection, and the bridge share this boundary);
* fail when the repository grows a legacy surface no row classifies; and
* fail when a row names a surface that is gone, so deleting a legacy component
  deletes its row in the same change instead of leaving a stale entry.

Removing a component is ordinary engineering: delete the code, its row, and the
tests that only existed for it, and keep the behavior tests of the surviving
owner (#3932). There is no removal stage, criterion ledger, or drain census.

The module also records temporary supervisor rollout flags with their exit
condition so a rollout flag can never silently become a permanent alternate
architecture.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.retirement_surfaces import (
    discover_legacy_surfaces,
    parse_surface_ref,
    surface_exists,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle: rollback imports this module
    from moonmind.omnigent.session_supervisor_rollback import RollbackExerciseDecision

RETIREMENT_CONTRACT_VERSION = "moonmind.omnigent-legacy-retirement/v2"


class RetirementClass(str, Enum):
    """Whether a retained legacy path still admits new work.

    :data:`ACTIVE_PRODUCT_PATH` admits new work; :data:`ROLLBACK_ONLY` admits
    new work only under an explicitly permitted rollback generation;
    :data:`NEW_ADMISSION_DISABLED` refuses it. Recorded work is never affected.
    """

    ACTIVE_PRODUCT_PATH = "active_product_path"
    NEW_ADMISSION_DISABLED = "new_admission_disabled"
    ROLLBACK_ONLY = "rollback_only"


# Classes that still admit new work. Everything else must not appear as the
# target of a newly compiled execution plan.
_NEW_ADMISSION_CLASSES: frozenset[RetirementClass] = frozenset(
    {RetirementClass.ACTIVE_PRODUCT_PATH, RetirementClass.ROLLBACK_ONLY}
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
    """One retained legacy path and whether it still admits new work."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    owner: str
    description: str
    retirement_class: RetirementClass = Field(alias="retirementClass")
    surfaces: tuple[str, ...] = Field(alias="surfaces", min_length=1)
    # The concrete code path that can still create new records or admit new work
    # through this component. Empty exactly when the class no longer admits.
    new_admission_source: str = Field("", alias="newAdmissionSource")
    # Exact rollback generations permitted to re-admit new work through this
    # path. Exact membership only — never a prefix or substring match.
    rollback_generations: frozenset[str] = Field(
        default_factory=frozenset, alias="rollbackGenerations"
    )

    @property
    def admits_new_work(self) -> bool:
        return self.retirement_class in _NEW_ADMISSION_CLASSES

    @property
    def requires_rollback_generation(self) -> bool:
        """Whether admitting new work needs an explicit rollback generation."""

        return self.retirement_class is RetirementClass.ROLLBACK_ONLY

    @model_validator(mode="after")
    def _validate(self) -> "LegacyPathRecord":
        for ref in self.surfaces:
            parse_surface_ref(ref)
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError(f"{self.path_id}: duplicate surface refs")

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
        return self


class LegacyAdmissionDecision(BaseModel):
    """Whether new work may still be admitted through one legacy path."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    allowed: bool
    reason_code: str = Field(alias="reasonCode")
    retirement_class: RetirementClass = Field(alias="retirementClass")
    rollback_generation: str | None = Field(None, alias="rollbackGeneration")
    contract_version: str = Field(RETIREMENT_CONTRACT_VERSION, alias="contractVersion")

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


# The code-owned admission table. A row exists only while a new-admission
# boundary can select its surfaces or an architecture exemption names it; a
# surface is a ``python:module:symbol``, runtime strategy, realizer identity,
# startup script, Compose service/profile, or image variable.
RETIREMENT_INVENTORY: tuple[LegacyPathRecord, ...] = (
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_codex_launch",
        owner="omnigent-runtime-plane",
        description=(
            "Direct Codex managed launch strategy: the runtime the workflow "
            "launcher selects for the ``codex_cli`` runtime id."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.strategies.codex_cli:"
            "CodexCliStrategy",
            "runtime-strategy:codex_cli",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies:RUNTIME_STRATEGIES"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_claude_launch",
        owner="omnigent-runtime-plane",
        description=(
            "Direct Claude Code managed launch strategy and session ownership "
            "for the ``claude_code`` runtime id."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.strategies.claude_code:"
            "ClaudeCodeStrategy",
            "runtime-strategy:claude_code",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies:RUNTIME_STRATEGIES"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.profile_bound_realizer",
        owner="omnigent-control-plane",
        description=(
            "``codex-profile-bound@1`` execution realizer registration and "
            "trusted planner selection."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.omnigent.realizers.codex_profile_bound:"
            "CodexProfileBoundRealizer",
            "realizer:codex-profile-bound@1",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.planner:select_execution_realizer"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.profile_bound_execution",
        owner="omnigent-control-plane",
        description="Legacy profile-bound execution coordinator and routing.",
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.omnigent.profile_bound_execution:"
            "OmnigentProfileBoundExecutionCoordinator",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.realizers.codex_profile_bound:CodexProfileBoundRealizer"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.oauth_host_runtime",
        owner="omnigent-runtime-plane",
        description=(
            "Legacy OAuth host runtime that assembles Codex host launch, mount, "
            "credential-volume, and egress attestation argument vectors."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.omnigent.oauth_host_runtime:OmnigentOAuthHostRuntime",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.profile_bound_execution:"
            "OmnigentProfileBoundExecutionCoordinator"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.codex_static_host_startup",
        owner="omnigent-deployment",
        description=(
            "Codex-specific static host startup, health, and init scripts kept "
            "as thin wrappers over the consolidated generic entrypoint (#3834)."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "script:start-codex-oauth-host.sh",
            "script:check-codex-oauth-host.sh",
            "script:init-codex-oauth-host.sh",
            "compose-service:omnigent-host-codex",
            "compose-service:omnigent-host-codex-init",
            "compose-profile:omnigent-host-codex",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host-codex",
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.claude_static_host_startup",
        owner="omnigent-deployment",
        description=(
            "Claude-specific static host startup and health scripts kept as thin "
            "wrappers over the consolidated generic entrypoint (#3834)."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "script:start-claude-oauth-host.sh",
            "script:check-claude-oauth-host.sh",
            "compose-service:omnigent-host-claude",
            "compose-service:omnigent-host-claude-init",
            "compose-profile:omnigent-host-claude",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host-claude",
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.projection_host_startup",
        owner="omnigent-deployment",
        description=(
            "Pre-consolidation ``omnigent-host`` Compose service, its "
            "runner-projection entrypoint, and health script. Still resolves the "
            "legacy image variable rather than the shared-image expression."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "script:start-host-with-projections.sh",
            "script:check-runner-projections.sh",
            "compose-service:omnigent-host",
            "compose-profile:omnigent-host",
        ),
        newAdmissionSource="docker-compose.yaml:omnigent-host",
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.host_image_variable_alias",
        owner="omnigent-deployment",
        description=(
            "Legacy generic host image variables honored as a bounded alias when "
            "the canonical shared-image variable is unset."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
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
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.opencode_host_image_alias",
        owner="omnigent-deployment",
        description=(
            "OpenCode-specific shared-image variable and the "
            "``omnigent-host-opencode`` image alias it names."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_OPENCODE_HOST_IMAGE_ENV",
            "env:OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.host_classes:"
            "get_opencode_host_image_ref"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.pi_host_image_alias",
        owner="omnigent-deployment",
        description="Per-provider Pi host image variable predating the shared image.",
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.omnigent.harness_platform.host_classes:"
            "OMNIGENT_PI_HOST_IMAGE_ENV",
            "env:OMNIGENT_PI_HOST_IMAGE_REF",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.host_classes:get_pi_host_image_ref"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.native_ui_compat",
        owner="omnigent-control-plane",
        description="Legacy native chat / Workflow Detail compatibility projection.",
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=("python:moonmind.omnigent.native_ui_compat:classify_native_ui_http",),
        newAdmissionSource=(
            "api_service.api.routers.omnigent_bridge:_bridge_event_payload"
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.embedded_host_transport",
        owner="omnigent-control-plane",
        description=(
            "Retired embedded host/runner transport admission enforcement "
            "(MoonLadderStudios/MoonMind#3955): the trusted selection and "
            "HTTP boundaries that reject new sessions, hosts, and credential "
            "consumers through the experimental embedded transport with an "
            "actionable proxy alternative. Retained sessions drain through "
            "their recorded mode/endpoint and cleanup owner. "
            "Proxy mode is the supported topology."
        ),
        retirementClass=RetirementClass.NEW_ADMISSION_DISABLED,
        surfaces=(
            "python:moonmind.omnigent.bridge_config:"
            "assert_new_embedded_transport_admission_allowed",
            "python:moonmind.omnigent.bridge_config:embedded_transport_retirement",
            "python:api_service.api.routers.omnigent_bridge:"
            "_reject_new_embedded_transport_admission",
            "python:api_service.api.routers.omnigent_bridge:"
            "_require_new_embedded_transport_admission",
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
    exception names the ``pathId`` of the row it shelters; the exemption is
    deleted together with that row's code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    exception_id: str = Field(alias="exceptionId")
    module: str
    rule: str
    reason: str
    retirement_path_id: str = Field(alias="retirementPathId")


# The complete set of enforced-boundary exemptions. Adding one requires naming
# the legacy path it shelters; the architecture contract fails when an exemption
# has no owning retirement path.
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
# the exit condition that permits its removal, so a rollout flag cannot become
# a permanent alternate architecture.
LEGACY_RETIREMENT_COMPLETE = "legacy_retirement_complete"
TEMPORARY_ROLLOUT_FLAGS: dict[str, str] = {
    "omnigent_session_supervisor_admission_mode": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_canary_owner_ids": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_generation": LEGACY_RETIREMENT_COMPLETE,
    "omnigent_session_supervisor_allowed_execution_profile_refs": LEGACY_RETIREMENT_COMPLETE,
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
    return next((path for path in inventory if surface_ref in path.surfaces), None)


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
    expired. ``new_admission_disabled`` refuses, which is what stops new Agent
    Profiles, schedules, presets, and alternate API clients from creating new
    legacy work once the class changes — without touching execution,
    cancellation, cleanup, or reads for already-recorded plans.
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


def assert_inventory_is_complete(
    inventory: tuple[LegacyPathRecord, ...] = RETIREMENT_INVENTORY,
    *,
    discovered: frozenset[str] | None = None,
) -> None:
    """The admission table must match the repository.

    * Every discovered legacy surface maps to exactly one row, so registering a
      new non-generic realizer, direct managed runtime strategy,
      provider-specific host script, duplicate Compose service/profile, or
      legacy image variable cannot bypass admission control.
    * Every row surface still exists, so deleting a legacy component deletes
      its row in the same change rather than leaving a stale admission entry.
    """

    surfaces = (
        discover_legacy_surfaces() if discovered is None else frozenset(discovered)
    )
    owners: dict[str, list[str]] = {}
    seen: set[str] = set()
    for path in inventory:
        if path.path_id in seen:
            raise RetirementGuardError(f"duplicate retirement row {path.path_id!r}")
        seen.add(path.path_id)
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

    stale = sorted(
        f"{ids[0]}: {ref}" for ref, ids in owners.items() if not surface_exists(ref)
    )
    if stale:
        raise RetirementGuardError(
            "retirement rows name surfaces that no longer exist; delete the row "
            f"(or the stale surface) together with the removed code: {stale}"
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
    "ArchitectureBoundaryException",
    "LEGACY_RETIREMENT_COMPLETE",
    "LegacyAdmissionDecision",
    "LegacyAdmissionRejected",
    "LegacyPathRecord",
    "OBSOLETE_CONFIGURATION",
    "ObsoleteConfiguration",
    "ObsoleteConfigurationError",
    "RETIREMENT_CONTRACT_VERSION",
    "RETIREMENT_INVENTORY",
    "RetirementClass",
    "RetirementGuardError",
    "TEMPORARY_ROLLOUT_FLAGS",
    "assert_architecture_exceptions_are_owned",
    "assert_inventory_is_complete",
    "assert_new_admission_allowed",
    "assert_obsolete_configuration",
    "assert_surface_admits_new_work",
    "assert_temporary_flags_have_retirement",
    "enforce_obsolete_configuration_at_startup",
    "evaluate_new_admission",
    "get_retirement_record",
    "retirement_record_for_surface",
]
