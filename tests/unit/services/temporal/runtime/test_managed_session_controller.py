from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionLocator,
    CodexManagedSessionRecord,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer


class _LocalArtifactStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def write_artifact(
        self, *, job_id: str, artifact_name: str, data: bytes
    ) -> tuple[Path, str]:
        target_dir = self._root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / artifact_name
        target.write_bytes(data)
        return target, f"{job_id}/{artifact_name}"

    def resolve_storage_path(self, ref: str) -> Path:
        return self._root / ref


@pytest.mark.asyncio
async def test_controller_launches_container_and_returns_typed_handle(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    session_store = ManagedSessionStore(tmp_path / "session-store")
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_root / "task-1" / "repo"),
        sessionWorkspacePath=str(workspace_root / "task-1" / "session"),
        artifactSpoolPath=str(workspace_root / "task-1" / "artifacts"),
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                },
                "status": "ready",
                "imageRef": "ghcr.io/moonladderstudios/moonmind:latest",
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
                "metadata": {"vendorThreadId": "vendor-thread-1"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root=str(workspace_root),
        session_store=session_store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.container_id == "ctr-1"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"
    assert commands[0] == ("docker", "rm", "-f", "mm-codex-session-sess-1")
    run_command = commands[1]
    assert "--name" in run_command
    assert request.image_ref in run_command
    assert "python3" in run_command
    assert "moonmind.workflows.temporal.runtime.codex_session_runtime" in run_command
    stored = session_store.load("sess-1")
    assert stored is not None
    assert stored.task_run_id == "task-1"
    assert stored.container_id == "ctr-1"
    assert stored.runtime_id == "codex_cli"
    session_supervisor.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_controller_send_turn_executes_inside_container(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "completed",
                "metadata": {"assistantText": "OK"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.metadata["assistantText"] == "OK"
    exec_command = commands[0]
    assert exec_command[:3] == ("docker", "exec", "-i")
    assert "send_turn" in exec_command


@pytest.mark.asyncio
async def test_controller_clear_and_terminate_preserve_container_boundary(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/tmp/agent_jobs/task-1/repo",
            sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
            artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    commands: list[tuple[str, ...]] = []
    session_supervisor = AsyncMock()
    session_supervisor.emit_session_event = Mock()

    async def _publish_reset_artifacts(
        *,
        previous_record: CodexManagedSessionRecord,
        record: CodexManagedSessionRecord,
        action: str,
        reason: str | None,
    ):
        assert previous_record.session_epoch == 1
        assert previous_record.thread_id == "logical-thread-1"
        assert record.session_epoch == 2
        assert record.thread_id == "logical-thread-2"
        assert action == "clear_session"
        assert reason is None
        return await store.update(
            record.session_id,
            latest_control_event_ref="sess-1/session.control_event.epoch-2.json",
            latest_reset_boundary_ref="sess-1/session.reset_boundary.epoch-2.json",
        )

    session_supervisor.publish_reset_artifacts.side_effect = _publish_reset_artifacts

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "clear_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-2",
                },
                "status": "ready",
                "imageRef": "ghcr.io/moonladderstudios/moonmind:latest",
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    cleared = await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
        )
    )
    terminated = await controller.terminate_session(
        TerminateCodexManagedSessionRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
        )
    )

    assert cleared.session_state.session_epoch == 2
    stored = store.load("sess-1")
    assert stored is not None
    assert stored.session_epoch == 2
    assert stored.thread_id == "logical-thread-2"
    assert stored.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert stored.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    session_supervisor.publish_reset_artifacts.assert_awaited_once()
    publish_kwargs = session_supervisor.publish_reset_artifacts.await_args.kwargs
    assert publish_kwargs["previous_record"].session_epoch == 1
    assert publish_kwargs["previous_record"].thread_id == "logical-thread-1"
    assert publish_kwargs["record"].session_epoch == 2
    assert publish_kwargs["record"].thread_id == "logical-thread-2"
    assert terminated.status == "terminated"
    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")


@pytest.mark.asyncio
async def test_controller_clear_session_publishes_durable_reset_artifacts(
    tmp_path: Path,
) -> None:
    commands: list[tuple[str, ...]] = []
    store = ManagedSessionStore(tmp_path / "session-store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-1",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if "clear_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-2",
                },
                "status": "ready",
                "imageRef": "ghcr.io/moonladderstudios/moonmind:latest",
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=supervisor,
        command_runner=_fake_runner,
    )

    cleared = await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
            reason="reset stale context",
        )
    )
    stored = store.load("sess-1")
    assert stored is not None

    assert cleared.session_state.session_epoch == 2
    assert stored.thread_id == "logical-thread-2"
    assert stored.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert stored.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    control_payload = json.loads(
        artifact_storage.resolve_storage_path(
            stored.latest_control_event_ref
        ).read_text(encoding="utf-8")
    )
    assert control_payload["reason"] == "reset stale context"

    summary = await controller.fetch_session_summary(
        FetchCodexManagedSessionSummaryRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
        )
    )
    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="logical-thread-2",
            taskRunId="task-1",
        )
    )

    assert summary.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert summary.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert publication.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"


