from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import create_registry_snapshot
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workloads.tool_bridge import (
    CONTAINER_RUN_CONTAINER_TOOL,
    INTEGRATION_CI_PROFILE_ID,
    INTEGRATION_CI_TOOL,
    build_dood_tool_definition_payload,
    build_workload_tool_handler,
    register_workload_tool_handlers,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


WORKSPACE_ROOT = Path("/work/agent_jobs")


def _integration_profile_payload() -> dict[str, object]:
    return {
        "id": INTEGRATION_CI_PROFILE_ID,
        "kind": "one_shot",
        "image": "ghcr.io/moonladderstudios/moonmind-integration-ci:1.0",
        "entrypoint": ["/bin/bash"],
        "commandWrapper": ["-lc"],
        "workdirTemplate": "/work/agent_jobs/${task_run_id}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "envAllowlist": ["CI", "DOCKER_HOST", "MOONMIND_DOCKER_NETWORK"],
        "networkPolicy": "bridge",
        "timeoutSeconds": 3600,
        "maxTimeoutSeconds": 7200,
    }


class _FakeLauncher:
    def __init__(self) -> None:
        self.validated: Any | None = None

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=(validated.profile.id if validated.profile is not None else validated.request.tool_name),
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            stdoutRef="art:sha256:stdout",
            stderrRef="art:sha256:stderr",
            diagnosticsRef="art:sha256:diagnostics",
            outputRefs={"runtime.diagnostics": "art:sha256:diagnostics"},
            metadata={
                "workload": {
                    "toolName": validated.request.tool_name,
                    "profileId": (validated.profile.id if validated.profile is not None else validated.request.tool_name),
                    "command": list(validated.request.command),
                },
                "artifactPublication": {"status": "complete"},
            },
        )


async def test_moonmind_integration_ci_routes_through_curated_workload_tool() -> None:
    snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(
                build_dood_tool_definition_payload(
                    name=INTEGRATION_CI_TOOL,
                    version="1.0",
                )
            ),
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_integration_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    dispatcher.register_skill(
        skill_name=INTEGRATION_CI_TOOL,
        version="1.0",
        handler=build_workload_tool_handler(
            tool_name=INTEGRATION_CI_TOOL,
            registry=registry,
            launcher=launcher,
        ),
    )

    result = await execute_tool_activity(
        invocation_payload={
            "id": "step-integration-ci",
            "tool": {
                "type": "skill",
                "name": INTEGRATION_CI_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/integration-ci",
                "envOverrides": {"CI": "1"},
            },
        },
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-integration-ci"},
    )

    assert launcher.validated is not None
    assert launcher.validated.request.profile_id == INTEGRATION_CI_PROFILE_ID
    assert launcher.validated.request.command == ("./tools/test_integration.sh",)
    assert result.status == "COMPLETED"
    assert result.outputs["profileId"] == INTEGRATION_CI_PROFILE_ID
    assert result.outputs["stdoutRef"] == "art:sha256:stdout"
    assert result.outputs["stderrRef"] == "art:sha256:stderr"
    assert result.outputs["diagnosticsRef"] == "art:sha256:diagnostics"


async def test_workflow_docker_mode_keeps_registry_and_dispatch_aligned() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_integration_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )

    disabled_dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        disabled_dispatcher,
        registry=registry,
        launcher=_FakeLauncher(),
        workflow_docker_mode="disabled",
    )
    assert disabled_dispatcher._skill_handlers == {}

    profiles_snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(
                build_dood_tool_definition_payload(
                    name=CONTAINER_RUN_CONTAINER_TOOL,
                    version="1.0",
                )
            ),
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    profiles_dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        profiles_dispatcher,
        registry=registry,
        launcher=_FakeLauncher(),
        workflow_docker_mode="profiles",
    )

    with pytest.raises(Exception) as exc_info:
        await execute_tool_activity(
            invocation_payload={
                "id": "step-run-container",
                "tool": {
                    "type": "skill",
                    "name": CONTAINER_RUN_CONTAINER_TOOL,
                    "version": "1.0",
                },
                "inputs": {
                    "repoDir": "/work/agent_jobs/wf-1/repo",
                    "artifactsDir": "/work/agent_jobs/wf-1/artifacts/integration-ci",
                    "scratchDir": "/work/agent_jobs/wf-1/scratch",
                    "image": "ghcr.io/example/runtime:1.2.3",
                    "command": ["pytest", "-q"],
                },
            },
            registry_snapshot=profiles_snapshot,
            dispatcher=profiles_dispatcher,
            context={"workflow_id": "wf-1", "node_id": "step-run-container"},
        )
    message = getattr(exc_info.value, "message", str(exc_info.value))
    assert "No mm.tool.execute handler registered" in message or "PERMISSION_DENIED" in message

    unrestricted_dispatcher = ToolActivityDispatcher()
    launcher = _FakeLauncher()
    register_workload_tool_handlers(
        unrestricted_dispatcher,
        registry=registry,
        launcher=launcher,
        workflow_docker_mode="unrestricted",
    )
    unrestricted_snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(
                build_dood_tool_definition_payload(
                    name=CONTAINER_RUN_CONTAINER_TOOL,
                    version="1.0",
                )
            ),
        ),
        artifact_store=InMemoryArtifactStore(),
    )

    result = await execute_tool_activity(
        invocation_payload={
            "id": "step-run-container",
            "tool": {
                "type": "skill",
                "name": CONTAINER_RUN_CONTAINER_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/integration-ci",
                "scratchDir": "/work/agent_jobs/wf-1/scratch",
                "image": "ghcr.io/example/runtime:1.2.3",
                "command": ["pytest", "-q"],
            },
        },
        registry_snapshot=unrestricted_snapshot,
        dispatcher=unrestricted_dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-run-container"},
    )

    assert launcher.validated is not None
    assert launcher.validated.profile is None
    assert result.status == "COMPLETED"
