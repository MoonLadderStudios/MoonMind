from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.deployment_execution import (
    ComposeVerification,
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    HostDockerComposeRunner,
    InMemoryDesiredStateStore,
    _ensure_command_succeeded,
    _is_host_absolute_path,
    _remap_host_compose_path,
    _tail_text,
    build_compose_command_plan,
    build_deployment_update_handler,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure


class RecordingEvidenceWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.records: list[tuple[str, Mapping[str, Any]]] = []

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        self.events.append(f"evidence:{kind}")
        self.records.append((kind, dict(payload)))
        return f"art:sha256:{kind.replace('-', ''):0<64}"[:75]


class RecordingDesiredStateStore(InMemoryDesiredStateStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def persist(self, payload: Mapping[str, Any]) -> str:
        self.events.append("desired:persist")
        return await super().persist(payload)


class RecordingRunner:
    def __init__(
        self,
        events: list[str],
        *,
        verification_succeeded: bool = True,
        verification_status: str | None = None,
        block_on_before: bool = False,
    ) -> None:
        self.events = events
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.verification_succeeded = verification_succeeded
        self.verification_status = verification_status
        self._before_event = None
        self._release_event = None
        if block_on_before:
            import asyncio

            self._before_event = asyncio.Event()
            self._release_event = asyncio.Event()

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        self.events.append(f"runner:capture:{phase}")
        if phase == "before" and self._before_event is not None:
            self._before_event.set()
            await self._release_event.wait()
        return {"stack": stack, "phase": phase, "services": ["api"]}

    async def pull(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.events.append("runner:pull")
        self.commands.append(("pull", command))
        return {
            "stack": stack,
            "command": list(command),
            "requestedImage": requested_image,
            "exitCode": 0,
        }

    async def up(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.events.append("runner:up")
        self.commands.append(("up", command))
        return {
            "stack": stack,
            "command": list(command),
            "requestedImage": requested_image,
            "exitCode": 0,
        }

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        self.events.append("runner:verify")
        return ComposeVerification(
            succeeded=self.verification_succeeded,
            updated_services=("api",) if self.verification_succeeded else (),
            running_services=(
                {"name": "api", "state": "running", "health": "healthy"},
            ),
            details={
                "stack": stack,
                "requestedImage": requested_image,
                "resolvedDigest": resolved_digest,
            },
            status=self.verification_status,
        )

    async def wait_until_before_capture_started(self) -> None:
        assert self._before_event is not None
        await self._before_event.wait()

    def release_before_capture(self) -> None:
        assert self._release_event is not None
        self._release_event.set()


def _inputs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "stack": "moonmind",
        "image": {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "20260425.1234",
            "resolvedDigest": "sha256:" + "a" * 64,
        },
        "mode": "changed_services",
        "removeOrphans": True,
        "wait": True,
        "reason": "Update to the latest tested build",
    }
    payload.update(overrides)
    return payload


def _executor(
    *,
    runner: RecordingRunner | None = None,
    events: list[str] | None = None,
    lock_manager: DeploymentUpdateLockManager | None = None,
) -> tuple[
    DeploymentUpdateExecutor,
    RecordingDesiredStateStore,
    RecordingEvidenceWriter,
    RecordingRunner,
    list[str],
]:
    events = events if events is not None else []
    store = RecordingDesiredStateStore(events)
    evidence = RecordingEvidenceWriter(events)
    runner = runner or RecordingRunner(events)
    return (
        DeploymentUpdateExecutor(
            lock_manager=lock_manager or DeploymentUpdateLockManager(),
            desired_state_store=store,
            evidence_writer=evidence,
            runner=runner,
        ),
        store,
        evidence,
        runner,
        events,
    )


class FailingUpRunner(RecordingRunner):
    async def up(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.events.append("runner:up")
        self.commands.append(("up", command))
        return {"stack": stack, "command": list(command), "exitCode": 17}


class FailingPullRunner(RecordingRunner):
    async def pull(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.events.append("runner:pull")
        self.commands.append(("pull", command))
        return {"stack": stack, "command": list(command), "exitCode": 23}


class SecretRecordingRunner(RecordingRunner):
    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        self.events.append(f"runner:capture:{phase}")
        return {
            "stack": stack,
            "phase": phase,
            "services": ["api"],
            "diagnostics": "token=super-secret,log_level=info",
            "environment": {
                "API_TOKEN": "token=super-secret",
                "REGISTRY_PASSWORD": "registry-password",
                "NORMAL_VALUE": "visible",
            },
        }

    async def pull(
        self, *, stack: str, command: tuple[str, ...], requested_image: str
    ) -> Mapping[str, Any]:
        self.events.append("runner:pull")
        self.commands.append(("pull", command))
        return {
            "stack": stack,
            "command": list(command),
            "exitCode": 0,
            "authHeader": "Bearer secret-token",
            "stdout": "passwd=hunter2;mode=ok",
        }


class InvalidStatusRunner(RecordingRunner):
    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        self.events.append("runner:verify")
        return ComposeVerification(
            succeeded=False,
            updated_services=(),
            running_services=(),
            details={"failedChecks": ["image-id"]},
            status="UNKNOWN",
        )


class SecretVerificationFailureRunner(RecordingRunner):
    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        self.events.append("runner:verify")
        return ComposeVerification(
            succeeded=False,
            updated_services=(),
            running_services=(),
            details={
                "message": "health check failed with token=super-secret,log_level=info",
                "requestedImage": requested_image,
                "resolvedDigest": resolved_digest,
            },
        )


class VerificationEvidenceFailureWriter(RecordingEvidenceWriter):
    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        if kind == "verification":
            self.events.append("evidence:verification:failed")
            raise RuntimeError("verification evidence write failed")
        return await super().write(kind, payload)


@pytest.mark.asyncio
async def test_same_stack_lock_contention_fails_before_side_effects() -> None:
    import asyncio

    events: list[str] = []
    lock_manager = DeploymentUpdateLockManager()
    blocking_runner = RecordingRunner(events, block_on_before=True)
    first_executor, _store, _evidence, _runner, _events = _executor(
        runner=blocking_runner, events=events, lock_manager=lock_manager
    )
    second_executor, _store2, _evidence2, _runner2, _events2 = _executor(
        events=[], lock_manager=lock_manager
    )
    first_task = asyncio.create_task(first_executor.execute(_inputs()))
    await blocking_runner.wait_until_before_capture_started()

    with pytest.raises(ToolFailure) as exc_info:
        await second_executor.execute(_inputs())

    assert exc_info.value.error_code == "DEPLOYMENT_LOCKED"
    assert exc_info.value.retryable is False
    assert exc_info.value.details["failureClass"] == "deployment_lock_unavailable"
    assert second_executor.desired_state_store.records == []

    blocking_runner.release_before_capture()
    first_result = await first_task
    assert first_result.status == "COMPLETED"


@pytest.mark.asyncio
async def test_lifecycle_order_persists_desired_state_before_compose_up() -> None:
    executor, store, _evidence, _runner, events = _executor()

    result = await executor.execute(_inputs(), {"source_run_id": "run-123"})

    assert result.status == "COMPLETED"
    assert events.index("runner:capture:before") < events.index("desired:persist")
    assert events.index("desired:persist") < events.index("runner:up")
    assert store.records[0]["stack"] == "moonmind"
    assert store.records[0]["imageRepository"] == "ghcr.io/moonladderstudios/moonmind"
    assert store.records[0]["requestedReference"] == "20260425.1234"
    assert store.records[0]["resolvedDigest"] == "sha256:" + "a" * 64
    assert store.records[0]["reason"] == "Update to the latest tested build"
    assert store.records[0]["sourceRunId"] == "run-123"


def test_changed_services_command_omits_force_recreate() -> None:
    plan = build_compose_command_plan(
        mode="changed_services",
        remove_orphans=True,
        wait=True,
        runner_mode="privileged_worker",
    )

    assert plan.pull_args == (
        "docker",
        "compose",
        "pull",
        "--policy",
        "always",
        "--ignore-buildable",
    )
    assert plan.up_args == (
        "docker",
        "compose",
        "up",
        "-d",
        "--remove-orphans",
        "--wait",
    )
    assert "--force-recreate" not in plan.up_args


def test_force_recreate_and_policy_flags_are_closed() -> None:
    plan = build_compose_command_plan(
        mode="force_recreate",
        remove_orphans=False,
        wait=False,
        runner_mode="ephemeral_updater_container",
    )

    assert plan.runner_mode == "ephemeral_updater_container"
    assert plan.up_args == (
        "docker",
        "compose",
        "up",
        "-d",
        "--force-recreate",
    )
    assert "--remove-orphans" not in plan.up_args
    assert "--wait" not in plan.up_args


@pytest.mark.asyncio
async def test_verification_failure_returns_failed_tool_result_with_evidence_refs(
) -> None:
    events: list[str] = []
    runner = RecordingRunner(events, verification_succeeded=False)
    executor, _store, evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    result = await executor.execute(_inputs())

    assert result.status == "FAILED"
    assert result.outputs["status"] == "FAILED"
    assert result.outputs["verificationArtifactRef"].startswith("art:sha256:")
    assert result.outputs["afterStateArtifactRef"].startswith("art:sha256:")
    assert [kind for kind, _payload in evidence.records] == [
        "before-state",
        "command-log",
        "verification",
        "after-state",
    ]


@pytest.mark.asyncio
async def test_verification_failure_outputs_failure_class_and_actionable_reason(
) -> None:
    events: list[str] = []
    runner = SecretVerificationFailureRunner(events)
    executor, _store, _evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    result = await executor.execute(_inputs())

    assert result.outputs["failure"] == {
        "class": "verification_failure",
        "reason": "health check failed with [REDACTED],log_level=info",
        "retryable": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_class"),
    [
        (_inputs(stack="other-stack"), "invalid_input"),
        (_inputs(updaterRunnerImage="docker:29-cli"), "invalid_input"),
    ],
)
async def test_invalid_input_failures_include_normalized_failure_class(
    payload: dict[str, object], expected_class: str
) -> None:
    executor, _store, _evidence, _runner, _events = _executor()

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(payload)

    assert exc_info.value.details["failureClass"] == expected_class


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner", "expected_phase", "expected_class"),
    [
        (FailingPullRunner, "pull", "image_pull_failure"),
        (FailingUpRunner, "up", "service_recreation_failure"),
    ],
)
async def test_command_failures_include_normalized_failure_class(
    runner: type[RecordingRunner], expected_phase: str, expected_class: str
) -> None:
    events: list[str] = []
    executor, _store, _evidence, _runner, _events = _executor(
        runner=runner(events), events=events
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs())

    assert exc_info.value.details["phase"] == expected_phase
    assert exc_info.value.details["failureClass"] == expected_class


@pytest.mark.asyncio
async def test_failed_execution_does_not_emit_rollback_without_explicit_request(
) -> None:
    events: list[str] = []
    runner = RecordingRunner(events, verification_succeeded=False)
    executor, _store, _evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    result = await executor.execute(_inputs())

    serialized = json.dumps(result.outputs)
    assert "rollback" not in serialized.lower()
    assert result.outputs["status"] == "FAILED"


@pytest.mark.asyncio
async def test_forbidden_runner_image_and_path_inputs_are_rejected() -> None:
    executor, _store, _evidence, _runner, _events = _executor()

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(updaterRunnerImage="docker:29-cli"))

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.details["fields"] == ["updaterRunnerImage"]


@pytest.mark.asyncio
async def test_non_allowlisted_stack_is_rejected_at_execution_boundary() -> None:
    executor, _store, _evidence, _runner, _events = _executor()

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(stack="other-stack"))

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.details["stack"] == "other-stack"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["removeOrphans", "wait"])
async def test_boolean_options_must_be_real_booleans(field: str) -> None:
    executor, _store, _evidence, _runner, _events = _executor()

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(**{field: "false"}))

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.details["field"] == field
    assert exc_info.value.details["value_type"] == "str"