@pytest.mark.asyncio
async def test_controller_clear_session_rejects_stale_durable_locator(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="logical-thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )
    runner = AsyncMock()
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=runner,
    )

    with pytest.raises(
        RuntimeError,
        match="sessionEpoch does not match the durable managed session record",
    ):
        await controller.clear_session(
            CodexManagedSessionClearRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                newThreadId="logical-thread-2",
            )
        )

    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_summary_and_publication_read_from_durable_record(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            stdoutArtifactRef="sess-1/stdout.log",
            stderrArtifactRef="sess-1/stderr.log",
            diagnosticsRef="sess-1/diagnostics.json",
            latestSummaryRef="sess-1/session.summary.json",
            latestCheckpointRef="sess-1/session.step_checkpoint.json",
            latestControlEventRef="sess-1/session.control_event.epoch-2.json",
            latestResetBoundaryRef="sess-1/session.reset_boundary.epoch-2.json",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=AsyncMock(),
    )

    summary = await controller.fetch_session_summary(
        FetchCodexManagedSessionSummaryRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
        )
    )
    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
            taskRunId="task-1",
        )
    )

    assert summary.latest_summary_ref == "sess-1/session.summary.json"
    assert summary.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert summary.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert summary.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert summary.metadata["stdoutArtifactRef"] == "sess-1/stdout.log"
    assert publication.published_artifact_refs == (
        "sess-1/stdout.log",
        "sess-1/stderr.log",
        "sess-1/diagnostics.json",
        "sess-1/session.summary.json",
        "sess-1/session.step_checkpoint.json",
        "sess-1/session.control_event.epoch-2.json",
        "sess-1/session.reset_boundary.epoch-2.json",
    )
    assert publication.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert publication.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"


@pytest.mark.asyncio
async def test_controller_publication_uses_snapshot_without_stopping_supervision(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=2,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            controlUrl="docker-exec://mm-codex-session-sess-1",
            status="ready",
            workspacePath="/work/agent_jobs/task-1/repo",
            sessionWorkspacePath="/work/agent_jobs/task-1/session",
            artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = AsyncMock()
    published_record = CodexManagedSessionRecord(
        sessionId="sess-1",
        sessionEpoch=2,
        taskRunId="task-1",
        containerId="ctr-1",
        threadId="thread-2",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl="docker-exec://mm-codex-session-sess-1",
        status="ready",
        workspacePath="/work/agent_jobs/task-1/repo",
        sessionWorkspacePath="/work/agent_jobs/task-1/session",
        artifactSpoolPath="/work/agent_jobs/task-1/artifacts",
        stdoutArtifactRef="sess-1/stdout.log",
        stderrArtifactRef="sess-1/stderr.log",
        diagnosticsRef="sess-1/diagnostics.json",
        latestSummaryRef="sess-1/session.summary.json",
        latestCheckpointRef="sess-1/session.step_checkpoint.json",
        startedAt="2026-04-06T12:00:00Z",
    )
    session_supervisor.publish_snapshot.return_value = published_record
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=AsyncMock(),
    )

    publication = await controller.publish_session_artifacts(
        PublishCodexManagedSessionArtifactsRequest(
            sessionId="sess-1",
            sessionEpoch=2,
            containerId="ctr-1",
            threadId="thread-2",
            taskRunId="task-1",
        )
    )

    session_supervisor.publish_snapshot.assert_awaited_once_with("sess-1")
    session_supervisor.finalize.assert_not_called()
    assert publication.published_artifact_refs == (
        "sess-1/stdout.log",
        "sess-1/stderr.log",
        "sess-1/diagnostics.json",
        "sess-1/session.summary.json",
        "sess-1/session.step_checkpoint.json",
    )


@pytest.mark.asyncio
async def test_controller_reconcile_reattaches_or_degrades_active_sessions(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-ok",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-ok",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-missing",
            sessionEpoch=1,
            taskRunId="task-2",
            containerId="ctr-missing",
            threadId="thread-2",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://missing",
            status="busy",
            workspacePath="/work/repo2",
            sessionWorkspacePath="/work/session2",
            artifactSpoolPath="/work/artifacts2",
            startedAt="2026-04-06T12:00:00Z",
        )
    )
    session_supervisor = AsyncMock()

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "inspect", "-f"):
            container_id = command[-1]
            if container_id == "ctr-ok":
                return 0, "true\n", ""
            return 1, "", "No such container"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=session_supervisor,
        command_runner=_fake_runner,
    )

    reconciled = await controller.reconcile()

    assert sorted(record.session_id for record in reconciled) == ["sess-missing", "sess-ok"]
    session_supervisor.start.assert_awaited_once()
    assert store.load("sess-ok").status == "ready"
    degraded = store.load("sess-missing")
    assert degraded is not None
    assert degraded.status == "degraded"
    assert degraded.error_message == "managed session container is missing during reconcile"


