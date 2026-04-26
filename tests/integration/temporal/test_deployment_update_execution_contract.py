from __future__ import annotations

from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.deployment_execution import (
    ComposeVerification,
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    InMemoryDesiredStateStore,
    InMemoryEvidenceWriter,
    register_deployment_update_tool_handler,
)
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
    build_deployment_update_tool_definition_payload,
)
from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from moonmind.workflows.skills.tool_plan_contracts import ToolResult
from moonmind.workflows.skills.tool_registry import create_registry_snapshot
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition


class HermeticRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple[str, ...]]] = []

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        return {"stack": stack, "phase": phase}

    async def pull(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        self.commands.append(("pull", command))
        return {"ok": True}

    async def up(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        self.commands.append(("up", command))
        return {"ok": True}

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        return ComposeVerification(
            succeeded=True,
            updated_services=("api",),
            running_services=({"name": "api", "state": "running"},),
            details={"requestedImage": requested_image, "resolvedDigest": resolved_digest},
        )


def _snapshot():
    return create_registry_snapshot(
        skills=(parse_tool_definition(build_deployment_update_tool_definition_payload()),),
        artifact_store=InMemoryArtifactStore(),
    )


def _payload() -> dict[str, object]:
    return {
        "id": "deploy-moonmind",
        "tool": {
            "type": "skill",
            "name": DEPLOYMENT_UPDATE_TOOL_NAME,
            "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
        },
        "inputs": {
            "stack": "moonmind",
            "image": {
                "repository": "ghcr.io/moonladderstudios/moonmind",
                "reference": "20260425.1234",
            },
            "mode": "changed_services",
            "removeOrphans": True,
            "wait": True,
            "reason": "Update to tested build",
        },
    }


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_returns_structured_result() -> None:
    runner = HermeticRunner()
    executor = DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=runner,
    )
    dispatcher = ToolActivityDispatcher()
    register_deployment_update_tool_handler(dispatcher, executor=executor)

    result = await execute_tool_activity(
        invocation_payload=_payload(),
        registry_snapshot=_snapshot(),
        dispatcher=dispatcher,
        context={"deployment_runner_mode": "privileged_worker"},
    )

    assert isinstance(result, ToolResult)
    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["stack"] == "moonmind"
    assert result.outputs["beforeStateArtifactRef"].startswith("art:sha256:")
    assert runner.commands[0][0] == "pull"
    assert runner.commands[1][0] == "up"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_surfaces_deployment_locked() -> None:
    lock_manager = DeploymentUpdateLockManager()
    lease = await lock_manager.acquire("moonmind")
    executor = DeploymentUpdateExecutor(
        lock_manager=lock_manager,
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=HermeticRunner(),
    )
    dispatcher = ToolActivityDispatcher()
    register_deployment_update_tool_handler(dispatcher, executor=executor)

    try:
        with pytest.raises(ToolFailure) as exc_info:
            await execute_tool_activity(
                invocation_payload=_payload(),
                registry_snapshot=_snapshot(),
                dispatcher=dispatcher,
            )
    finally:
        await lease.release()

    assert exc_info.value.error_code == "DEPLOYMENT_LOCKED"
    assert exc_info.value.retryable is False