@pytest.mark.asyncio
async def test_failed_command_result_stops_execution_and_persists_diagnostics() -> None:
    events: list[str] = []
    runner = FailingUpRunner(events)
    executor, _store, evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs())

    assert exc_info.value.error_code == "DEPLOYMENT_COMMAND_FAILED"
    assert exc_info.value.details["phase"] == "up"
    assert "runner:verify" not in events
    assert [kind for kind, _payload in evidence.records] == [
        "before-state",
        "command-log",
        "after-state",
    ]
    command_payload = dict(evidence.records[1][1])
    assert command_payload["pull"]["result"]["exitCode"] == 0
    assert command_payload["up"]["result"]["exitCode"] == 17
    assert command_payload["error"]["error_code"] == "DEPLOYMENT_COMMAND_FAILED"


def test_unsupported_runner_mode_fails_closed() -> None:
    with pytest.raises(ToolFailure) as exc_info:
        build_compose_command_plan(
            mode="changed_services",
            remove_orphans=True,
            wait=True,
            runner_mode="caller_selected",
        )

    assert exc_info.value.error_code == "POLICY_VIOLATION"
    assert exc_info.value.details["failureClass"] == "policy_violation"


def test_compose_config_validation_failure_has_normalized_class() -> None:
    with pytest.raises(ToolFailure) as exc_info:
        _ensure_command_succeeded("config", {"status": "invalid"})

    assert exc_info.value.error_code == "DEPLOYMENT_COMMAND_FAILED"
    assert exc_info.value.details["failureClass"] == "compose_config_validation_failure"


