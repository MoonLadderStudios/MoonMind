"""deployment.update_compose_stack dispatch contract against the real controller.

The registered tool runs through the production tool dispatcher and submits
to the shipped ``deploy/controller`` endpoint (in-process threaded server,
bearer check, operation store). Only the controller's Compose applier is
replaced. Every result must satisfy the tool's declared output schema.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

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
    ToolFailure,
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
CONTEXT = {
    "idempotency_key": "deployment-update|moonmind|wf-contract",
    "workflow_id": "mm:wf-contract",
}


@pytest.fixture
def controller_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., InProcessController]]:
    load_controller_modules(monkeypatch)
    state_dir = tmp_path / "controller-state"
    state_dir.mkdir()
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    for name in (
        "MOONMIND_CONTROLLER_URL",
        "MOONMIND_CONTROLLER_SECRET",
        "MOONMIND_CONTROLLER_SECRET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
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


def _snapshot():
    return create_registry_snapshot(
        skills=(
            parse_tool_definition(build_deployment_update_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )


def _payload(reference: str = "20260425.1234") -> dict[str, object]:
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
                "reference": reference,
            },
            "mode": "changed_services",
            "removeOrphans": True,
            "wait": True,
            "reason": "Update to tested build",
        },
    }


def _rollback_payload() -> dict[str, object]:
    payload = _payload("stable")
    inputs = dict(payload["inputs"])
    inputs["operationKind"] = "rollback"
    inputs["rollbackSourceActionId"] = "depupd_recent"
    inputs["confirmation"] = (
        "Rollback to ghcr.io/moonladderstudios/moonmind:stable confirmed"
    )
    payload["inputs"] = inputs
    return payload


async def _dispatch(payload: dict[str, object]) -> ToolResult:
    dispatcher = ToolActivityDispatcher()
    register_deployment_update_tool_handler(dispatcher)
    return await execute_tool_activity(
        invocation_payload=payload,
        registry_snapshot=_snapshot(),
        dispatcher=dispatcher,
        context=dict(CONTEXT),
    )


def _assert_declared_outputs(outputs: dict[str, Any]) -> None:
    """Every serialized output satisfies the tool's declared output schema."""
    schema = parse_tool_definition(
        build_deployment_update_tool_definition_payload()
    ).output_schema
    properties = schema["properties"]
    undeclared = set(outputs) - set(properties)
    assert undeclared == set(), f"undeclared output keys: {sorted(undeclared)}"
    missing = set(schema["required"]) - set(outputs)
    assert missing == set(), f"missing required output keys: {sorted(missing)}"
    assert outputs["status"] in properties["status"]["enum"]
    assert outputs["owner"] in properties["owner"]["enum"]
    for key, value in outputs.items():
        expected = properties[key].get("type")
        python_type = {
            "string": str,
            "boolean": bool,
            "array": list,
            "object": dict,
        }.get(expected)
        if python_type is not None:
            assert isinstance(value, python_type), (key, value)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_returns_the_controller_operation(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()

    result = await _dispatch(_payload())

    assert isinstance(result, ToolResult)
    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["stack"] == "moonmind"
    assert result.outputs["owner"] == "controller"
    assert result.outputs["requestedImage"] == (
        "ghcr.io/moonladderstudios/moonmind:20260425.1234"
    )
    assert result.outputs["installedImage"] == result.outputs["requestedImage"]
    assert controller.applied == [result.outputs["operationId"]]
    assert result.progress["state"] == "SUCCEEDED"
    _assert_declared_outputs(result.outputs)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_failed_operation_has_failure_metadata(
    controller_factory: Callable[..., InProcessController],
) -> None:
    def failing(controller: InProcessController, operation: dict) -> None:
        controller.store.record_attempt_error(
            operation["operationId"], error="health check failed"
        )
        raise controller.engine.ApplyError("verify", 1, "health check failed")

    controller_factory(failing)

    result = await _dispatch(_payload())

    assert result.status == "FAILED"
    assert result.outputs["status"] == "FAILED"
    assert result.outputs["failure"]["class"] == "deployment_failure"
    assert "health check failed" in result.outputs["failure"]["reason"]
    assert result.outputs["failure"]["retryable"] is False
    # Retry goes through the controller's explicit Retry, not redelivery.
    assert result.outputs["retryAllowed"] is True
    # No installation was confirmed, so none is reported.
    assert "installedImage" not in result.outputs
    _assert_declared_outputs(result.outputs)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_rollback_dispatch_submits_the_earlier_image_to_the_controller(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()

    result = await _dispatch(_rollback_payload())

    assert result.status == "COMPLETED"
    assert result.outputs["requestedImage"] == (
        "ghcr.io/moonladderstudios/moonmind:stable"
    )
    operation = controller.store.load(result.outputs["operationId"])
    assert operation["desired"]["image"] == "ghcr.io/moonladderstudios/moonmind:stable"
    _assert_declared_outputs(result.outputs)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_deployment_update_tool_dispatch_without_a_controller_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(tmp_path / "absent"))
    monkeypatch.delenv("MOONMIND_CONTROLLER_URL", raising=False)

    with pytest.raises(ToolFailure) as exc_info:
        await _dispatch(_payload())

    assert exc_info.value.error_code == "DEPLOYMENT_CONTROLLER_NOT_INSTALLED"
    assert exc_info.value.retryable is False
