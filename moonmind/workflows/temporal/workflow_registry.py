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
    """Return the exact local activities hosted beside deterministic workflows."""

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
        workflow._Definition.must_from_class(registration.load_class()).name:
            registration.projection_scope
        for registration in (USER_WORKFLOW_REGISTRATION, *STATIC_WORKFLOW_REGISTRATIONS)
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
