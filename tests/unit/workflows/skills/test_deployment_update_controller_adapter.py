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

    def start(applier=None, target=None) -> InProcessController:
        controller = InProcessController(
            state_dir,
            secret=SECRET,
            applier=applier,
            target_resolver=lambda stack: dict(target or DEFAULT_TARGET),
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


@pytest.mark.parametrize("configured_env", [False, True])
def test_workflow_controller_update_converges_the_selected_omnigent_release(
    controller_factory, tmp_path, monkeypatch, configured_env
):
    import json
    from types import SimpleNamespace

    from moonmind.omnigent import settings as omnigent_settings
    from moonmind.workflows.skills import deployment_release, omnigent_release

    repo = tmp_path / "deployment-checkout"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    if configured_env:
        (repo / ".env").write_text("OMNIGENT_IMAGE_TAG=latest\n")
    state = repo / "deploy" / "state"
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", str(repo))
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", str(state / ".env.deploy")
    )
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(state / "desired-state.json")
    )
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCK_DIR", str(state / "locks"))
    monkeypatch.setattr(
        omnigent_settings, "build_omnigent_gate", lambda: SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(omnigent_settings, "generic_host_enabled", lambda: True)
    monkeypatch.setattr(deployment_execution, "CONTROLLER_OBSERVE_TIMEOUT_SECONDS", 5)
    target = {
        "project": "moonmind-test-controller-omnigent",
        "projectDir": str(repo),
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api", "omnigent"],
    }
    candidate = {
        "server": "example/server@sha256:" + "a" * 64,
        "shared": "example/host@sha256:" + "b" * 64,
    }
    live = {"server": "example/server@sha256:" + "c" * 64}
    resolved = []
    phases = []

    async def inputs():
        return await omnigent_release._default_deployment_inputs(repo / ".env")

    async def resolve(inputs):
        assert inputs["OMNIGENT_IMAGE_TAG"] == "latest"
        resolved.append(dict(inputs))
        return dict(candidate)

    async def read_live():
        return dict(live)

    async def restart(selected):
        phases.append("migrate")
        live.update(selected)

    async def await_resolution(selected):
        return dict(selected)

    async def catalog():
        return {"catalogRef": "catalog:selected"}

    async def policies(selected):
        return {"cut": [], "skipped": []}

    async def schedules():
        return {"refreshed": 0, "failures": []}

    async def running_server(expected):
        return live["server"]

    def drivers(**kwargs):
        assert kwargs["runner"].project_name == target["project"]
        assert kwargs["moonmind_image"] == IMAGE
        return omnigent_release.OmnigentReleaseDrivers(
            deployment_inputs=inputs,
            resolve_candidates=resolve,
            read_live_refs=read_live,
            restart_server=restart,
            await_resolution=await_resolution,
            sync_catalog=catalog,
            cut_policy_versions=policies,
            refresh_schedules=schedules,
            verify_live_container=running_server,
        )

    monkeypatch.setattr(omnigent_release, "production_drivers", drivers)

    class ComposeBoundary:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if "--omnigent-select" in args or "--omnigent-migrate" in args:
                phase = args[-2].removeprefix("--omnigent-")
                if phase == "select":
                    phases.append("select")
                result = asyncio.run(
                    deployment_release.run_controller_omnigent_step(
                        phase, json.loads(args[-1])
                    )
                )
                return {
                    "exit": 0,
                    "output": "MOONMIND_OMNIGENT_RESULT=" + json.dumps(result),
                }
            if "up" in args:
                phases.append("up")
                desired_env = controller.server.read_env_file(
                    str(state / ".env.deploy")
                )
                assert desired_env["OMNIGENT_IMAGE_REF"] == candidate["server"]
            if "ps" in args:
                return {
                    "exit": 0,
                    "output": json.dumps(
                        [
                            {"Service": name, "State": "running"}
                            for name in target["services"]
                        ]
                    ),
                }
            return {"exit": 0, "output": "ok"}

    def apply(controller, operation):
        return controller.server.production_apply(controller.store, operation)

    controller = controller_factory(apply, target=target)
    # The fixture retains the real endpoint; only daemon/registry/database
    # side effects are replaced. Both release algorithms and state stores run.
    monkeypatch.setattr(
        controller.engine, "subprocess_runner", lambda: ComposeBoundary()
    )
    result = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))
    assert result.status == "COMPLETED", result
    operation = controller.store.load(result.outputs["operationId"])
    assert operation["omnigentSelection"]["revision"] == 1
    assert phases == ["select", "up", "migrate"]
    assert len(resolved) == 1
    assert live == candidate
    release_record = json.loads((state / "desired-state.json").read_text())[
        "omnigentRelease"
    ]
    assert release_record["revision"] == 1
    assert release_record["serverImageRef"] == candidate["server"]
    assert all(check["status"] == "passed" for check in operation["verification"])
    # Retrying the workflow observes the same confirmed operation, without a
    # new full updater or another Omnigent selection/migration.
    again = asyncio.run(_handler()(dict(INPUTS), dict(CONTEXT)))
    assert again.outputs["operationId"] == result.outputs["operationId"]
    assert phases == ["select", "up", "migrate"]
