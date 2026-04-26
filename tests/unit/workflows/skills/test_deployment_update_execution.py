from __future__ import annotations

import json
from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.deployment_execution import (
    ComposeVerification,
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    InMemoryDesiredStateStore,
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

    async def pull(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        self.events.append("runner:pull")
        self.commands.append(("pull", command))
        return {"stack": stack, "command": list(command), "exitCode": 0}

    async def up(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        self.events.append("runner:up")
        self.commands.append(("up", command))
        return {"stack": stack, "command": list(command), "exitCode": 0}

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
    async def up(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
        self.events.append("runner:up")
        self.commands.append(("up", command))
        return {"stack": stack, "command": list(command), "exitCode": 17}


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

    async def pull(self, *, stack: str, command: tuple[str, ...]) -> Mapping[str, Any]:
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
