"""Public schema exports with lazy loading to avoid import cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "SecretRef": ("moonmind.schemas.manifest_models", "SecretRef"),
    "AuthItem": ("moonmind.schemas.manifest_models", "AuthItem"),
    "Defaults": ("moonmind.schemas.manifest_models", "Defaults"),
    "Reader": ("moonmind.schemas.manifest_models", "Reader"),
    "Spec": ("moonmind.schemas.manifest_models", "Spec"),
    "Manifest": ("moonmind.schemas.manifest_models", "Manifest"),
    "export_schema": ("moonmind.schemas.manifest_models", "export_schema"),
    "TaskActionAvailability": (
        "moonmind.schemas.task_compatibility_models",
        "TaskActionAvailability",
    ),
    "TaskDebugContext": (
        "moonmind.schemas.task_compatibility_models",
        "TaskDebugContext",
    ),
    "TaskCompatibilityRow": (
        "moonmind.schemas.task_compatibility_models",
        "TaskCompatibilityRow",
    ),
    "TaskCompatibilityDetail": (
        "moonmind.schemas.task_compatibility_models",
        "TaskCompatibilityDetail",
    ),
    "TaskCompatibilityListResponse": (
        "moonmind.schemas.task_compatibility_models",
        "TaskCompatibilityListResponse",
    ),
    "CreateExecutionRequest": (
        "moonmind.schemas.temporal_models",
        "CreateExecutionRequest",
    ),
    "UpdateExecutionRequest": (
        "moonmind.schemas.temporal_models",
        "UpdateExecutionRequest",
    ),
    "UpdateExecutionResponse": (
        "moonmind.schemas.temporal_models",
        "UpdateExecutionResponse",
    ),
    "SignalExecutionRequest": (
        "moonmind.schemas.temporal_models",
        "SignalExecutionRequest",
    ),
    "CancelExecutionRequest": (
        "moonmind.schemas.temporal_models",
        "CancelExecutionRequest",
    ),
    "ExecutionModel": ("moonmind.schemas.temporal_models", "ExecutionModel"),
    "ExecutionListResponse": (
        "moonmind.schemas.temporal_models",
        "ExecutionListResponse",
    ),
    "CreateJobRequest": ("moonmind.schemas.agent_queue_models", "CreateJobRequest"),
    "CreateWorkerTokenRequest": (
        "moonmind.schemas.agent_queue_models",
        "CreateWorkerTokenRequest",
    ),
    "AppendJobEventRequest": (
        "moonmind.schemas.agent_queue_models",
        "AppendJobEventRequest",
    ),
    "ArtifactModel": ("moonmind.schemas.agent_queue_models", "ArtifactModel"),
    "ArtifactListResponse": (
        "moonmind.schemas.agent_queue_models",
        "ArtifactListResponse",
    ),
    "ClaimJobRequest": ("moonmind.schemas.agent_queue_models", "ClaimJobRequest"),
    "ClaimJobResponse": ("moonmind.schemas.agent_queue_models", "ClaimJobResponse"),
    "HeartbeatRequest": ("moonmind.schemas.agent_queue_models", "HeartbeatRequest"),
    "CompleteJobRequest": ("moonmind.schemas.agent_queue_models", "CompleteJobRequest"),
    "FailJobRequest": ("moonmind.schemas.agent_queue_models", "FailJobRequest"),
    "JobModel": ("moonmind.schemas.agent_queue_models", "JobModel"),
    "JobListResponse": ("moonmind.schemas.agent_queue_models", "JobListResponse"),
    "JobEventModel": ("moonmind.schemas.agent_queue_models", "JobEventModel"),
    "JobEventListResponse": (
        "moonmind.schemas.agent_queue_models",
        "JobEventListResponse",
    ),
    "WorkerTokenModel": ("moonmind.schemas.agent_queue_models", "WorkerTokenModel"),
    "WorkerTokenCreateResponse": (
        "moonmind.schemas.agent_queue_models",
        "WorkerTokenCreateResponse",
    ),
    "WorkerTokenListResponse": (
        "moonmind.schemas.agent_queue_models",
        "WorkerTokenListResponse",
    ),
    "SpecWorkflowRunModel": (
        "moonmind.schemas.workflow_models",
        "SpecWorkflowRunModel",
    ),
    "WorkflowTaskStateModel": (
        "moonmind.schemas.workflow_models",
        "WorkflowTaskStateModel",
    ),
    "WorkflowArtifactModel": (
        "moonmind.schemas.workflow_models",
        "WorkflowArtifactModel",
    ),
    "WorkflowCredentialAuditModel": (
        "moonmind.schemas.workflow_models",
        "WorkflowCredentialAuditModel",
    ),
    "WorkflowRunCollectionResponse": (
        "moonmind.schemas.workflow_models",
        "WorkflowRunCollectionResponse",
    ),
    "CreateWorkflowRunRequest": (
        "moonmind.schemas.workflow_models",
        "CreateWorkflowRunRequest",
    ),
}

__all__ = sorted(_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
