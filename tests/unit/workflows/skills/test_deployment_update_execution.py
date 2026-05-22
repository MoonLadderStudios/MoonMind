from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.deployment_execution import (
    ComposeCommandPlan,
    ComposeVerification,
    DeploymentUpdateExecutor,
    DeploymentUpdateLockManager,
    FileDeploymentUpdateLockManager,
    FileDesiredStateStore,
    HostDockerComposeRunner,
    InMemoryDesiredStateStore,
    TemporalDeploymentEvidenceWriter,
    _ensure_command_succeeded,
    _ensure_runner_survives_update,
    _is_host_absolute_path,
    _remap_host_compose_path,
    _service_name_matches,
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

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        self.events.append("runner:inspect-image")
        return {
            "Id": "sha256:" + "b" * 64,
            "RepoTags": [requested_image],
            "RepoDigests": [],
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


class SelfHostedComposeRunner(RecordingRunner):
    def __init__(
        self,
        events: list[str],
        *,
        worker_image_id: str = "sha256:" + "a" * 64,
        target_image_id: str = "sha256:" + "b" * 64,
    ) -> None:
        super().__init__(events)
        self.worker_image_id = worker_image_id
        self.target_image_id = target_image_id

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        self.events.append(f"runner:capture:{phase}")
        return {
            "stack": stack,
            "phase": phase,
            "services": [
                {
                    "ID": "abc123def456",
                    "Service": "temporal-worker-agent-runtime",
                    "State": "running",
                }
            ],
            "images": [
                {
                    "ContainerName": "moonmind-temporal-worker-agent-runtime-1",
                    "ID": self.worker_image_id,
                    "Repository": "ghcr.io/moonladderstudios/moonmind",
                    "Tag": "latest",
                }
            ],
        }

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        self.events.append("runner:inspect-image")
        return {"Id": self.target_image_id, "RepoTags": [requested_image]}


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
async def test_file_stack_lock_rejects_second_process_boundary_acquire(tmp_path) -> None:
    manager = FileDeploymentUpdateLockManager(lock_dir=str(tmp_path / "locks"))
    lease = await manager.acquire("moonmind")

    try:
        with pytest.raises(ToolFailure) as exc_info:
            await manager.acquire("moonmind")
    finally:
        await lease.release()

    assert exc_info.value.error_code == "DEPLOYMENT_LOCKED"
    assert exc_info.value.details["failureClass"] == "deployment_lock_unavailable"
    assert not (tmp_path / "locks" / "moonmind.lock").exists()


@pytest.mark.asyncio
async def test_file_stack_lock_rejects_path_traversal_stack_name(tmp_path) -> None:
    manager = FileDeploymentUpdateLockManager(lock_dir=str(tmp_path / "locks"))

    with pytest.raises(ToolFailure) as exc_info:
        await manager.acquire("../moonmind")

    assert exc_info.value.error_code == "INVALID_STACK_NAME"
    assert not (tmp_path / "locks").exists()


@pytest.mark.asyncio
async def test_file_stack_lock_recovers_stale_lock_before_acquire(tmp_path) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock_path = lock_dir / "moonmind.lock"
    lock_path.write_text(
        json.dumps(
            {
                "stack": "moonmind",
                "pid": 999999999,
                "createdAt": (
                    datetime.now(UTC) - timedelta(hours=7)
                ).isoformat().replace("+00:00", "Z"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manager = FileDeploymentUpdateLockManager(lock_dir=str(lock_dir))

    lease = await manager.acquire("moonmind")
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["stack"] == "moonmind"
    finally:
        await lease.release()


@pytest.mark.asyncio
async def test_lifecycle_order_persists_desired_state_before_compose_up() -> None:
    executor, store, _evidence, _runner, events = _executor()

    result = await executor.execute(_inputs(), {"source_run_id": "run-123"})

    assert result.status == "COMPLETED"
    assert events.index("runner:capture:before") < events.index("desired:persist")
    assert events.index("desired:persist") < events.index("runner:pull")
    assert events.index("desired:persist") < events.index("runner:up")
    assert store.records[0]["stack"] == "moonmind"
    assert store.records[0]["imageRepository"] == "ghcr.io/moonladderstudios/moonmind"
    assert store.records[0]["requestedReference"] == "20260425.1234"
    assert store.records[0]["resolvedDigest"] == "sha256:" + "a" * 64
    assert store.records[0]["reason"] == "Update to the latest tested build"
    assert store.records[0]["sourceRunId"] == "run-123"


@pytest.mark.asyncio
async def test_file_desired_state_store_writes_compose_env_and_audit_json(
    tmp_path,
) -> None:
    env_file = tmp_path / "state" / ".env.deploy"
    json_file = tmp_path / "state" / "desired-state.json"
    store = FileDesiredStateStore(
        env_file_path=str(env_file),
        json_file_path=str(json_file),
    )

    ref = await store.persist(
        {
            "stack": "moonmind",
            "imageRepository": "ghcr.io/moonladderstudios/moonmind",
            "requestedReference": "stable",
            "resolvedDigest": "sha256:" + "b" * 64,
            "reason": "Operator requested stable",
            "operator": "admin@example.com",
            "createdAt": "2026-04-26T00:00:00Z",
            "sourceRunId": "depupd_123",
        }
    )

    assert ref == f"file:{env_file}"
    env_text = env_file.read_text(encoding="utf-8")
    assert (
        'MOONMIND_IMAGE="ghcr.io/moonladderstudios/moonmind@sha256:'
        + "b" * 64
        + '"'
    ) in env_text
    assert (
        'MOONMIND_IMAGE_REQUESTED="ghcr.io/moonladderstudios/moonmind:stable"'
        in env_text
    )
    assert 'MOONMIND_DEPLOYMENT_RUN_ID="depupd_123"' in env_text
    audit = json.loads(json_file.read_text(encoding="utf-8"))
    assert audit["stack"] == "moonmind"
    assert audit["operator"] == "admin@example.com"
    assert audit["reason"] == "Operator requested stable"


@pytest.mark.asyncio
async def test_file_desired_state_store_quotes_compose_env_values(tmp_path) -> None:
    env_file = tmp_path / "state" / ".env.deploy"
    json_file = tmp_path / "state" / "desired-state.json"
    store = FileDesiredStateStore(
        env_file_path=str(env_file),
        json_file_path=str(json_file),
    )

    await store.persist(
        {
            "stack": "moonmind",
            "imageRepository": "ghcr.io/moonladderstudios/moonmind",
            "requestedReference": 'stable"quoted',
            "sourceRunId": "depupd_$123#tag",
        }
    )

    env_text = env_file.read_text(encoding="utf-8")
    assert (
        'MOONMIND_IMAGE="ghcr.io/moonladderstudios/moonmind:stable\\"quoted"'
        in env_text
    )
    assert 'MOONMIND_DEPLOYMENT_RUN_ID="depupd_$123#tag"' in env_text


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
    executor, store, _evidence, _runner, _events = _executor(
        runner=runner(events), events=events
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs())

    assert exc_info.value.details["phase"] == expected_phase
    assert exc_info.value.details["failureClass"] == expected_class
    if expected_phase == "pull":
        assert len(store.records) == 1
        assert events.index("desired:persist") < events.index("runner:pull")


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


@pytest.mark.asyncio
async def test_privileged_worker_fails_before_recreating_its_own_container(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    events: list[str] = []
    runner = SelfHostedComposeRunner(events)
    executor, store, evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs())

    assert exc_info.value.error_code == "DEPLOYMENT_RUNNER_UNSAFE"
    assert exc_info.value.details["failureClass"] == "runner_self_recreation_unsafe"
    assert exc_info.value.details["service"] == "temporal-worker-agent-runtime"
    assert len(store.records) == 1
    assert events.index("desired:persist") < events.index("runner:pull")
    assert "runner:pull" in events
    assert "runner:inspect-image" in events
    assert "runner:up" not in events
    assert [kind for kind, _payload in evidence.records] == [
        "before-state",
        "command-log",
        "after-state",
    ]


@pytest.mark.asyncio
async def test_privileged_worker_allows_changed_services_when_worker_image_is_current(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    current_image_id = "sha256:" + "c" * 64
    events: list[str] = []
    runner = SelfHostedComposeRunner(
        events,
        worker_image_id=current_image_id,
        target_image_id=current_image_id,
    )
    executor, store, _evidence, _runner, _events = _executor(
        runner=runner, events=events
    )

    result = await executor.execute(_inputs())

    assert result.status == "COMPLETED"
    assert len(store.records) == 1
    assert events.index("desired:persist") < events.index("runner:pull")
    assert events.index("runner:inspect-image") < events.index("runner:up")
    assert "runner:up" in events


def test_runner_guard_skips_when_targeted_services_exclude_worker(monkeypatch) -> None:
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    plan = ComposeCommandPlan(
        runner_mode="privileged_worker",
        pull_args=("docker", "compose", "pull"),
        up_args=("docker", "compose", "up", "-d", "api"),
    )
    before_state = {
        "services": [
            {"ID": "abc123def456", "Service": "temporal-worker-agent-runtime"}
        ]
    }

    # Worker is not in the targeted service list, so the guard must allow.
    _ensure_runner_survives_update(command_plan=plan, before_state=before_state)


def test_runner_guard_blocks_when_targeted_services_include_worker(monkeypatch) -> None:
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    plan = ComposeCommandPlan(
        runner_mode="privileged_worker",
        pull_args=("docker", "compose", "pull"),
        up_args=("docker", "compose", "up", "-d", "api", "temporal-worker-agent-runtime"),
    )
    before_state = {
        "services": [
            {"ID": "abc123def456", "Service": "temporal-worker-agent-runtime"}
        ]
    }

    with pytest.raises(ToolFailure) as exc_info:
        _ensure_runner_survives_update(command_plan=plan, before_state=before_state)

    assert exc_info.value.error_code == "DEPLOYMENT_RUNNER_UNSAFE"
    assert exc_info.value.details["service"] == "temporal-worker-agent-runtime"


def test_runner_guard_blocks_when_no_specific_services_targeted(monkeypatch) -> None:
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    plan = ComposeCommandPlan(
        runner_mode="privileged_worker",
        pull_args=("docker", "compose", "pull"),
        up_args=("docker", "compose", "up", "-d", "--wait"),
    )
    before_state = {
        "services": [
            {"ID": "abc123def456", "Service": "temporal-worker-agent-runtime"}
        ]
    }

    with pytest.raises(ToolFailure) as exc_info:
        _ensure_runner_survives_update(command_plan=plan, before_state=before_state)

    assert exc_info.value.error_code == "DEPLOYMENT_RUNNER_UNSAFE"


@pytest.mark.parametrize(
    "candidate",
    [
        "moonmind-api-1",
        "moonmind_api_1",
        "api-1",
        "api_1",
        "api",
    ],
)
def test_service_name_matches_compose_service_tokens(candidate: str) -> None:
    assert _service_name_matches(candidate, "api") is True


@pytest.mark.parametrize(
    "candidate",
    [
        "moonmind-api-gateway-1",
        "moonmind_api_gateway_1",
        "api-gateway-1",
        "worker-api-sidecar-1",
    ],
)
def test_service_name_does_not_match_service_prefixes(candidate: str) -> None:
    assert _service_name_matches(candidate, "api") is False


def test_host_alias_replaces_broken_symlink(tmp_path) -> None:
    local_dir = tmp_path / "workspace" / "host_project"
    local_dir.mkdir(parents=True)
    host_dir = tmp_path / "host" / "MoonMind"
    host_dir.parent.mkdir(parents=True)
    # Install a broken symlink pointing nowhere.
    host_dir.symlink_to(tmp_path / "missing", target_is_directory=True)
    assert host_dir.is_symlink()
    assert not host_dir.exists()

    runner = HostDockerComposeRunner(
        project_dir=str(host_dir),
        local_project_dir=str(local_dir),
    )
    runner._ensure_host_project_read_alias()

    assert host_dir.is_symlink()
    assert host_dir.resolve() == local_dir.resolve()


def test_host_alias_fails_when_existing_path_targets_other_checkout(tmp_path) -> None:
    local_dir = tmp_path / "workspace" / "host_project"
    local_dir.mkdir(parents=True)
    other_dir = tmp_path / "other_project"
    other_dir.mkdir()
    host_dir = tmp_path / "host" / "MoonMind"
    host_dir.parent.mkdir(parents=True)
    # Pre-existing directory at the alias path that does not match the checkout.
    host_dir.mkdir()
    (host_dir / "marker").write_text("foreign", encoding="utf-8")

    runner = HostDockerComposeRunner(
        project_dir=str(host_dir),
        local_project_dir=str(local_dir),
    )

    with pytest.raises(ToolFailure) as exc_info:
        runner._ensure_host_project_read_alias()

    assert exc_info.value.error_code == "POLICY_VIOLATION"
    assert exc_info.value.details["failureClass"] == "policy_violation"
    # The pre-existing foreign directory must be left untouched.
    assert (host_dir / "marker").read_text(encoding="utf-8") == "foreign"


def test_host_alias_is_idempotent_when_already_aliased(tmp_path) -> None:
    local_dir = tmp_path / "workspace" / "host_project"
    local_dir.mkdir(parents=True)
    host_dir = tmp_path / "host" / "MoonMind"

    runner = HostDockerComposeRunner(
        project_dir=str(host_dir),
        local_project_dir=str(local_dir),
    )
    runner._ensure_host_project_read_alias()
    # Second call must not fail when the alias already resolves to the checkout.
    runner._ensure_host_project_read_alias()

    assert host_dir.is_symlink()
    assert host_dir.resolve() == local_dir.resolve()


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


def test_compose_base_command_uses_env_file_when_desired_state_exists(tmp_path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env.deploy"
    env_file.write_text("MOONMIND_IMAGE=example/app:tag\n", encoding="utf-8")

    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        env_file=str(env_file),
    )

    command = runner._compose_base_command()

    assert command[-2:] == ["--env-file", str(env_file)]


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
        ("docker", "compose", "ps"),
        max_stdout_chars=8,
        max_stderr_chars=8,
    )

    assert result["exitCode"] == 0
    assert result["stdout"] == "cdefTAIL"
    assert result["stderr"] == "err tail"
    assert result["command"][-1] == "ps"
    assert captured["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_host_compose_runner_aliases_host_project_path_for_file_reads(
    tmp_path, monkeypatch
):
    local_dir = tmp_path / "workspace" / "host_project"
    local_dir.mkdir(parents=True)
    compose = local_dir / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  api:\n"
        "    image: example/app:latest\n"
        "    env_file:\n"
        "      - .env\n",
        encoding="utf-8",
    )
    dotenv = local_dir / ".env"
    dotenv.write_text("POSTGRES_PASSWORD=secret\n", encoding="utf-8")
    host_dir = tmp_path / "host" / "MoonMind"
    captured: dict[str, Any] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(
        *args: str,
        cwd: str,
        env: Mapping[str, str],
        stdout: Any,
        stderr: Any,
    ):
        captured["args"] = args
        captured["cwd"] = cwd
        assert (host_dir / ".env").read_text(encoding="utf-8") == (
            "POSTGRES_PASSWORD=secret\n"
        )
        return FakeProcess()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    runner = HostDockerComposeRunner(
        project_dir=str(host_dir),
        local_project_dir=str(local_dir),
    )

    result = await runner._run_compose_command(("docker", "compose", "up", "-d"))

    assert result["exitCode"] == 0
    assert host_dir.is_symlink()
    assert host_dir.resolve() == local_dir
    assert captured["cwd"] == str(local_dir)
    assert "--project-directory" in captured["args"]
    assert str(host_dir) in captured["args"]


@pytest.mark.asyncio
async def test_host_compose_runner_prefers_compose_env_file_over_process_override(
    tmp_path, monkeypatch
):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / ".env.deploy"
    env_file.write_text("MOONMIND_IMAGE=example/app@sha256:abc\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(
        *args: str,
        cwd: str,
        env: Mapping[str, str],
        stdout: Any,
        stderr: Any,
    ):
        captured["args"] = args
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    runner = HostDockerComposeRunner(
        project_dir=str(tmp_path),
        env_file=str(env_file),
    )

    await runner._run_compose_command(
        ("docker", "compose", "pull"),
        requested_image="example/app:stable",
    )

    assert "--env-file" in captured["args"]
    assert captured["env"].get("MOONMIND_IMAGE") != "example/app:stable"


@pytest.mark.asyncio
async def test_temporal_deployment_evidence_writer_persists_json_artifact() -> None:
    class FakeArtifact:
        artifact_id = "artifact-123"

    class FakeArtifactService:
        def __init__(self) -> None:
            self.created: dict[str, Any] | None = None
            self.completed: dict[str, Any] | None = None

        async def create(self, **kwargs: Any):
            self.created = kwargs
            return FakeArtifact(), object()

        async def write_complete(self, **kwargs: Any):
            self.completed = kwargs
            return FakeArtifact()

    service = FakeArtifactService()
    writer = TemporalDeploymentEvidenceWriter(
        artifact_service=service,
        principal="system:deployment",
        execution_ref={"workflow_id": "wf", "run_id": "run"},
    )

    ref = await writer.write("verification", {"status": "SUCCEEDED"})

    assert ref == "artifact-123"
    assert service.created is not None
    assert service.created["metadata_json"]["deploymentEvidenceKind"] == "verification"
    assert service.completed is not None
    payload = json.loads(service.completed["payload"].decode("utf-8"))
    assert payload["status"] == "SUCCEEDED"


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


@pytest.mark.asyncio
async def test_run_compose_json_failure_bounds_stderr_in_tool_failure(
    tmp_path, monkeypatch
):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    huge_stderr = ("compose diagnostic line\n" * 5000).encode("utf-8")

    class FakeProcess:
        returncode = 1

        async def communicate(self):
            return b"", huge_stderr

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
    assert failure.details["failureClass"] == "compose_config_validation_failure"
    embedded_result = failure.details["result"]
    assert len(embedded_result["stderr"]) <= 2000
    assert len(embedded_result["stderr"]) < len(huge_stderr)
