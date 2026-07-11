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
    task_queues: tuple[str, ...]
    activity_handlers: tuple[Any, ...]
    activity_types: tuple[str, ...]
    fingerprint: str


def _registry_fingerprint(parts: tuple[str, ...]) -> str:
    payload = "\n".join(parts).encode("utf-8")
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
    *,
    task_queues: tuple[str, ...] | None = None,
    activity_handlers: tuple[Any, ...] = (),
) -> WorkflowWorkerSpec:
    """Return the single immutable specification for construction and diagnostics."""

    workflow_types = workflow_fleet_workflow_types(temporal_settings)
    workflow_classes = workflow_fleet_workflow_classes()
    if len(workflow_types) != len(workflow_classes):
        raise RuntimeError("workflow registry type/class count mismatch")
    from temporalio import workflow

    loaded_types = tuple(
        workflow._Definition.must_from_class(workflow_class).name
        for workflow_class in workflow_classes
    )
    if loaded_types != workflow_types:
        raise RuntimeError(
            "workflow registry declared/loaded type mismatch: "
            f"declared={workflow_types!r} loaded={loaded_types!r}"
        )
    resolved_task_queues = task_queues or (
        temporal_settings.workflow_task_queue,
    )
    activity_types = tuple(
        str(getattr(getattr(handler, "__temporal_activity_definition", None), "name", None)
            or getattr(handler, "__name__", type(handler).__name__))
        for handler in activity_handlers
    )
    fingerprint_parts = (
        "fleet=workflow",
        *(f"queue={value}" for value in resolved_task_queues),
        *(f"workflow={value}" for value in workflow_types),
        *(f"activity={value}" for value in activity_types),
    )
    return WorkflowWorkerSpec(
        workflow_types=workflow_types,
        workflow_classes=workflow_classes,
        task_queues=resolved_task_queues,
        activity_handlers=activity_handlers,
        activity_types=activity_types,
        fingerprint=_registry_fingerprint(fingerprint_parts),
    )