def test_tail_text_returns_empty_for_non_positive_limit() -> None:
    payload = b"abcdef"

    assert _tail_text(payload, max_chars=0) == ""
    assert _tail_text(payload, max_chars=-3) == ""


@pytest.mark.asyncio
async def test_context_executor_override_supports_registered_handler() -> None:
    executor, _store, _evidence, _runner, _events = _executor()
    handler = build_deployment_update_handler()

    result = await handler(
        _inputs(),
        {
            "deployment_update_executor": executor,
            "deployment_runner_mode": "privileged_worker",
        },
    )

    assert result.status == "COMPLETED"
    assert result.outputs["requestedImage"] == (
        "ghcr.io/moonladderstudios/moonmind:20260425.1234"
    )

@pytest.mark.asyncio
async def test_partial_verification_returns_partial_status_with_artifact_refs() -> None:
    events: list[str] = []
    runner = RecordingRunner(
        events, verification_succeeded=False, verification_status="PARTIALLY_VERIFIED"
    )
    executor, _store, evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    result = await executor.execute(_inputs())

    assert result.status == "FAILED"
    assert result.outputs["status"] == "PARTIALLY_VERIFIED"
    assert result.outputs["beforeStateArtifactRef"].startswith("art:sha256:")
    assert result.outputs["commandLogArtifactRef"].startswith("art:sha256:")
    assert result.outputs["verificationArtifactRef"].startswith("art:sha256:")
    assert result.outputs["afterStateArtifactRef"].startswith("art:sha256:")
    assert [kind for kind, _payload in evidence.records] == [
        "before-state",
        "command-log",
        "verification",
        "after-state",
    ]


