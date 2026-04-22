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
    INTEGRATION_CI_PROFILE_ID,
    INTEGRATION_CI_TOOL,
    build_dood_tool_definition_payload,
    build_workload_tool_handler,
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
            profileId=validated.profile.id,
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
                    "profileId": validated.profile.id,
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
