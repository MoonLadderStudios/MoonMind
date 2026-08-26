"""Docker-backed workload contract helpers."""

from typing import TYPE_CHECKING

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
_UNRESTRICTED_CONTAINER_TOOL_EXPORTS = frozenset(
    {
        "CONTAINER_EXECUTION_CONTEXT_KEY",
        "CONTAINER_RUN_CONTAINER_TOOL",
        "UNRESTRICTED_CONTAINER_TOOL_NAMES",
        "UnrestrictedContainerWorkspace",
        "build_container_execution_context",
        "build_unrestricted_container_tool_definition_payload",
        "is_unrestricted_container_tool",
        "register_unrestricted_container_tool_handler",
        "unrestricted_container_tool_enabled",
    }
)

if TYPE_CHECKING:
    from moonmind.workloads.tool_bridge import (
        CONTAINER_JOB_TOOL_NAMES,
        CONTAINER_RUN_JOB_TOOL,
        build_container_job_tool_definition_payload,
        is_container_job_tool,
    )
    from moonmind.workloads.unrestricted_container_tool import (
        CONTAINER_EXECUTION_CONTEXT_KEY,
        CONTAINER_RUN_CONTAINER_TOOL,
        UNRESTRICTED_CONTAINER_TOOL_NAMES,
        UnrestrictedContainerWorkspace,
        build_container_execution_context,
        build_unrestricted_container_tool_definition_payload,
        is_unrestricted_container_tool,
        register_unrestricted_container_tool_handler,
        unrestricted_container_tool_enabled,
    )

def __getattr__(name: str) -> object:
    if name in _TOOL_BRIDGE_EXPORTS:
        from moonmind.workloads import tool_bridge

        value = getattr(tool_bridge, name)
        globals()[name] = value
        return value
    if name in _UNRESTRICTED_CONTAINER_TOOL_EXPORTS:
        from moonmind.workloads import unrestricted_container_tool

        value = getattr(unrestricted_container_tool, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CONTAINER_EXECUTION_CONTEXT_KEY",
    "CONTAINER_JOB_TOOL_NAMES",
    "CONTAINER_RUN_CONTAINER_TOOL",
    "CONTAINER_RUN_JOB_TOOL",
    "DockerContainerJanitor",
    "DockerWorkloadConcurrencyLimiter",
    "DockerWorkloadLauncher",
    "DockerWorkloadLauncherError",
    "RunnerProfile",
    "RunnerProfileRegistry",
    "UNRESTRICTED_CONTAINER_TOOL_NAMES",
    "UnrestrictedContainerWorkspace",
    "ValidatedWorkloadRequest",
    "WorkloadCredentialMount",
    "WorkloadMount",
    "WorkloadOwnershipMetadata",
    "WorkloadPolicyError",
    "WorkloadRequest",
    "WorkloadResult",
    "build_container_execution_context",
    "build_container_job_tool_definition_payload",
    "build_unrestricted_container_tool_definition_payload",
    "is_container_job_tool",
    "is_unrestricted_container_tool",
    "register_unrestricted_container_tool_handler",
    "unrestricted_container_tool_enabled",
]
