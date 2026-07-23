"""Canonical workflow registrations for the Temporal workflow fleet."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import import_module
from typing import Any

from temporalio import workflow

from moonmind.config.settings import TemporalSettings


@dataclass(frozen=True, slots=True)
class WorkflowRegistration:
    """A workflow class and its canonical Temporal type name."""

    module: str
    class_name: str

    def load_class(self) -> type[Any]:
        """Import the workflow class without making topology imports cyclic."""

        return getattr(import_module(self.module), self.class_name)


USER_WORKFLOW_REGISTRATION = WorkflowRegistration(
    "moonmind.workflows.temporal.workflows.run",
    "MoonMindUserWorkflow",
)


STATIC_WORKFLOW_REGISTRATIONS = (
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.container_job",
        "MoonMindContainerJobWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.manifest_ingest",
        "MoonMindManifestIngestWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.provider_profile_manager",
        "MoonMindProviderProfileManagerWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.agent_session",
        "MoonMindAgentSessionWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.managed_session_reconcile",
        "MoonMindManagedSessionReconcileWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup",
        "MoonMindManagedRuntimeWorkspaceCleanupWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.agent_run",
        "MoonMindAgentRun",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.oauth_session",
        "MoonMindOAuthSessionWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.omnigent_oauth_host_janitor",
        "MoonMindOmnigentOAuthHostJanitorWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.merge_automation",
        "MoonMindMergeAutomationWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.pr_resolver",
        "MoonMindPRResolverWorkflow",
    ),
    WorkflowRegistration(
        "moonmind.workflows.temporal.workflows.publication_recovery",
        "MoonMindPublicationRecoveryWorkflow",
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
    )
