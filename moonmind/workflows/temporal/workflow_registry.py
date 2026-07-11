"""Canonical workflow registrations for the Temporal workflow fleet."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
from importlib import import_module
from typing import Any

from moonmind.config.settings import TemporalSettings
from moonmind.workflows.temporal.hard_switch_cutover import (
    registered_user_workflow_type,
)


@dataclass(frozen=True, slots=True)
class WorkflowRegistration:
    """A workflow class and its canonical Temporal type name."""

    workflow_type: str
    module: str
    class_name: str

    def load_class(self) -> type[Any]:
        """Import the workflow class without making topology imports cyclic."""

        return getattr(import_module(self.module), self.class_name)


@dataclass(frozen=True, slots=True)
class WorkflowWorkerSpec:
    """Immutable executable identity for a workflow-fleet deployment."""

    workflow_types: tuple[str, ...]
    workflow_classes: tuple[type[Any], ...]
    fingerprint: str


def _registry_fingerprint(workflow_types: tuple[str, ...]) -> str:
    payload = "\n".join(workflow_types).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


USER_WORKFLOW_REGISTRATION = WorkflowRegistration(
    "MoonMind.UserWorkflow",
    "moonmind.workflows.temporal.workflows.run",
    "MoonMindUserWorkflow",
)


STATIC_WORKFLOW_REGISTRATIONS = (
    WorkflowRegistration(
        "MoonMind.ManifestIngest",
        "moonmind.workflows.temporal.workflows.manifest_ingest",
        "MoonMindManifestIngestWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.ProviderProfileManager",
        "moonmind.workflows.temporal.workflows.provider_profile_manager",
        "MoonMindProviderProfileManagerWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.AgentSession",
        "moonmind.workflows.temporal.workflows.agent_session",
        "MoonMindAgentSessionWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.ManagedSessionReconcile",
        "moonmind.workflows.temporal.workflows.managed_session_reconcile",
        "MoonMindManagedSessionReconcileWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.ManagedRuntimeWorkspaceCleanup",
        "moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup",
        "MoonMindManagedRuntimeWorkspaceCleanupWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.AgentRun",
        "moonmind.workflows.temporal.workflows.agent_run",
        "MoonMindAgentRun",
    ),
    WorkflowRegistration(
        "MoonMind.OAuthSession",
        "moonmind.workflows.temporal.workflows.oauth_session",
        "MoonMindOAuthSessionWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.MergeAutomation",
        "moonmind.workflows.temporal.workflows.merge_automation",
        "MoonMindMergeAutomationWorkflow",
    ),
    WorkflowRegistration(
        "MoonMind.PRResolver",
        "moonmind.workflows.temporal.workflows.pr_resolver",
        "MoonMindPRResolverWorkflow",
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

    return (
        registered_user_workflow_type(temporal_settings),
        *(registration.workflow_type for registration in STATIC_WORKFLOW_REGISTRATIONS),
    )


def workflow_fleet_worker_spec(
    temporal_settings: TemporalSettings,
) -> WorkflowWorkerSpec:
    """Return the single immutable specification for construction and diagnostics."""

    workflow_types = workflow_fleet_workflow_types(temporal_settings)
    workflow_classes = workflow_fleet_workflow_classes()
    if len(workflow_types) != len(workflow_classes):
        raise RuntimeError("workflow registry type/class count mismatch")
    return WorkflowWorkerSpec(
        workflow_types=workflow_types,
        workflow_classes=workflow_classes,
        fingerprint=_registry_fingerprint(workflow_types),
    )
