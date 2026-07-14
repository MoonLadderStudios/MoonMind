"""Docker-backed workload contract helpers."""

from moonmind.schemas.workload_models import (
    RunnerProfile,
    ValidatedWorkloadRequest,
    WorkloadCredentialMount,
    WorkloadMount,
    WorkloadOwnershipMetadata,
    WorkloadRequest,
    WorkloadResult,
)
from moonmind.workloads.docker_launcher import (
    DockerContainerJanitor,
    DockerWorkloadConcurrencyLimiter,
    DockerWorkloadLauncher,
    DockerWorkloadLauncherError,
)
from moonmind.workloads.registry import RunnerProfileRegistry, WorkloadPolicyError

_TOOL_BRIDGE_EXPORTS = frozenset(
    {
        "CONTAINER_JOB_TOOL_NAMES",
        "CONTAINER_RUN_JOB_TOOL",
        "build_container_job_tool_definition_payload",
        "is_container_job_tool",
    }
)

def __getattr__(name: str) -> object:
    if name in _TOOL_BRIDGE_EXPORTS:
        from moonmind.workloads import tool_bridge

        value = getattr(tool_bridge, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CONTAINER_JOB_TOOL_NAMES",
    "CONTAINER_RUN_JOB_TOOL",
    "DockerContainerJanitor",
    "DockerWorkloadConcurrencyLimiter",
    "DockerWorkloadLauncher",
    "DockerWorkloadLauncherError",
    "RunnerProfile",
    "RunnerProfileRegistry",
    "ValidatedWorkloadRequest",
    "WorkloadCredentialMount",
    "WorkloadMount",
    "WorkloadOwnershipMetadata",
    "WorkloadPolicyError",
    "WorkloadRequest",
    "WorkloadResult",
    "build_container_job_tool_definition_payload",
    "is_container_job_tool",
]