@pytest.mark.asyncio
async def test_unsupported_verification_status_fails_closed() -> None:
    events: list[str] = []
    executor, _store, _evidence, _runner, _events = _executor(
        runner=InvalidStatusRunner(events), events=events
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs())

    assert exc_info.value.error_code == "DEPLOYMENT_VERIFICATION_INVALID"
    assert "runner:capture:after" in events


@pytest.mark.asyncio
async def test_audit_metadata_is_attached_to_verification_evidence_and_outputs(
) -> None:
    executor, _store, evidence, _runner, _events = _executor()

    result = await executor.execute(
        _inputs(),
        {
            "source_run_id": "run-123",
            "workflow_id": "workflow-456",
            "task_id": "task-789",
            "operator": "operator@example.com",
            "operator_role": "admin",
        },
    )

    audit = result.outputs["audit"]
    assert audit["runId"] == "run-123"
    assert audit["workflowId"] == "workflow-456"
    assert audit["taskId"] == "task-789"
    assert audit["operator"] == "operator@example.com"
    assert audit["operatorRole"] == "admin"
    assert audit["stack"] == "moonmind"
    assert audit["requestedImage"] == "ghcr.io/moonladderstudios/moonmind:20260425.1234"
    assert audit["resolvedDigest"] == "sha256:" + "a" * 64
    assert audit["mode"] == "changed_services"
    assert audit["options"]["removeOrphans"] is True
    assert audit["finalStatus"] == "SUCCEEDED"
    assert audit["startedAt"] <= audit["completedAt"]

    verification_payload = next(
        payload for kind, payload in evidence.records if kind == "verification"
    )
    assert verification_payload["audit"]["workflowId"] == "workflow-456"
    assert verification_payload["audit"]["finalStatus"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_evidence_payloads_are_recursively_redacted_before_publication() -> None:
    events: list[str] = []
    executor, _store, evidence, _runner, _events = _executor(
        runner=SecretRecordingRunner(events), events=events
    )

    await executor.execute(_inputs())

    serialized = json.dumps([payload for _kind, payload in evidence.records])
    assert "super-secret" not in serialized
    assert "registry-password" not in serialized
    assert "secret-token" not in serialized
    assert "[REDACTED]" in serialized
    before_payload = next(
        payload for kind, payload in evidence.records if kind == "before-state"
    )
    assert before_payload["environment"]["NORMAL_VALUE"] == "visible"
    assert before_payload["diagnostics"] == "[REDACTED],log_level=info"
    command_payload = next(
        payload for kind, payload in evidence.records if kind == "command-log"
    )
    assert command_payload["pull"]["result"]["stdout"] == "[REDACTED];mode=ok"


@pytest.mark.asyncio
async def test_output_audit_failure_reason_is_redacted() -> None:
    events: list[str] = []
    executor, _store, _evidence, _runner, _events = _executor(
        runner=SecretVerificationFailureRunner(events), events=events
    )

    result = await executor.execute(_inputs())

    audit = result.outputs["audit"]
    assert audit["failureReason"] == (
        "health check failed with [REDACTED],log_level=info"
    )
    assert "super-secret" not in json.dumps(result.outputs)


@pytest.mark.asyncio
async def test_post_verification_exception_marks_audit_failed() -> None:
    events: list[str] = []
    runner = RecordingRunner(events)
    store = RecordingDesiredStateStore(events)
    evidence = VerificationEvidenceFailureWriter(events)
    executor = DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=store,
        evidence_writer=evidence,
        runner=runner,
    )

    with pytest.raises(RuntimeError, match="verification evidence write failed"):
        await executor.execute(_inputs())

    after_payload = next(
        payload for kind, payload in evidence.records if kind == "after-state"
    )
    assert after_payload["audit"]["finalStatus"] == "FAILED"
    assert after_payload["audit"]["failureReason"] == (
        "verification evidence write failed"
    )


