"""deployment.update_compose_stack is only a submit/observe adapter once a controller is installed.

A workflow that requests the typed update tool must not become a second
updater beside the standalone controller: it submits the same controller
operation under its durable execution identity and observes the result.
Runs against the in-process ``deploy/controller`` endpoint.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from moonmind.workflows.skills import deployment_controller as controller_client
from moonmind.workflows.skills import deployment_execution
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from tests.support.deployment_controller import (
    DEFAULT_TARGET,
    InProcessController,
    forget_controller_modules,
    install_controller_state,
    load_controller_modules,
    slow_applier,
)

SECRET = "adapter-test-secret-value"
DIGEST = "sha256:" + "d" * 64
IMAGE = f"ghcr.io/moonladderstudios/moonmind@{DIGEST}"
INPUTS = {
    "stack": "moonmind",
    "image": {"repository": "ghcr.io/moonladderstudios/moonmind", "reference": DIGEST},
    "mode": "changed_services",
    "reason": "Workflow-requested update",
}
CONTEXT = {
    "idempotency_key": "deployment-update|moonmind|wf-1",
    "workflow_id": "mm:wf-1",
}


class _NoLegacyRunner:
    """Any legacy Compose use means the tool became a second updater."""

    def __getattr__(self, name: str):
        raise AssertionError(
            f"legacy updater used ({name}) while a controller is installed"
        )


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
    monkeypatch.setattr(
        deployment_execution, "CONTROLLER_OBSERVE_INTERVAL_SECONDS", 0.05
    )
    started: list[InProcessController] = []

    def start(applier=None) -> InProcessController:
        controller = InProcessController(
            state_dir,
            secret=SECRET,
            applier=applier,
            target_resolver=lambda stack: dict(DEFAULT_TARGET),
        )
        started.append(controller)
        install_controller_state(state_dir, port=controller.port, secret=SECRET)
        monkeypatch.setenv("MOONMIND_CONTROLLER_URL", controller.url)
        return controller

    yield start
    for controller in started:
        controller.close()
    forget_controller_modules()


def _handler():
    executor = deployment_execution.DeploymentUpdateExecutor(
        lock_manager=deployment_execution.DeploymentUpdateLockManager(),
        desired_state_store=deployment_execution.InMemoryDesiredStateStore(),
        evidence_writer=deployment_execution.InMemoryEvidenceWriter(),
        runner=_NoLegacyRunner(),
    )
    return deployment_execution.build_deployment_update_handler(executor)


def test_tool_submits_and_observes_the_controller_operation(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()

    result = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))

    assert result.status == "COMPLETED"
    operation_id = result.outputs["operationId"]
    assert operation_id.startswith("wf-")
    assert result.outputs["owner"] == "controller"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["requestedImage"] == IMAGE
    assert result.outputs["installedImage"] == IMAGE
    assert result.outputs["resolvedDigest"] == DIGEST
    assert controller.applied == [operation_id]
    # An activity retry reattaches to the same operation instead of
    # launching another updater.
    again = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))
    assert again.outputs["operationId"] == operation_id
    assert controller.applied == [operation_id]


def test_tool_waits_for_a_running_operation_after_a_lost_acknowledgment(
    controller_factory: Callable[..., InProcessController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    controller = controller_factory(slow_applier(release))
    monkeypatch.setattr(controller_client, "CONTROLLER_SUBMIT_TIMEOUT_SECONDS", 0.3)
    timer = threading.Timer(1.0, release.set)
    timer.start()
    try:
        result = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))
    finally:
        release.set()
        timer.cancel()
    assert result.status == "COMPLETED"
    assert controller.applied == [result.outputs["operationId"]]


def test_tool_reports_a_failed_controller_operation_with_its_original_error(
    controller_factory: Callable[..., InProcessController],
) -> None:
    def failing(controller: InProcessController, operation: dict) -> None:
        controller.store.record_attempt_error(
            operation["operationId"], error="up failed: init-db exited 1"
        )
        raise controller.engine.ApplyError("up", 1, "init-db exited 1")

    controller_factory(failing)

    result = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))

    assert result.status == "FAILED"
    assert result.outputs["status"] == "FAILED"
    assert result.outputs["failure"]["reason"].startswith(
        "attempt 1: up failed: init-db exited 1"
    )
    assert result.outputs["failure"]["retryable"] is False
    assert result.outputs["retryAllowed"] is True


def test_tool_reports_an_unavailable_controller_without_running_the_legacy_updater(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()
    controller.close()

    with pytest.raises(ToolFailure) as failure:
        asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))

    assert failure.value.error_code == "DEPLOYMENT_CONTROLLER_UNAVAILABLE"
    assert failure.value.retryable is True
    assert SECRET not in str(failure.value)


def test_tool_without_a_durable_identity_is_refused(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()

    with pytest.raises(ToolFailure) as failure:
        asyncio.run(_handler()(dict(INPUTS), {}))

    assert failure.value.error_code == "INVALID_INPUT"
    assert controller.applied == []
