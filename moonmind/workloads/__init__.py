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
        "CONTAINER_RUN_WORKLOAD_TOOL",
        "DEFAULT_UNREAL_PROFILE_ID",
        "DOOD_TOOL_NAMES",
        "UNREAL_RUN_TESTS_TOOL",
        "build_dood_tool_definition_payload",
        "build_workload_tool_handler",
        "is_dood_tool",
        "register_workload_tool_handlers",
    }
)

def __getattr__(name: str) -> object:
    if name in _TOOL_BRIDGE_EXPORTS:
        from moonmind.workloads import tool_bridge

        return getattr(tool_bridge, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CONTAINER_RUN_WORKLOAD_TOOL",
    "DEFAULT_UNREAL_PROFILE_ID",
    "DOOD_TOOL_NAMES",
    "DockerContainerJanitor",
    "DockerWorkloadConcurrencyLimiter",
    "DockerWorkloadLauncher",
    "DockerWorkloadLauncherError",
    "RunnerProfile",
    "RunnerProfileRegistry",
    "UNREAL_RUN_TESTS_TOOL",
    "ValidatedWorkloadRequest",
    "WorkloadCredentialMount",
    "WorkloadMount",
    "WorkloadOwnershipMetadata",
    "WorkloadPolicyError",
    "WorkloadRequest",
    "WorkloadResult",
    "build_dood_tool_definition_payload",
    "build_workload_tool_handler",
    "is_dood_tool",
    "register_workload_tool_handlers",
]
