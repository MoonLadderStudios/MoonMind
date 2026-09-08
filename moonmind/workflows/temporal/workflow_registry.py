"""Canonical workflow registrations for the Temporal workflow fleet."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import import_module
from typing import Any, Literal

from temporalio import workflow

from moonmind.config.settings import TemporalSettings


@dataclass(frozen=True, slots=True)
class WorkflowRegistration:
    """A workflow class and its canonical Temporal type name."""

    module: str
    class_name: str
    projection_scope: Literal["product", "operator", "excluded"]

    def load_class(self) -> type[Any]:
        """Import the workflow class without making topology imports cyclic."""

        return getattr(import_module(self.module), self.class_name)


class WorkflowRegistrationError(ValueError):
    """A production workflow registration is invalid or ambiguous."""


PROJECTION_SCOPES = ("product", "operator", "excluded")


USER_WORKFLOW_REGISTRATION = WorkflowRegistration(
    "moonmind.workflows.temporal.workflows.run",
    "MoonMindUserWorkflow",
    "product",
)


STATIC_WORKFLOW_REGISTRATIONS = (
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.container_job",
        "MoonMindContainerJobWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.manifest_ingest",
        "MoonMindManifestIngestWorkflow",
        "product",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.control_stop_continuation",
        "MoonMindControlStopContinuationWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.provider_profile_manager",
        "MoonMindProviderProfileManagerWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.agent_session",
        "MoonMindAgentSessionWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.managed_session_reconcile",
        "MoonMindManagedSessionReconcileWorkflow",
        "excluded",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup",
        "MoonMindManagedRuntimeWorkspaceCleanupWorkflow",
        "excluded",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.agent_run",
        "MoonMindAgentRun",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.omnigent_session",
        "MoonMindOmnigentSessionWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.checkpoint_branch_turn",
        "MoonMindCheckpointBranchTurnWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.oauth_session",
        "MoonMindOAuthSessionWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.omnigent_oauth_host_janitor",
        "MoonMindOmnigentOAuthHostJanitorWorkflow",
        "excluded",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.merge_automation",
        "MoonMindMergeAutomationWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.pr_resolver",
        "MoonMindPRResolverWorkflow",
        "operator",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.publication_recovery",
        "MoonMindPublicationRecoveryWorkflow",
        "operator",
    ),
)


def raw_workflow_registrations() -> tuple[WorkflowRegistration, ...]:
    """Return every production registration before any name-keyed reduction.

    Validate this sequence — never only the ``workflow_projection_scopes()``
    dictionary — so duplicate Temporal names cannot hide behind key
    replacement (MoonLadderStudios/MoonMind#3959).
    """

    return (USER_WORKFLOW_REGISTRATION, *STATIC_WORKFLOW_REGISTRATIONS)


def validate_workflow_registrations(
    registrations: tuple[WorkflowRegistration, ...] | None = None,
) -> dict[str, WorkflowRegistration]:
    """Resolve raw registrations to Temporal names, failing before formatting.

    Raises :class:`WorkflowRegistrationError` on duplicate Temporal names
    within the same production worker, unresolved classes, or missing
    required role/owner metadata. Returns the validated name-keyed map.
    """

    resolved: dict[str, WorkflowRegistration] = {}
    owners: dict[str, list[str]] = {}
    for registration in (
        raw_workflow_registrations() if registrations is None else registrations
    ):
        module = (registration.module or "").strip()
        class_name = (registration.class_name or "").strip()
        scope = (registration.projection_scope or "").strip()
        if not module or not class_name:
            raise WorkflowRegistrationError(
                "Workflow registration is missing required owner metadata: "
                f"{registration!r}"
            )
        if scope not in PROJECTION_SCOPES:
            raise WorkflowRegistrationError(
                f"Workflow registration {module}.{class_name} declares "
                f"unsupported projection scope {scope!r}; expected one of: "
                f"{', '.join(PROJECTION_SCOPES)}"
            )
        try:
            workflow_class = registration.load_class()
        except (ImportError, AttributeError) as exc:
            raise WorkflowRegistrationError(
                f"Workflow registration {module}.{class_name} cannot be "
                f"resolved: {exc}"
            ) from exc
        try:
            temporal_name = workflow._Definition.must_from_class(workflow_class).name
        except Exception as exc:
            raise WorkflowRegistrationError(
                f"Workflow class {module}.{class_name} has no Temporal "
                f"definition name: {exc}"
            ) from exc
        if not temporal_name:
            raise WorkflowRegistrationError(
                f"Workflow class {module}.{class_name} resolved to an empty "
                "Temporal type name"
            )
        owners.setdefault(temporal_name, []).append(f"{module}.{class_name}")
        if temporal_name in resolved:
            raise WorkflowRegistrationError(
                f"Duplicate Temporal workflow name {temporal_name!r} registered by "
                f"{owners[temporal_name][0]} and {module}.{class_name}; "
                "registrations within one production worker must be unique "
                "before dictionary reduction"
            )
        resolved[temporal_name] = registration
    return resolved


@cache
def workflow_fleet_workflow_classes() -> tuple[type[Any], ...]:
    """Return the exact workflow classes registered by production workers."""

    return (
        USER_WORKFLOW_REGISTRATION.load_class(),
        *(registration.load_class() for registration in STATIC_WORKFLOW_REGISTRATIONS),
    )


def workflow_fleet_workflow_types(
    temporal_settings: TemporalSettings,
) -> tuple[str, ...]:
    """Return type names from the same registry used to construct workers."""

    del temporal_settings
    return tuple(
        workflow._Definition.must_from_class(workflow_class).name
        for workflow_class in workflow_fleet_workflow_classes()
    )


@cache
def workflow_fleet_activity_handlers() -> tuple[Any, ...]:
    """Return the exact activity handlers hosted on the workflow fleet.

    These are regular Temporal activities colocated with deterministic
    workflow code — not Temporal Local Activities. ``agent_run`` helpers are
    the current workflow-queue lane; the checkpoint-branch handlers below
    remain only for pre-cutover histories recorded without a queue override
    (replay/in-flight compatibility) and must not be read as the live
    scheduling pattern.
    """

    from moonmind.workflows.temporal.workflows.agent_run import (
        external_adapter_execution_style,
        get_activity_route,
        resolve_adapter_metadata,
        resolve_external_adapter,
    )

    return (
        resolve_adapter_metadata,
        get_activity_route,
        resolve_external_adapter,
        external_adapter_execution_style,
        # Replay/in-flight compatibility: pre-cutover activities have no queue
        # override and remain scheduled on the workflow queue. No new calls
        # route here; retire after those histories and pending tasks drain.
        *checkpoint_branch_activity_handlers(),
    )


@cache
def workflow_projection_scopes() -> dict[str, str]:
    """Classify the actual production registrations without guessing type names."""
    return {
        temporal_name: registration.projection_scope
        for temporal_name, registration in validate_workflow_registrations().items()
    }


def workflow_projection_scope(workflow_type: str | None) -> str:
    return workflow_projection_scopes().get(workflow_type or "", "unknown")


def product_workflow_types() -> tuple[str, ...]:
    return tuple(name for name, scope in workflow_projection_scopes().items() if scope == "product")


class WorkflowProjectionExcluded(ValueError):
    """The Temporal type has no admission to product execution views."""

    def __init__(self, workflow_type: str | None) -> None:
        self.scope = workflow_projection_scope(workflow_type)
        self.code = f"workflow_type_{'operator_only' if self.scope == 'operator' else self.scope}"
        super().__init__(f"{self.code}: {workflow_type!r} ({self.scope})")


def require_product_projection(workflow_type: str | None) -> None:
    if workflow_projection_scope(workflow_type) != "product":
        raise WorkflowProjectionExcluded(workflow_type)


@cache
def checkpoint_branch_activity_handlers() -> tuple[Any, ...]:
    """One I/O implementation shared by artifact workers and retained histories."""

    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        mark_checkpoint_branch_turn_running,
        persist_checkpoint_branch_turn_terminal,
        persist_checkpoint_branch_turn_terminal_rejection,
    )

    return (
        mark_checkpoint_branch_turn_running,
        persist_checkpoint_branch_turn_terminal,
        persist_checkpoint_branch_turn_terminal_rejection,
    )


#: Activity types for checkpoint-branch persistence. Single source of truth for
#: the new-write routing, compatibility retention, and retirement gate below.
#: MoonLadderStudios/MoonMind#3949.
CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES: tuple[str, ...] = (
    "checkpoint_branch.turn.mark_running",
    "checkpoint_branch.turn.persist_terminal",
    "checkpoint_branch.turn.persist_terminal_rejection",
)

#: New-only workflow-queue helpers. These are deterministic/metadata reads with
#: the ``workflow`` capability class; they never require artifact-store,
#: provider, Docker, or database authority beyond Temporal itself.
WORKFLOW_FLEET_NEW_ONLY_HELPER_ACTIVITY_TYPES: tuple[str, ...] = (
    "integration.resolve_adapter_metadata",
    "integration.get_activity_route",
    "integration.resolve_external_adapter",
    "integration.external_adapter_execution_style",
)


def checkpoint_branch_persistence_contract() -> dict[str, Any]:
    """Describe new-write routing vs retained compatibility vs final topology.

    New histories schedule the three persistence activities on the artifacts
    fleet via the ``checkpoint-branch-artifact-fleet-v1`` patch marker
    (``CheckpointBranchTurn._persistence_route_options``). Pre-marker histories
    retain their recorded workflow-queue behavior and keep executing against
    the retained ``workflow_fleet_activity_handlers`` below.

    Retirement reuses existing mechanisms only: Temporal Visibility drain
    metrics (``TemporalClientAdapter.get_drain_metrics``), worker-versioning
    build identity (``build_worker_spec`` deployment/build IDs), and the
    pre-cutover replay fixtures under
    ``tests/fixtures/temporal/checkpoint_before_artifacts_fleet``. Fixture
    replay proves history compatibility, never deployed drainage. Removal of
    the retained workflow-queue handlers is blocked until old tasks,
    retained histories, and supported resets have a verified disposition.
    """

    from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
        CHECKPOINT_BRANCH_ARTIFACT_FLEET_PATCH,
    )

    return {
        "patch_marker": CHECKPOINT_BRANCH_ARTIFACT_FLEET_PATCH,
        "activity_types": CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES,
        "new_write": {
            "fleet": "artifacts",
            "capability_class": "artifacts",
            "route_source": (
                "CheckpointBranchTurn._persistence_route_options "
                "with checkpoint-branch-artifact-fleet-v1"
            ),
        },
        "retained_compatibility": {
            "fleet": "workflow",
            "queue_behavior": "recorded pre-marker workflow queue (no override)",
            "handlers": "workflow_fleet_activity_handlers (same implementations)",
            "reason": "replay/in-flight compatibility for pre-cutover histories",
        },
        "final_topology": {
            "workflow_fleet": "four metadata/adapter helpers only",
            "artifacts_fleet": "single persistence implementation",
        },
        "drain_owner": {
            "old_tasks": "artifacts-fleet owner via #3946 persistence ownership",
            "retained_histories": (
                "Temporal Visibility drain metrics + worker-versioning build IDs"
            ),
            "supported_resets": "existing reset/versioning cutover, not a new mode",
            "fixture_role": "replay compatibility only; not deployed-drain proof",
        },
        "removal_blocked": True,
        "removal_gate": (
            "delete retained workflow-queue persistence handlers, dead DI, and "
            "now-unneeded permissions together only after old consumers have a "
            "verified drain disposition"
        ),
    }


def workflow_fleet_capability_inventory() -> dict[str, Any]:
    """Separate new-only needs from retained old-task I/O authority.

    A worker intentionally executing old persistence tasks still needs
    narrowly scoped artifact-store/database authority until those tasks drain.
    Queue separation alone is not privilege separation; the actual
    process/container boundary is the fleet service
    (``temporal-worker-workflow`` vs ``temporal-worker-artifacts``) with its
    documented capabilities, privileges, secrets, mounts, and egress policy.
    """

    return {
        "new_only_helpers": {
            "activity_types": WORKFLOW_FLEET_NEW_ONLY_HELPER_ACTIVITY_TYPES,
            "capability_class": "workflow",
            "required_authority": ("temporal",),
            "forbidden": (
                "artifacts",
                "llm",
                "sandbox",
                "agent_runtime",
                "docker_workload",
                "provider_tokens",
            ),
        },
        "retained_compatibility_handlers": {
            "activity_types": CHECKPOINT_BRANCH_PERSISTENCE_ACTIVITY_TYPES,
            "capability_class": "artifacts",
            "required_authority": ("artifact_store", "database"),
            "scope": (
                "narrowly scoped I/O only while pre-cutover tasks/histories "
                "remain; do not strip before the drain owner confirms disposition"
            ),
        },
        "boundary": {
            "workflow_service": "temporal-worker-workflow",
            "artifacts_service": "temporal-worker-artifacts",
            "note": "queue separation is not privilege separation",
        },
    }
