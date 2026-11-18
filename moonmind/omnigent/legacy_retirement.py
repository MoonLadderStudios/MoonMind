"""Code-owned admission table for retained legacy Omnigent paths.

Source issues: MoonLadderStudios/MoonMind#3712, MoonLadderStudios/MoonMind#3835,
MoonLadderStudios/MoonMind#3932, MoonLadderStudios/MoonMind#5341.

Each row names a retained legacy surface that a new-admission boundary can still
select (a runtime strategy, a non-generic realizer, a provider-specific startup
script or Compose service, a legacy image variable, the embedded transport) or
that owns an enforced-boundary exemption, together with its retirement class,
the actual consumer it still serves, and the observable condition under which
it can be deleted. The class decides only one thing: whether *new* work may
still be admitted.
Already-recorded work keeps executing, cancelling, cleaning up, replaying, and
reading through its own owner regardless of the class.

The guard helpers here:

* reject new admission through a row whose class no longer admits it
  (plan compilation, runtime selection, and the bridge share this boundary);
* fail when the repository grows a legacy surface no row classifies; and
* fail when a row names a surface that is gone, so deleting a legacy component
  deletes its row in the same change instead of leaving a stale entry.

``OBSOLETE_CONFIGURATION`` is the one deprecate-then-reject table for retired
environment names, MoonMind settings with no Omnigent path included; the API and
every worker check it at startup.

Removing a component is ordinary engineering: delete the code, its row, and the
tests that only existed for it, and keep the behavior tests of the surviving
owner (#3932). There is no removal stage, criterion ledger, or drain census.
Restoring a prior default for new work belongs to the supervisor rollback
control (:mod:`moonmind.omnigent.session_supervisor_rollback`), not to a row.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.retirement_surfaces import (
    discover_legacy_surfaces,
    parse_surface_ref,
    surface_exists,
)

RETIREMENT_CONTRACT_VERSION = "moonmind.omnigent-legacy-retirement/v3"


class RetirementClass(str, Enum):
    """Whether a retained legacy path still admits new work.

    :data:`ACTIVE_PRODUCT_PATH` admits new work; :data:`NEW_ADMISSION_DISABLED`
    refuses it. Recorded work is never affected.
    """

    ACTIVE_PRODUCT_PATH = "active_product_path"
    NEW_ADMISSION_DISABLED = "new_admission_disabled"


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
    # Who still depends on the path: the supported journeys, explicit choices,
    # and retained histories or records it serves. An unknown installed state
    # is not evidence that no consumer remains.
    consumer: str
    # The observable state that permits deleting the path together with this
    # row. It names what must migrate or drain, not a date or phase.
    removal_condition: str = Field(alias="removalCondition")
    # For a path that still admits new work: the shared Omnigent lifecycle
    # operation that would serve its consumer, and the concrete outcome that
    # operation still lacks. Keeping a path live needs a named gap, not age.
    # Both are empty exactly when the class no longer admits.
    shared_operation: str = Field("", alias="sharedOperation")
    missing_outcome: str = Field("", alias="missingOutcome")

    @property
    def admits_new_work(self) -> bool:
        return self.retirement_class is RetirementClass.ACTIVE_PRODUCT_PATH

    @model_validator(mode="after")
    def _validate(self) -> "LegacyPathRecord":
        for ref in self.surfaces:
            parse_surface_ref(ref)
        if len(set(self.surfaces)) != len(self.surfaces):
            raise ValueError(f"{self.path_id}: duplicate surface refs")
        for name, value in (
            ("consumer", self.consumer),
            ("removal condition", self.removal_condition),
        ):
            if not value.strip():
                raise ValueError(f"{self.path_id}: row must name its {name}")

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
        for name, value in (
            ("shared operation", self.shared_operation),
            ("missing outcome", self.missing_outcome),
        ):
            if admits != bool(value.strip()):
                raise ValueError(
                    f"{self.path_id}: a path that still admits new work must "
                    f"name its {name}, and only such a path may"
                )
        return self


class LegacyAdmissionDecision(BaseModel):
    """Whether new work may still be admitted through one legacy path."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    path_id: str = Field(alias="pathId")
    allowed: bool
    reason_code: str = Field(alias="reasonCode")
    retirement_class: RetirementClass = Field(alias="retirementClass")
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
        consumer=(
            "Untranslated serialized ``codex_cli`` plans and native extensions/direct targets, "
            "native choices with retained context/workspaces or unsupported tier options, "
            "and Codex key Profiles the provider config cannot "
            "carry, which ``profile_route_runtime`` routes here; their "
            "queued starts, retries and recovery, "
            "workflow-scoped Codex AgentSession turns, and recorded "
            "direct-run histories that replay these activities."
        ),
        removalCondition=(
            "New ``codex_cli`` admission is closed "
            "(``MOONMIND_CODEX_DIRECT_RETIRED_AT`` or this row's class), "
            "explicit selections translate to the shared Omnigent "
            "lifecycle, and no open history can schedule or retry a direct "
            "Codex launch."
        ),
        sharedOperation=(
            "``codex-native`` through ``generic-omnigent-host@1``, "
            "already the unset default."
        ),
        missingOutcome=(
            "OAuth Profiles with protected or untranslated per-run environment, file or HOME settings; "
            "Codex key Profiles the Omnigent provider config cannot carry "
            "exactly: files outside the declared private Codex HOME, uncarried tool/launch settings, authentication "
            "outside the selected key SecretRef, or endpoints the existing "
            "egress policy does not admit. Declared single-provider Responses "
            "configuration, including the rendered managed MiniMax recipe, "
            "uses ``omnigent-provider-config@1`` and its private Codex config, "
            "including developer instructions, project-document discovery and history preferences. "
            "Serialized inputs without original admitted snapshots, native contexts/workspaces beyond unpinned canonical GitHub repository handoffs and tier "
            "parameters still need equivalent shared-plan handoffs. Pool accounts bind "
            "their selected plan to the existing fenced grant; Codex same-epoch successors "
            "carry selected account, context and verified workspace through the parent. "
            "Fresh fixed and implicit accounts use the same dispatch handoff; "
            "queued older parents without it retain native dispatch. Fixed step accounts "
            "already have their own plans; the root cannot authorize another account. "
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
        consumer=(
            "Untranslated serialized ``claude_code`` plans and native extensions/direct targets "
            "and native contexts/workspaces or unsupported tier options; Claude "
            "Provider Profiles no Omnigent configuration accepts (key "
            "Profiles whose launch environment the provider config cannot "
            "carry, and every Claude Profile while generic Claude is "
            "unqualified), which ``profile_route_runtime`` routes here; "
            "their queued starts, retries and recovery; and recorded "
            "direct-run histories that replay these activities."
        ),
        removalCondition=(
            "New ``claude_code`` admission is closed by this row's class, "
            "explicit selections translate to the shared Omnigent "
            "lifecycle, and no open history can schedule or retry a direct "
            "Claude launch."
        ),
        sharedOperation=(
            "``claude-native`` through ``generic-omnigent-host@1``, "
            "already the unset default."
        ),
        missingOutcome=(
            "OAuth Profiles with per-run environment outside the supported "
            "nonsecret tuning and safe shell values, file or HOME settings; Claude key Profiles "
            "with file templates, HOME overrides, "
            "uncarried environment settings or endpoints outside the installed "
            "egress policy. Exact API-key Profiles and declared MiniMax "
            "environment bundles run through ``omnigent-provider-config@1`` "
            "with their endpoint, model pins and non-secret launch tuning. "
            "Native Claude command rendering "
            "already uses the shared renderer through the admitted harness. "
            "Serialized inputs without original admitted snapshots, native contexts/workspaces beyond unpinned canonical GitHub repository handoffs and tier parameters "
            "still need an equivalent immutable shared-plan handoff. Fresh fixed, "
            "implicit and pool accounts bind their selected plan to the existing fenced grant."
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.managed_runtime_launcher",
        owner="omnigent-runtime-plane",
        description=(
            "Direct managed-runtime launcher that owns process launch, "
            "environment, credential, workspace and cleanup for the direct "
            "runtime strategies, separately from the shared host lifecycle."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.workflows.temporal.runtime.launcher:"
            "ManagedRuntimeLauncher",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.runtime.strategies:RUNTIME_STRATEGIES"
        ),
        consumer=(
            "New, queued and recovered ``codex_cli`` and ``claude_code`` "
            "managed runs, wired by "
            "``moonmind.workflows.temporal.worker_runtime``."
        ),
        removalCondition=(
            "Both direct launch rows are deleted. The Codex and Claude CLIs "
            "and Node leave the application image in the same change."
        ),
        sharedOperation=(
            "The generic host launcher, workspace, credential "
            "materializers and janitor under "
            "``moonmind.omnigent.host_services``."
        ),
        missingOutcome=(
            "Every outcome the two direct launch rows still name; the "
            "launcher has no consumer of its own."
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.direct_agent_session",
        owner="omnigent-runtime-plane",
        description=(
            "Direct workflow-scoped Codex AgentSession: the "
            "``MoonMind.AgentSession`` child, its app-server session runtime, "
            "and the Docker session controller."
        ),
        retirementClass=RetirementClass.ACTIVE_PRODUCT_PATH,
        surfaces=(
            "python:moonmind.workflows.temporal.workflows.agent_session:"
            "MoonMindAgentSessionWorkflow",
            "python:moonmind.workflows.temporal.runtime.codex_session_runtime:"
            "CodexManagedSessionRuntime",
            "python:moonmind.workflows.temporal.runtime."
            "managed_session_controller:DockerCodexManagedSessionController",
        ),
        newAdmissionSource=(
            "moonmind.workflows.temporal.workflows.run:"
            "MoonMindRunWorkflow._maybe_bind_workflow_scoped_session"
        ),
        consumer=(
            "Managed ``codex_cli`` runs that bind a workflow-scoped session: "
            "session reuse and clearing between steps, bound native "
            "interaction, steering, interrupt and recovery of the session "
            "container, and recorded histories that replay the child "
            "workflow and its patches."
        ),
        removalCondition=(
            "The direct Codex launch row is deleted and no open history "
            "starts, signals or replays a ``MoonMind.AgentSession`` child."
        ),
        sharedOperation=(
            "Bridge sessions with ``sendMessage``, ``interruptTurn``, "
            "steering and attested host resume."
        ),
        missingOutcome=(
            "Direct admission for configurations outside the exact shared "
            "translation and retained AgentSession histories still invoke "
            "this child. Ported Profiles use shared same-epoch native context "
            "continuation with attempt-scoped session/resource authority; "
            "a literal provider-session identity is not shared across attempts."
        ),
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
            "python:moonmind.omnigent.harness_platform.shared_host_image:"
            "LEGACY_HOST_IMAGE_ENV",
            "env:OMNIGENT_HOST_IMAGE_REF",
        ),
        newAdmissionSource=(
            "moonmind.omnigent.harness_platform.shared_host_image:"
            "resolve_shared_host_image_ref"
        ),
        consumer=(
            "Existing deployment configuration that still pins a host image "
            "through the pre-shared-image variables; .env-template no longer "
            "sets them and the API Compose environment forwards only a "
            "supplied value, with no default."
        ),
        removalCondition=(
            "One release after the variables' 2026-11-17 deprecation in "
            "``OBSOLETE_CONFIGURATION``: remove ``LEGACY_HOST_IMAGE_ENV``, "
            "the nested ``OMNIGENT_HOST_IMAGE_REF`` fallbacks and this row."
        ),
        sharedOperation=("The canonical shared-image variable."),
        missingOutcome=(
            "Nothing functional; deployments still supplying the old "
            "variables keep their pinned image, with a startup warning, "
            "until the deprecation window ends."
        ),
    ),
    LegacyPathRecord(
        pathId="omnigent.legacy.opencode_host_image_alias",
        owner="omnigent-deployment",
        description=(
            "Deprecated OpenCode-specific image variable; defaults to the "
            "shared image."
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
        consumer=(
            "Deployments that still pin the OpenCode host through this "
            "variable: bootstrap judges the pin, and OpenCode credential "
            "validation runs in it. No Host Class launches it; "
            "``opencode-native`` runs ``omnigent-opencode@2`` on the shared "
            "image."
        ),
        removalCondition=(
            "The variable completes its obsolete-configuration deprecation "
            "window and OpenCode credential validation reads the shared image."
        ),
        sharedOperation="``OMNIGENT_SHARED_HOST_IMAGE_REF``.",
        missingOutcome=(
            "Nothing functional; deployments still supplying the variable keep "
            "their pinned image until the deprecation window ends."
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
        consumer=(
            "``pi-native`` execution, whose ``omnigent-pi@1`` Host Class "
            "still resolves this variable."
        ),
        removalCondition="Pi runs from the shared host image through a runtime pack.",
        sharedOperation=("A runtime pack on the shared host image."),
        missingOutcome=(
            "A Pi runtime pack; ``omnigent-pi@1`` has only its " "per-provider image."
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
        consumer=(
            "Native chat and Workflow Detail projections of recorded bridge "
            "events, including retained direct and embedded sessions."
        ),
        removalCondition=(
            "Workflow Detail and native chat read only the generic bridge "
            "event projection and no retained session needs the "
            "compatibility classifier."
        ),
        sharedOperation=("The generic bridge event projection."),
        missingOutcome=(
            "A projection for retained direct and embedded sessions whose"
            " recorded events only this classifier reads."
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
    # Who decides when the row is deleted: a retirement row whose code still
    # honors the variable, or, for a variable nothing honors any more, the
    # row's own removal condition. Exactly one is set.
    retirement_path_id: str = Field("", alias="retirementPathId")
    removal_condition: str = Field("", alias="removalCondition")
    # Empty when nothing replaces the variable; the deployment removes it.
    replacement: str = ""
    # False while the variable is still honored (nothing to warn about yet).
    # True once the deployment must stop supplying it.
    deprecated: bool = False
    removed: bool = False
    guidance: str = ""
    # The value .env-template and Compose shipped. It selects exactly what
    # omitting the variable selects, so an untouched .env neither warns nor
    # fails startup, during the window or after removal.
    shipped_default: str = Field("", alias="shippedDefault")

    @model_validator(mode="after")
    def _validate(self) -> "ObsoleteConfiguration":
        if bool(self.retirement_path_id.strip()) == bool(
            self.removal_condition.strip()
        ):
            raise ValueError(
                f"{self.variable}: name a retirement path or record a removal "
                "condition, not both"
            )
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


_HOST_IMAGE_ALIAS_GUIDANCE = (
    "Choose the host image through OMNIGENT_SHARED_HOST_IMAGE_REF (or "
    "OMNIGENT_SHARED_HOST_IMAGE and OMNIGENT_SHARED_HOST_IMAGE_TAG) and remove "
    "this variable from the deployment's .env. The alias is still honored "
    "until it is removed one release after its 2026-11-17 deprecation."
)

# For names nothing reads any more. Rejecting such a name keeps a value a
# deployment still sets (a credential alias, say) from silently falling back to
# a default; after one release of rejection the row is deleted.
_REJECTED_FOR_ONE_RELEASE = (
    "One release after 2026-11-17, when this table became the variable's only "
    "rejecter: delete the row."
)
_ARTIFACT_ALIAS_GUIDANCE = (
    "The alias is no longer read, so the artifact store would otherwise fall "
    "back to its default. Rename it in the deployment's .env."
)

_PUBLICATION_RECOVERY_COHORT_GUIDANCE = (
    "Publication recovery has no shadow mode, canary cohort, or generation; "
    "PUBLICATION_RECOVERY_ENABLED turns it on or off and "
    "PUBLICATION_RECOVERY_ALLOWED_MODES limits destination modes."
)
_CONTROL_STOP_CONTINUATION_GUIDANCE = (
    "Control-stop continuation was removed; it never had a production " "producer."
)
# Rollout controls a single-operator product does not have. Rejecting a kept
# non-default value stops it from changing behavior silently: a shadow or
# canary setting would otherwise turn into live publication. The previous
# default selects what omitting the variable now selects.
_RETIRED_ROLLOUT_CONTROLS: tuple[tuple[str, str, str], ...] = (
    *(
        (f"PUBLICATION_RECOVERY_{name}", default, _PUBLICATION_RECOVERY_COHORT_GUIDANCE)
        for name, default in (
            ("SHADOW", "false"),
            ("CANARY_REPOSITORIES", ""),
            ("CANARY_OWNER_IDS", ""),
            ("GENERATION", "default"),
        )
    ),
    *(
        (
            f"CONTROL_STOP_CONTINUATION_{name}",
            default,
            _CONTROL_STOP_CONTINUATION_GUIDANCE,
        )
        for name, default in (
            ("ENABLED", "false"),
            ("SHADOW", "false"),
            ("CANARY_OWNER_IDS", ""),
            ("ALLOWED_PROVIDER_PROFILE_IDS", ""),
            ("ALLOWED_EXECUTION_PROFILE_REFS", ""),
            ("ALLOWED_LAUNCH_POLICY_REFS", ""),
            ("GENERATION", "disabled"),
        )
    ),
    (
        "LIVE_LOGS_SESSION_TIMELINE_ROLLOUT",
        "off",
        "The Live Logs session timeline was removed; Live Logs shows the line "
        "viewer for every run.",
    ),
    (
        "LIVE_LOGS_STRUCTURED_HISTORY_ENABLED",
        "true",
        "Live Logs always loads structured history and falls back to the "
        "merged log only when history is unavailable.",
    ),
)

# Every obsolete configuration identity: the API and every worker warn about it
# during its deprecation window and fail startup once it is removed. The Pi
# alias is not deprecated yet: it remains a supported way to pin a prior image.
OBSOLETE_CONFIGURATION: tuple[ObsoleteConfiguration, ...] = (
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
        deprecated=True,
        guidance=_HOST_IMAGE_ALIAS_GUIDANCE,
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE",
        deprecated=True,
        guidance=_HOST_IMAGE_ALIAS_GUIDANCE,
        shippedDefault="ghcr.io/omnigent-ai/omnigent-host",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_HOST_IMAGE_TAG",
        retirementPathId="omnigent.legacy.host_image_variable_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_TAG",
        deprecated=True,
        guidance=_HOST_IMAGE_ALIAS_GUIDANCE,
        shippedDefault="latest",
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.opencode_host_image_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
        deprecated=True,
        guidance=(
            "No Host Class launches this image any more; OpenCode runs "
            "omnigent-opencode@2 on the shared host image. Pin that image "
            "through OMNIGENT_SHARED_HOST_IMAGE_REF and remove this variable "
            "from the deployment's .env."
        ),
    ),
    ObsoleteConfiguration(
        variable="OMNIGENT_PI_HOST_IMAGE_REF",
        retirementPathId="omnigent.legacy.pi_host_image_alias",
        replacement="OMNIGENT_SHARED_HOST_IMAGE_REF",
    ),
    *(
        ObsoleteConfiguration(
            variable=alias,
            removalCondition=_REJECTED_FOR_ONE_RELEASE,
            replacement=replacement,
            deprecated=True,
            removed=True,
            guidance=_ARTIFACT_ALIAS_GUIDANCE,
        )
        for alias, replacement in (
            ("MINIO_ENDPOINT", "TEMPORAL_ARTIFACT_S3_ENDPOINT"),
            ("MINIO_BUCKET", "TEMPORAL_ARTIFACT_S3_BUCKET"),
            ("MINIO_ACCESS_KEY", "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID"),
            ("MINIO_SECRET_KEY", "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY"),
            ("TEMPORAL_ARTIFACTS_ROOT", "TEMPORAL_ARTIFACT_ROOT"),
        )
    ),
    *(
        ObsoleteConfiguration(
            variable=variable,
            removalCondition=_REJECTED_FOR_ONE_RELEASE,
            deprecated=True,
            removed=True,
            guidance=(
                "The cutover record and release notes were removed; user "
                "Workflow Execution routing resolves from "
                "TEMPORAL_USER_WORKFLOW_V2_TASK_QUEUE and "
                "TEMPORAL_WORKFLOW_TASK_QUEUE."
            ),
        )
        for variable in (
            "TEMPORAL_USER_WORKFLOW_CUTOVER_RECORD_PATH",
            "TEMPORAL_USER_WORKFLOW_RELEASE_NOTES_PATH",
        )
    ),
    ObsoleteConfiguration(
        variable="MEMORY_LONG_TERM",
        removalCondition=_REJECTED_FOR_ONE_RELEASE,
        deprecated=True,
        removed=True,
        guidance=(
            "Long-term memory providers were retired; Mem0 required Qdrant. "
            "No provider replaces them."
        ),
        shippedDefault="off",
    ),
    *(
        ObsoleteConfiguration(
            variable=f"{prefix}{variable}",
            removalCondition=_REJECTED_FOR_ONE_RELEASE,
            deprecated=True,
            removed=True,
            guidance=guidance,
            shippedDefault=previous_default,
        )
        for variable, previous_default, guidance in _RETIRED_ROLLOUT_CONTROLS
        for prefix in ("", "FEATURE_FLAGS__")
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
    return assert_new_admission_allowed(record.path_id, inventory=inventory)


def evaluate_new_admission(path: LegacyPathRecord) -> LegacyAdmissionDecision:
    """Whether new work may be admitted through ``path``.

    ``new_admission_disabled`` refuses, which is what stops new Agent Profiles,
    schedules, presets, and alternate API clients from creating new legacy work
    once the class changes — without touching execution, cancellation, cleanup,
    or reads for already-recorded plans.
    """

    if path.admits_new_work:
        return LegacyAdmissionDecision(
            pathId=path.path_id,
            allowed=True,
            reasonCode="active_product_path",
            retirementClass=path.retirement_class,
        )
    return LegacyAdmissionDecision(
        pathId=path.path_id,
        allowed=False,
        reasonCode=f"new_admission_disabled:{path.retirement_class.value}",
        retirementClass=path.retirement_class,
    )


def assert_new_admission_allowed(
    path_id: str,
    *,
    inventory: tuple[LegacyPathRecord, ...] | None = None,
) -> LegacyAdmissionDecision:
    """Fail fast when new work selects a path whose class no longer admits it."""

    decision = evaluate_new_admission(
        get_retirement_record(path_id, inventory=inventory)
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
    rejected outright instead of being silently ignored. A blank value or the
    shipped default is treated as omitted. Names and the shipped default match
    case-insensitively, as settings read them.
    """

    if configuration is None:
        configuration = OBSOLETE_CONFIGURATION
    supplied: dict[str, list[str]] = {}
    for key, raw in env.items():
        value = str(raw or "").strip()
        if value:
            supplied.setdefault(str(key).strip().upper(), []).append(value)
    warnings: list[str] = []
    rejected: list[str] = []
    for entry in configuration:
        default = entry.shipped_default.casefold()
        if all(
            value.casefold() == default
            for value in supplied.get(entry.variable.upper(), ())
        ):
            continue
        action = (
            f"set {entry.replacement} instead" if entry.replacement else "remove it"
        )
        owner = (
            f" (retirement row {entry.retirement_path_id})"
            if entry.retirement_path_id
            else ""
        )
        message = f"{entry.variable} is obsolete; {action}{owner}."
        if entry.removed:
            rejected.append(f"{message} {entry.guidance}".strip())
        elif entry.deprecated:
            warnings.append(f"{message} {entry.guidance}".strip())
    if rejected:
        raise ObsoleteConfigurationError(
            "obsolete configuration is no longer honored: " + " ".join(rejected)
        )
    return tuple(warnings)


def enforce_obsolete_configuration_at_startup(
    log: Any,
    *,
    env: Mapping[str, str] | None = None,
    configuration: tuple[ObsoleteConfiguration, ...] | None = None,
) -> tuple[str, ...]:
    """The shared process-startup check for obsolete configuration.

    Every process that consumes these settings calls this — the API and each
    separately restartable Temporal worker — so restarting or deploying one
    process without the other can neither skip the deprecation warning nor
    silently accept a variable another process rejects.

    Raises :class:`ObsoleteConfigurationError` on a removed variable; returns the
    deprecation warnings it logged.
    """

    from dotenv import dotenv_values

    from moonmind.config import paths
    from moonmind.omnigent.bootstrap.store import load_installed_release_image_env

    environment = os.environ if env is None else env
    # Settings also load ``.env`` without exporting it. As there, the process
    # environment wins over the file, matching names case-insensitively.
    exported = {str(key).strip().upper() for key in environment}
    source = {
        key: value or ""
        for key, value in dotenv_values(paths.ENV_FILE, encoding="utf-8").items()
        if key.strip().upper() not in exported
    }
    source.update(environment)
    # The release controller renders its recorded refs into ``.env.deploy``.
    # Only a value the deployment supplies itself is obsolete configuration.
    generated = load_installed_release_image_env()
    warnings = assert_obsolete_configuration(
        {key: value for key, value in source.items() if generated.get(key) != value},
        configuration=configuration,
    )
    for warning in warnings:
        log.warning("Obsolete configuration: %s", warning)
    return warnings


__all__ = [
    "ARCHITECTURE_BOUNDARY_EXCEPTIONS",
    "ArchitectureBoundaryException",
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
    "assert_architecture_exceptions_are_owned",
    "assert_inventory_is_complete",
    "assert_new_admission_allowed",
    "assert_obsolete_configuration",
    "assert_surface_admits_new_work",
    "enforce_obsolete_configuration_at_startup",
    "evaluate_new_admission",
    "get_retirement_record",
    "retirement_record_for_surface",
]
