from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionRecord,
    PublishCodexManagedSessionArtifactsRequest,
)
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


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


async def test_reset_boundary_preserves_latest_context_pack_ref_for_publication(
    tmp_path: Path,
) -> None:
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
            metadata={
                "latestContextPackRef": "artifacts/context/rag-context-abc123.json",
                "retrievedContextArtifactPath": "artifacts/context/rag-context-abc123.json",
                "retrievedContextTransport": "direct",
                "retrievedContextItemCount": 2,
                "retrievalDurabilityAuthority": "artifact_ref",
                "sessionContinuityCacheStatus": "advisory_only",
            },
            startedAt=datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        )
    )

    async def _fake_runner(
        command: tuple[str, ...],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
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

    await controller.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
            reason="reset stale context",
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

    assert publication.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
    assert publication.metadata["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"

    boundary_payload = json.loads(
        artifact_storage.resolve_storage_path(publication.latest_reset_boundary_ref).read_text(encoding="utf-8")
    )
    assert boundary_payload["metadata"]["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"