@pytest.mark.asyncio
async def test_controller_reconcile_surfaces_transient_inspect_failures(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-ok",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-ok",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if command[:3] == ("docker", "inspect", "-f"):
            return 1, "", "docker daemon unavailable"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        session_supervisor=AsyncMock(),
        command_runner=_fake_runner,
    )

    with pytest.raises(RuntimeError, match="failed to inspect managed session container ctr-ok"):
        await controller.reconcile()


@pytest.mark.asyncio
async def test_controller_session_status_persists_returned_session_identity(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="ready",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "session_status" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-2",
                    "threadId": "thread-2",
                },
                "status": "ready",
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    await controller.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
        )
    )

    updated = store.load("sess-1")
    assert updated is not None
    assert updated.session_epoch == 2
    assert updated.container_id == "ctr-2"
    assert updated.thread_id == "thread-2"


@pytest.mark.asyncio
async def test_controller_send_turn_skips_missing_durable_record(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 2,
                    "containerId": "ctr-1",
                    "threadId": "thread-2",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "completed",
                "metadata": {"assistantText": "OK"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert store.load("sess-1") is None


@pytest.mark.asyncio
async def test_controller_send_turn_persists_failed_turn_status(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "send_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {"reason": "turn execution failed"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    updated = store.load("sess-1")
    assert response.status == "failed"
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error_message == "turn execution failed"


@pytest.mark.asyncio
async def test_controller_interrupt_turn_preserves_failed_runtime_result(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "session-store")
    store.save(
        CodexManagedSessionRecord(
            sessionId="sess-1",
            sessionEpoch=1,
            taskRunId="task-1",
            containerId="ctr-1",
            threadId="thread-1",
            runtimeId="codex_cli",
            imageRef="img",
            controlUrl="docker-exec://ok",
            status="busy",
            workspacePath="/work/repo",
            sessionWorkspacePath="/work/session",
            artifactSpoolPath="/work/artifacts",
            startedAt="2026-04-06T12:00:00Z",
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if "interrupt_turn" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "thread-1",
                    "activeTurnId": None,
                },
                "turnId": "vendor-turn-1",
                "status": "failed",
                "metadata": {"reason": "turn-id mismatch"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        session_store=store,
        command_runner=_fake_runner,
    )

    response = await controller.interrupt_turn(
        InterruptCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="thread-1",
            turnId="vendor-turn-1",
        )
    )

    updated = store.load("sess-1")
    assert response.status == "failed"
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error_message == "turn-id mismatch"


@pytest.mark.asyncio
async def test_controller_launch_retries_ready_probe_errors(tmp_path: Path) -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    ready_attempts = 0

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        nonlocal ready_attempts
        if command[:3] == ("docker", "rm", "-f"):
            return 1, "", "No such container"
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            ready_attempts += 1
            if ready_attempts == 1:
                return 1, "", "container not ready"
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            payload = {
                "sessionState": {
                    "sessionId": "sess-1",
                    "sessionEpoch": 1,
                    "containerId": "ctr-1",
                    "threadId": "logical-thread-1",
                },
                "status": "ready",
                "imageRef": request.image_ref,
                "controlUrl": "docker-exec://mm-codex-session-sess-1",
                "metadata": {"vendorThreadId": "vendor-thread-1"},
            }
            return 0, json.dumps(payload), ""
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
        ready_poll_attempts=2,
    )

    handle = await controller.launch_session(request)

    assert handle.status == "ready"
    assert ready_attempts == 2


@pytest.mark.asyncio
async def test_controller_launch_cleans_up_container_when_handshake_fails() -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )
    commands: list[tuple[str, ...]] = []

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        commands.append(command)
        if command[:3] == ("docker", "rm", "-f"):
            return 0, "", ""
        if command[:2] == ("docker", "run"):
            return 0, "ctr-1\n", ""
        if "ready" in command:
            return 0, '{"ready": true}\n', ""
        if "launch_session" in command:
            return 1, "", "launch failed"
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
        ready_poll_interval_seconds=0,
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        await controller.launch_session(request)

    assert commands[-1] == ("docker", "rm", "-f", "ctr-1")


@pytest.mark.asyncio
async def test_controller_launch_rejects_reserved_session_environment() -> None:
    request = LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath="/tmp/agent_jobs/task-1/repo",
        sessionWorkspacePath="/tmp/agent_jobs/task-1/session",
        artifactSpoolPath="/tmp/agent_jobs/task-1/artifacts",
        codexHomePath="/home/app/.codex",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        environment={"MOONMIND_SESSION_WORKSPACE_PATH": "/tmp/override"},
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        raise AssertionError(f"unexpected command: {command}")

    controller = DockerCodexManagedSessionController(
        workspace_volume_name="agent_workspaces",
        codex_volume_name="codex_auth_volume",
        workspace_root="/tmp/agent_jobs",
        command_runner=_fake_runner,
    )

    with pytest.raises(RuntimeError, match="reserved session keys"):
        await controller.launch_session(request)