@pytest.mark.asyncio
async def test_progress_contains_lifecycle_states_without_command_output() -> None:
    executor, _store, _evidence, _runner, _events = _executor()

    result = await executor.execute(_inputs())

    progress = result.progress
    assert progress["state"] == "SUCCEEDED"
    assert progress["message"] == "Deployment update succeeded."
    states = [event["state"] for event in progress["events"]]
    assert states == [
        "QUEUED",
        "VALIDATING",
        "LOCK_WAITING",
        "CAPTURING_BEFORE_STATE",
        "PERSISTING_DESIRED_STATE",
        "PULLING_IMAGES",
        "RECREATING_SERVICES",
        "VERIFYING",
        "CAPTURING_AFTER_STATE",
        "SUCCEEDED",
    ]
    serialized = json.dumps(progress)
    assert "exitCode" not in serialized
    assert "docker" not in serialized


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/host/repo", True),
        ("relative/path", False),
        ("", False),
        ("C:\\repo", True),
        ("c:/repo", True),
        ("\\\\server\\share", True),
    ],
)
def test_is_host_absolute_path_handles_windows_paths(value: str, expected: bool):
    from pathlib import PurePosixPath

    assert _is_host_absolute_path(value) is expected
    # Path-like values should also work
    if value:
        assert _is_host_absolute_path(PurePosixPath(value)) is _is_host_absolute_path(value)


def test_remap_host_compose_path_preserves_subpath_under_host_dir(tmp_path):
    from pathlib import Path

    host_dir = Path("/host/repo")
    local_dir = tmp_path / "host_project"
    local_dir.mkdir()
    nested = local_dir / "deploy"
    nested.mkdir()
    compose = nested / "docker-compose.prod.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    remapped = _remap_host_compose_path(
        Path("/host/repo/deploy/docker-compose.prod.yaml"),
        host_dir,
        local_dir,
    )
    assert remapped == compose


def test_remap_host_compose_path_falls_back_to_basename_when_unrelated(tmp_path):
    from pathlib import Path

    host_dir = Path("/host/repo")
    local_dir = tmp_path / "host_project"
    local_dir.mkdir()

    remapped = _remap_host_compose_path(
        Path("/elsewhere/docker-compose.yaml"),
        host_dir,
        local_dir,
    )
    assert remapped == local_dir / "docker-compose.yaml"


