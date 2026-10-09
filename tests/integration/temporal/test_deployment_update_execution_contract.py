"""deployment.update_compose_stack dispatch contract against the real controller.

The tool is an adapter onto the standalone deployment controller
(MoonLadderStudios/MoonMind#4502). Dispatch runs the registered handler and
the shipped ``deploy/controller`` endpoint in process; only the controller's
Compose applier is replaced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.deployment_execution import (
    register_deployment_update_tool_handler,
)
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    build_deployment_update_tool_definition_payload,
)
from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import (
    ToolResult,
    parse_tool_definition,
)
from moonmind.workflows.skills.tool_registry import create_registry_snapshot
from tests.support.deployment_controller import (
    InProcessController,
    forget_controller_modules,
    install_controller_state,
    load_controller_modules,
)

SECRET = "contract-test-secret-value"
DIGEST = "sha256:" + "e" * 64
CONTEXT = {
    "idempotency_key": "deployment-update|moonmind|contract-wf",
    "workflow_id": "mm:contract-wf",
}


@pytest.fixture
def controller_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., InProcessController]]:
    load_controller_modules(monkeypatch)
    state_dir = tmp_path / "controller-state"
    state_dir.mkdir()
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET", raising=False)
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)
    started: list[InProcessController] = []

    def start(applier=None) -> InProcessController:
        controller = InProcessController(state_dir, secret=SECRET, applier=applier)
        started.append(controller)
        install_controller_state(state_dir, port=controller.port, secret=SECRET)
        monkeypatch.setenv("MOONMIND_CONTROLLER_URL", controller.url)
        return controller

    yield start
    for controller in started:
        controller.close()
    forget_controller_modules()


def _definition():
    return parse_tool_definition(build_deployment_update_tool_definition_payload())


def _snapshot():
    return create_registry_snapshot(
        skills=(_definition(),),
        artifact_store=InMemoryArtifactStore(),
    )


def _payload() -> dict[str, object]:
    return {
        "id": "deploy-moonmind",
        "tool": {
            "type": "skill",
            "name": DEPLOYMENT_UPDATE_TOOL_NAME,
        },
        "inputs": {
            "stack": "moonmind",
            "image": {
                "repository": "ghcr.io/moonladderstudios/moonmind",
                "reference": DIGEST,
            },
            "mode": "changed_services",
            "reason": "Update to tested build",
        },
    }


async def _dispatch() -> ToolResult:
    dispatcher = ToolActivityDispatcher()
    register_deployment_update_tool_handler(dispatcher)
    return await execute_tool_activity(
        invocation_payload=_payload(),
        registry_snapshot=_snapshot(),
        dispatcher=dispatcher,
        context=dict(CONTEXT),
    )


def _assert_within_output_schema(outputs: dict[str, object]) -> None:
    # The schema sets ``additionalProperties: False`` and later plan steps
    # reference these keys, so the payload and the declaration must agree.
    schema = _definition().output_schema
    undeclared = set(outputs) - set(schema["properties"])
    assert undeclared == set(), f"undeclared output keys: {sorted(undeclared)}"
    assert set(schema["required"]) <= set(outputs)
    assert outputs["status"] in schema["properties"]["status"]["enum"]
    assert all(value is not None for value in outputs.values())


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_returns_the_controller_operation(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()

    result = await _dispatch()

    assert isinstance(result, ToolResult)
    assert result.status == "COMPLETED"
    _assert_within_output_schema(dict(result.outputs))
    assert result.outputs["owner"] == "controller"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["resolvedDigest"] == DIGEST
    assert controller.applied == [result.outputs["operationId"]]
    assert result.progress["state"] == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_failed_apply_has_failure_metadata(
    controller_factory: Callable[..., InProcessController],
) -> None:
    def failing(controller: InProcessController, operation: dict) -> None:
        controller.store.record_attempt_error(
            operation["operationId"], error="up failed: api health check failed"
        )
        raise controller.engine.ApplyError("up", 1, "api health check failed")

    controller_factory(failing)

    result = await _dispatch()

    assert result.status == "FAILED"
    _assert_within_output_schema(dict(result.outputs))
    assert result.outputs["status"] == "FAILED"
    assert "installedImage" not in result.outputs
    assert result.outputs["failure"]["class"] == "deployment_failure"
    assert "api health check failed" in result.outputs["failure"]["reason"]
    assert result.outputs["failure"]["retryable"] is False
    assert result.outputs["retryAllowed"] is True
