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
        "moonmind.workflows.temporal.workflows.release_canary",
        "ReleaseCanaryWorkflow", "excluded",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.container_job",
        "MoonMindContainerJobWorkflow",
        "operator",
    ),
    # MoonLadderStudios/MoonMind#4192: the native ManifestIngest product is
    # retired and intentionally absent from this registration. New
    # MoonMind.ManifestIngest launches are rejected actionably at the schema,
    # recurring, and service boundaries, existing ManifestIngest-targeted
    # schedules are paused (never recreated) during reconciliation, and the
    # corresponding manifest.compile / manifest.write_summary activity
    # bindings are removed with the workflow. Deploying this removal
    # requires an empty-history cutover verified through the versioned drain
    # gate in ``moonmind.gates.manifest_ingest_drain``
    # (``manifest-ingest-removal-drain-v1``): zero open ManifestIngest
    # histories, zero pending manifest tasks, and zero existing
    # ManifestIngest-targeted schedules, all from live deployment probes.
    # Unobservable dimensions retain the old release for drain; fixture
    # replay is history compatibility, not deployed drainage evidence.
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
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.github_issue_reconcile",
        "MoonMindGitHubIssueReconcileWorkflow",
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

    from moonmind.workflows.temporal.workflows.release_canary import inspect_release_activity
    return (
        inspect_release_activity,
        resolve_adapter_metadata,
        get_activity_route,
        resolve_external_adapter,
        external_adapter_execution_style,
        # Replay/in-flight compatibility: pre-cutover activities have no queue
        # override and remain scheduled on the workflow queue. No new calls
        # route here; retire only through the drain gate in
        # ``moonmind.gates.checkpoint_compat_drain``
        # (MoonLadderStudios/MoonMind#3949): removal requires
        # ``evaluate_checkpoint_compat_drain_observations`` to report zero
        # outstanding old-queue consumers collected from live deployment
        # probes via ``collect_checkpoint_compat_drain_observations`` (see
        # ``render_checkpoint_compat_drain_report`` for the operator
        # procedure). Scoped visibility counts come from
        # ``TemporalExecutionService.get_drain_metrics`` with the workflow
        # task queue passed explicitly.
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