def test_compose_base_command_remaps_subpath_for_host_compose_file(tmp_path):
    """Absolute host compose paths must resolve into the local mount with subpath intact."""

    local_dir = tmp_path / "host_project"
    nested = local_dir / "deploy"
    nested.mkdir(parents=True)
    compose = nested / "docker-compose.prod.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    runner = HostDockerComposeRunner(
        project_dir="/host/repo",
        compose_file="/host/repo/deploy/docker-compose.prod.yaml",
        project_name="moonmind",
        local_project_dir=str(local_dir),
    )
    command = runner._compose_base_command()
    assert command[-2] == "-f"
    assert command[-1] == str(compose)
    assert command[5] == "/host/repo"


def test_compose_base_command_accepts_windows_host_paths(tmp_path):
    """Worker on Linux must treat Windows drive-letter host paths as absolute."""

    local_dir = tmp_path / "host_project"
    local_dir.mkdir()
    compose = local_dir / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    runner = HostDockerComposeRunner(
        project_dir="C:\\Users\\dev\\MoonMind",
        compose_file="C:\\Users\\dev\\MoonMind\\docker-compose.yaml",
        project_name="moonmind",
        local_project_dir=str(local_dir),
    )
    command = runner._compose_base_command()
    assert command[5] == "C:\\Users\\dev\\MoonMind"
    assert command[-1] == str(compose)


@pytest.mark.asyncio
async def test_host_compose_runner_returns_bounded_command_output_tails(
    tmp_path, monkeypatch
):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"0123456789abcdefTAIL", b"compose stderr tail"

    async def fake_create_subprocess_exec(
        *args: str,
        cwd: str,
        env: Mapping[str, str],
        stdout: Any,
        stderr: Any,
    ):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return FakeProcess()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    runner = HostDockerComposeRunner(project_dir=str(tmp_path))

    result = await runner._run_compose_command(
        ("docker", "compose", "ps"), max_output_chars=8
    )

    assert result["exitCode"] == 0
    assert result["stdout"] == "cdefTAIL"
    assert result["stderr"] == "err tail"
    assert result["command"][-1] == "ps"
    assert captured["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_host_compose_runner_parses_full_json_stdout_with_stderr_warning(
    tmp_path, monkeypatch
):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    large_service_record = {
        "Service": "api",
        "State": "running",
        "Labels": "x" * 22000,
    }
    images_record = {
        "Service": "api",
        "Repository": "ghcr.io/moonladderstudios/moonmind",
        "Tag": "latest",
    }

    class FakeProcess:
        returncode = 0

        def __init__(self, stdout: bytes, stderr: bytes) -> None:
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_create_subprocess_exec(
        *args: str,
        cwd: str,
        env: Mapping[str, str],
        stdout: Any,
        stderr: Any,
    ):
        calls.append(args)
        payload = (
            json.dumps(images_record)
            if args[-3:] == ("images", "--format", "json")
            else json.dumps(large_service_record)
        )
        return FakeProcess(
            f"{payload}\n".encode("utf-8"),
            b'time="2026-05-13T16:37:34Z" level=warning msg="diagnostic"\n',
        )

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    runner = HostDockerComposeRunner(project_dir=str(tmp_path))

    state = await runner.capture_state(stack="moonmind", phase="before")

    assert state["services"] == [large_service_record]
    assert state["images"] == [images_record]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_host_compose_runner_invalid_json_stdout_is_tool_failure(
    tmp_path, monkeypatch
):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (
                b"not json\n",
                b'time="2026-05-13T16:37:34Z" level=warning msg="diagnostic"\n',
            )

    async def fake_create_subprocess_exec(
        *args: str,
        cwd: str,
        env: Mapping[str, str],
        stdout: Any,
        stderr: Any,
    ):
        return FakeProcess()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    runner = HostDockerComposeRunner(project_dir=str(tmp_path))

    with pytest.raises(ToolFailure) as exc_info:
        await runner._run_compose_json(("ps", "--format", "json"))

    failure = exc_info.value
    assert failure.error_code == "DEPLOYMENT_COMMAND_FAILED"
    assert failure.message == "Deployment compose command returned invalid JSON."
    assert failure.details["failureClass"] == "compose_config_validation_failure"
    assert failure.details["stderr"]
