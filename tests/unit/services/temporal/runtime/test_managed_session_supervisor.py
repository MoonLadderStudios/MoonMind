from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.codex_conformance.canary import (
    CANARY_DUPLICATE_EXECUTION,
    CANARY_SCENARIO_VERSION,
    CANARY_SESSION_TERMINATED_EARLY,
    CANARY_TOOL_PROTOCOL_INCOMPATIBLE,
    DEFAULT_MARKER_PATH,
    validate_canary_evidence,
)
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.managed_session_supervisor import (
    ManagedSessionSupervisor,
)

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

def _record(tmp_path: Path) -> CodexManagedSessionRecord:
    workspace = tmp_path / "repo"
    session_workspace = tmp_path / "session"
    spool = tmp_path / "artifacts"
    workspace.mkdir()
    session_workspace.mkdir()
    spool.mkdir()
    return CodexManagedSessionRecord(
        sessionId="sess-1",
        sessionEpoch=1,
        agentRunId="task-1",
        containerId="ctr-1",
        threadId="thread-1",
        runtimeId="codex_cli",
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
        controlUrl="docker-exec://mm-codex-session-sess-1",
        status="ready",
        workspacePath=str(workspace),
        sessionWorkspacePath=str(session_workspace),
        artifactSpoolPath=str(spool),
        startedAt=datetime(2026, 4, 6, 12, 0, tzinfo=UTC),
    )

def _canary_marker() -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "nonce": "nonce-123456",
        "command": "sleep 4 && printf ok",
        "processExitCode": 0,
        "startedAt": "2026-07-10T12:00:00Z",
        "completedAt": "2026-07-10T12:00:04Z",
        "durationSeconds": 4.0,
        "outputSha256": "a" * 64,
    }

def _canary_events(*, session_id: str = "sess-1") -> list[dict[str, object]]:
    return [
        {
            "stream": "session",
            "timestamp": "2026-07-10T12:00:01Z",
            "kind": "tool_call_started",
            "text": "Tool call started.",
            "sessionId": session_id,
            "turnId": "turn-1",
        },
        {
            "stream": "session",
            "timestamp": "2026-07-10T12:00:02Z",
            "kind": "tool_call_completed",
            "text": "Tool call completed.",
            "sessionId": session_id,
            "turnId": "turn-1",
        },
        {
            "stream": "session",
            "timestamp": "2026-07-10T12:00:06Z",
            "kind": "turn_completed",
            "text": "Turn completed.",
            "sessionId": session_id,
            "turnId": "turn-1",
        },
    ]

def _canary_evidence(
    *,
    record: CodexManagedSessionRecord,
    observation: dict[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "issueRef": "MoonLadderStudios/MoonMind#3150",
        "scenarioVersion": CANARY_SCENARIO_VERSION,
        "candidateImageDigest": "sha256:" + "b" * 64,
        "codexCliVersion": "codex-test",
        "moonmindBuildSha": "build-test",
        "runId": "run-1",
        "workflowId": "workflow-1",
        "sessionId": observation["sessionId"],
        "sessionIdsObserved": observation["sessionIdsObserved"],
        "turnId": observation["turnId"],
        "markerArtifactRef": observation["markerArtifactRef"],
        "markerPath": observation["markerPath"],
        "marker": _canary_marker(),
        "timestamps": observation["timestamps"],
        "protocolEvents": observation["protocolEvents"],
        "finalAgentStatus": "completed",
        "agentRunResultSuccessful": True,
        "cleanupObserved": observation["cleanupObserved"],
        "cleanupSessionId": observation["cleanupSessionId"],
        "githubMutationCount": observation["githubMutationCount"],
        "processInvocationCount": observation["processInvocationCount"],
        "markerArtifactCreateCount": observation["markerArtifactCreateCount"],
        "providerAvailable": True,
        "failureCode": None,
        "evidenceArtifactRef": observation["evidenceArtifactRef"],
    }

@pytest.mark.asyncio
async def test_session_supervisor_publishes_artifacts_and_offsets(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_text("session started\nassistant: OK\n", encoding="utf-8")
    (spool / "stderr.log").write_text("warning: none\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    watched = store.load("sess-1")

    finalized = await supervisor.finalize("sess-1", status="terminated")

    assert watched is not None
    assert watched.last_log_offset == len("session started\nassistant: OK\nwarning: none\n")
    assert watched.stdout_log_offset == len("session started\nassistant: OK\n")
    assert watched.stderr_log_offset == len("warning: none\n")
    assert watched.last_log_at is not None
    assert finalized.status == "terminated"
    assert finalized.stdout_artifact_ref == "sess-1/stdout.log"
    assert finalized.stderr_artifact_ref == "sess-1/stderr.log"
    assert finalized.diagnostics_ref == "sess-1/diagnostics.json"
    assert finalized.latest_summary_ref == "sess-1/session.summary.json"
    assert finalized.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"
    assert finalized.last_log_offset == len("session started\nassistant: OK\nwarning: none\n")
    assert finalized.stdout_log_offset == len("session started\nassistant: OK\n")
    assert finalized.stderr_log_offset == len("warning: none\n")
    assert finalized.last_log_at is not None
    assert artifact_storage.resolve_storage_path("sess-1/stdout.log").read_text(encoding="utf-8") == "session started\nassistant: OK\n"
    assert artifact_storage.resolve_storage_path("sess-1/stderr.log").read_text(encoding="utf-8") == "warning: none\n"
    assert artifact_storage.resolve_storage_path("sess-1/session.summary.json").exists()
    assert artifact_storage.resolve_storage_path("sess-1/session.step_checkpoint.json").exists()

@pytest.mark.asyncio
async def test_session_supervisor_publishes_output_chunks_to_live_spool(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_text("session started\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    with (spool / "stdout.log").open("a", encoding="utf-8") as handle:
        handle.write("assistant: OK\n")
    (spool / "stderr.log").write_text("warning: none\n", encoding="utf-8")
    await asyncio.sleep(0.05)

    live_spool = Path(record.workspace_path) / "live_streams.spool"
    payloads = [
        json.loads(line)
        for line in live_spool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    stdout_events = [
        item for item in payloads if item["stream"] == "stdout"
    ]
    stderr_events = [
        item for item in payloads if item["stream"] == "stderr"
    ]

    assert [item["kind"] for item in stdout_events] == ["stdout_chunk", "stdout_chunk"]
    assert stdout_events[0]["text"] == "session started\n"
    assert stdout_events[0]["offset"] == 0
    assert stdout_events[1]["text"] == "assistant: OK\n"
    assert stdout_events[1]["offset"] == len("session started\n")
    assert stderr_events[0]["kind"] == "stderr_chunk"
    assert stderr_events[0]["text"] == "warning: none\n"
    assert stderr_events[0]["offset"] == 0

    snapshot = await supervisor.publish_snapshot("sess-1")
    journal = artifact_storage.resolve_storage_path(snapshot.observability_events_ref)
    journal_text = journal.read_text(encoding="utf-8")
    assert '"stream":"stdout"' in journal_text
    assert '"kind":"stdout_chunk"' in journal_text
    assert '"stream":"stderr"' in journal_text
    assert '"kind":"stderr_chunk"' in journal_text

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_session_supervisor_syncs_active_turn_from_runtime_state(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    state_path = Path(record.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_path.write_text(
        json.dumps(
            {
                "sessionId": record.session_id,
                "sessionEpoch": record.session_epoch,
                "logicalThreadId": record.thread_id,
                "vendorThreadId": "vendor-thread-1",
                "containerId": record.container_id,
                "activeTurnId": "vendor-turn-1",
                "lastTurnId": "vendor-turn-1",
                "lastTurnStatus": "running",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    await supervisor.start(record)
    await asyncio.sleep(0.05)

    watched = store.load("sess-1")
    assert watched is not None
    assert watched.status == "busy"
    assert watched.active_turn_id == "vendor-turn-1"
    assert watched.updated_at is not None

    live_spool = Path(record.workspace_path) / "live_streams.spool"
    payloads = [
        json.loads(line)
        for line in live_spool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert payloads == [
        {
            "runId": "task-1",
            "sequence": 1,
            "stream": "session",
            "timestamp": payloads[0]["timestamp"],
            "text": "Turn started: vendor-turn-1.",
            "kind": "turn_started",
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "vendor-turn-1",
            "activeTurnId": "vendor-turn-1",
            "metadata": {
                "action": "send_turn",
                "source": "managed_session_runtime_state",
            },
        }
    ]

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_session_supervisor_ignores_terminal_runtime_active_turn(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    state_path = Path(record.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_path.write_text(
        json.dumps(
            {
                "sessionId": record.session_id,
                "sessionEpoch": record.session_epoch,
                "logicalThreadId": record.thread_id,
                "vendorThreadId": "vendor-thread-1",
                "containerId": record.container_id,
                "activeTurnId": "vendor-turn-1",
                "lastTurnId": "vendor-turn-1",
                "lastTurnStatus": "completed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    await supervisor.start(record)
    await asyncio.sleep(0.05)

    watched = store.load("sess-1")
    assert watched is not None
    assert watched.status == "ready"
    assert watched.active_turn_id is None
    assert not (Path(record.workspace_path) / "live_streams.spool").exists()

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_session_supervisor_resumes_from_persisted_stream_offsets(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    spool = Path(record.artifact_spool_path)
    stdout_prefix = b"old stdout\n"
    stderr_prefix = b"old stderr\n"
    (spool / "stdout.log").write_bytes(stdout_prefix)
    (spool / "stderr.log").write_bytes(stderr_prefix)
    store.save(
        record.model_copy(
            update={
                "last_log_offset": len(stdout_prefix) + len(stderr_prefix),
                "stdout_log_offset": len(stdout_prefix),
                "stderr_log_offset": len(stderr_prefix),
            }
        )
    )

    await supervisor.start(record)
    with (spool / "stdout.log").open("ab") as handle:
        handle.write(b"new stdout\n")
    with (spool / "stderr.log").open("ab") as handle:
        handle.write(b"new stderr\n")
    await asyncio.sleep(0.05)

    live_spool = Path(record.workspace_path) / "live_streams.spool"
    payloads = [
        json.loads(line)
        for line in live_spool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [(item["stream"], item["text"], item["offset"]) for item in payloads] == [
        ("stdout", "new stdout\n", len(stdout_prefix)),
        ("stderr", "new stderr\n", len(stderr_prefix)),
    ]
    watched = store.load("sess-1")
    assert watched is not None
    assert watched.stdout_log_offset == len(stdout_prefix) + len(b"new stdout\n")
    assert watched.stderr_log_offset == len(stderr_prefix) + len(b"new stderr\n")
    assert watched.last_log_offset == watched.stdout_log_offset + watched.stderr_log_offset

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_session_supervisor_conservatively_replays_without_stream_offsets(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    spool = Path(record.artifact_spool_path)
    (spool / "stdout.log").write_text("missed while down\n", encoding="utf-8")
    store.save(record.model_copy(update={"last_log_offset": 123}))

    await supervisor.start(record)
    await asyncio.sleep(0.05)

    live_spool = Path(record.workspace_path) / "live_streams.spool"
    payloads = [
        json.loads(line)
        for line in live_spool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [(item["stream"], item["text"], item["offset"]) for item in payloads] == [
        ("stdout", "missed while down\n", 0),
    ]

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_session_supervisor_waits_for_complete_utf8_sequences(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_bytes("€".encode("utf-8")[:1])
    await asyncio.sleep(0.05)
    assert not (Path(record.workspace_path) / "live_streams.spool").exists()

    with (spool / "stdout.log").open("ab") as handle:
        handle.write("€\n".encode("utf-8")[1:])
    await asyncio.sleep(0.05)

    live_spool = Path(record.workspace_path) / "live_streams.spool"
    payloads = [
        json.loads(line)
        for line in live_spool.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [(item["stream"], item["text"], item["offset"]) for item in payloads] == [
        ("stdout", "€\n", 0),
    ]
    watched = store.load("sess-1")
    assert watched is not None
    assert watched.stdout_log_offset == len("€\n".encode("utf-8"))

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_publish_snapshot_keeps_watch_task_running(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    spool = Path(record.artifact_spool_path)

    await supervisor.start(record)
    (spool / "stdout.log").write_text("first\n", encoding="utf-8")
    await asyncio.sleep(0.05)

    snapshot = await supervisor.publish_snapshot("sess-1")
    assert snapshot.stdout_artifact_ref == "sess-1/stdout.log"
    assert snapshot.latest_summary_ref == "sess-1/session.summary.json"
    assert snapshot.latest_checkpoint_ref == "sess-1/session.step_checkpoint.json"

    (spool / "stdout.log").write_text("first\nsecond\n", encoding="utf-8")
    await asyncio.sleep(0.05)
    watched = store.load("sess-1")

    assert watched is not None
    assert watched.last_log_offset == len("first\nsecond\n")

    await supervisor.finalize("sess-1", status="terminated")

@pytest.mark.asyncio
async def test_publish_snapshot_persists_run_keyed_session_events(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    log_streamer = RuntimeLogStreamer(artifact_storage)
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=log_streamer,
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)

    supervisor.emit_session_event(
        record=record,
        text="Session cleared.",
        kind="session_cleared",
        metadata={"reason": "operator_reset"},
    )
    supervisor.emit_session_event(
        record=record,
        text="User message submitted for turn vendor-turn-1.",
        kind="user_message_submitted",
        turn_id="vendor-turn-1",
        active_turn_id="vendor-turn-1",
        metadata={"action": "send_turn", "messageLength": 12},
    )
    supervisor.emit_session_event(
        record=record,
        text="Turn failed: vendor-turn-1.",
        kind="turn_failed",
        turn_id="vendor-turn-1",
        metadata={"action": "send_turn", "failureClass": "permanent"},
    )

    snapshot = await supervisor.publish_snapshot("sess-1")
    diagnostics_payload = json.loads(
        artifact_storage.resolve_storage_path(snapshot.diagnostics_ref).read_text(
            encoding="utf-8"
        )
    )

    assert diagnostics_payload["observability_events"][0]["stream"] == "session"
    assert diagnostics_payload["observability_events"][0]["kind"] == "session_cleared"
    assert diagnostics_payload["observability_events"][0]["metadata"]["reason"] == "operator_reset"
    assert [
        event["kind"] for event in diagnostics_payload["observability_events"]
    ] == [
        "session_cleared",
        "user_message_submitted",
        "turn_failed",
        "summary_published",
        "checkpoint_published",
    ]
    assert snapshot.observability_events_ref == "sess-1/observability.events.jsonl"
    journal = artifact_storage.resolve_storage_path(snapshot.observability_events_ref)
    assert journal.exists()
    journal_text = journal.read_text(encoding="utf-8")
    assert '"stream":"session"' in journal_text
    assert '"kind":"user_message_submitted"' in journal_text
    assert '"kind":"turn_failed"' in journal_text
    assert '"kind":"summary_published"' in journal_text
    assert '"kind":"checkpoint_published"' in journal_text
    assert log_streamer.consume_observability_events(record.agent_run_id) == []

@pytest.mark.asyncio
async def test_publish_snapshot_publishes_codex_canary_observation_from_runtime_evidence(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    record = _record(tmp_path)
    store.save(record)
    marker_path = Path(record.workspace_path) / DEFAULT_MARKER_PATH
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text(json.dumps(_canary_marker()) + "\n", encoding="utf-8")
    for event in _canary_events():
        supervisor.emit_session_event(
            record=record,
            text=str(event["text"]),
            kind=str(event["kind"]),
            turn_id=str(event["turnId"]),
            metadata=dict(event.get("metadata", {})),
        )

    snapshot = await supervisor.publish_snapshot("sess-1")

    observation = snapshot.metadata["codexConformanceCanary"]
    assert observation["markerArtifactRef"] == "sess-1/codex_conformance_canary.marker.json"
    assert observation["evidenceArtifactRef"] == "sess-1/codex_conformance_canary.evidence.json"
    assert observation["sessionIdsObserved"] == ["sess-1"]
    assert observation["protocolEvents"] == [
        "resumable_process_handle",
        "poll_after_yield",
    ]
    marker_artifact = artifact_storage.resolve_storage_path(
        "sess-1/codex_conformance_canary.marker.json"
    )
    evidence_artifact = artifact_storage.resolve_storage_path(
        "sess-1/codex_conformance_canary.evidence.json"
    )
    assert json.loads(marker_artifact.read_text(encoding="utf-8")) == _canary_marker()
    assert "codexConformanceCanary" in json.loads(
        evidence_artifact.read_text(encoding="utf-8")
    )

def test_codex_canary_observation_validates_success(tmp_path: Path) -> None:
    record = _record(tmp_path)
    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=_canary_events(),
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is not None
    result = validate_canary_evidence(
        _canary_evidence(record=record, observation=observation),
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is True

def test_codex_canary_observation_requires_resumable_handle(tmp_path: Path) -> None:
    record = _record(tmp_path)
    events = [
        {
            **event,
            "kind": "assistant_message",
            "text": "Assistant message completed.",
        }
        for event in _canary_events()
    ]

    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=events,
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is None

def test_codex_canary_observation_requires_poll_after_yield(tmp_path: Path) -> None:
    record = _record(tmp_path)
    events = _canary_events()[:1]

    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=events,
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is None

def test_codex_canary_observation_preserves_early_cleanup_failure(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path)
    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=_canary_events(),
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:03Z",
    )

    assert observation is not None
    result = validate_canary_evidence(
        _canary_evidence(record=record, observation=observation),
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is False
    assert result.reason_code == CANARY_TOOL_PROTOCOL_INCOMPATIBLE

@pytest.mark.parametrize(
    ("metadata", "reason_code"),
    [
        ({"processInvocationCount": 2}, CANARY_DUPLICATE_EXECUTION),
        ({"markerArtifactCreateCount": 2}, CANARY_DUPLICATE_EXECUTION),
    ],
)
def test_codex_canary_observation_preserves_duplicate_counts(
    tmp_path: Path,
    metadata: dict[str, object],
    reason_code: str,
) -> None:
    record = _record(tmp_path)
    events = _canary_events()
    events[0]["metadata"] = metadata
    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=events,
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is not None
    result = validate_canary_evidence(
        _canary_evidence(record=record, observation=observation),
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is False
    assert result.reason_code == reason_code

def test_codex_canary_observation_preserves_changed_session_failure(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path)
    events = _canary_events()
    events[1]["sessionId"] = "sess-2"
    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=events,
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is not None
    result = validate_canary_evidence(
        _canary_evidence(record=record, observation=observation),
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is False
    assert result.reason_code == CANARY_SESSION_TERMINATED_EARLY

def test_codex_canary_observation_preserves_github_mutation_failure(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path)
    events = _canary_events()
    events.append(
        {
            "stream": "session",
            "timestamp": "2026-07-10T12:00:03Z",
            "kind": "tool_call_completed",
            "text": "GitHub mutation attempted.",
            "sessionId": "sess-1",
            "turnId": "turn-1",
            "metadata": {"githubMutation": True},
        }
    )
    observation = ManagedSessionSupervisor._build_codex_canary_observation(
        record=record,
        status="terminated",
        marker_ref="sess-1/codex_conformance_canary.marker.json",
        evidence_ref="sess-1/codex_conformance_canary.evidence.json",
        events=events,
        marker=_canary_marker(),
        cleanup_timestamp="2026-07-10T12:00:07Z",
    )

    assert observation is not None
    result = validate_canary_evidence(
        _canary_evidence(record=record, observation=observation),
        now=datetime(2026, 7, 10, 12, 1, 0, tzinfo=UTC),
    )
    assert result.passed is False
    assert result.reason_code == CANARY_TOOL_PROTOCOL_INCOMPATIBLE

@pytest.mark.asyncio
async def test_publish_reset_artifacts_writes_epoch_specific_control_and_boundary_refs(
    tmp_path: Path,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    previous = _record(tmp_path)
    store.save(previous)
    current = previous.model_copy(
        update={
            "session_epoch": 2,
            "thread_id": "thread-2",
            "updated_at": datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        }
    )

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=current,
        action="clear_session",
        reason="reset stale context",
    )

    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"

    control_payload = json.loads(
        artifact_storage.resolve_storage_path(
            "sess-1/session.control_event.epoch-2.json"
        ).read_text(encoding="utf-8")
    )
    boundary_payload = json.loads(
        artifact_storage.resolve_storage_path(
            "sess-1/session.reset_boundary.epoch-2.json"
        ).read_text(encoding="utf-8")
    )

    assert control_payload["linkType"] == "session.control_event"
    assert control_payload["action"] == "clear_session"
    assert control_payload["previousSessionEpoch"] == 1
    assert control_payload["newSessionEpoch"] == 2
    assert control_payload["previousThreadId"] == "thread-1"
    assert control_payload["newThreadId"] == "thread-2"
    assert control_payload["reason"] == "reset stale context"

    assert boundary_payload["linkType"] == "session.reset_boundary"
    assert boundary_payload["boundaryKind"] == "clear_session"
    assert boundary_payload["previousSessionEpoch"] == 1
    assert boundary_payload["sessionEpoch"] == 2
    assert boundary_payload["previousThreadId"] == "thread-1"
    assert boundary_payload["threadId"] == "thread-2"

    next_record = published.model_copy(
        update={
            "session_epoch": 3,
            "thread_id": "thread-3",
            "updated_at": datetime(2026, 4, 7, 8, 5, tzinfo=UTC),
        }
    )
    republished = await supervisor.publish_reset_artifacts(
        previous_record=published,
        record=next_record,
        action="clear_session",
        reason=None,
    )

    assert republished.latest_control_event_ref == "sess-1/session.control_event.epoch-3.json"
    assert republished.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-3.json"
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.control_event.epoch-2.json"
    ).exists()
    assert artifact_storage.resolve_storage_path(
        "sess-1/session.reset_boundary.epoch-2.json"
    ).exists()

@pytest.mark.asyncio
async def test_publish_reset_artifacts_preserves_newer_store_fields(tmp_path: Path) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    previous = _record(tmp_path)
    store.save(previous)
    await store.update(
        "sess-1",
        session_epoch=2,
        thread_id="thread-2",
        last_log_offset=77,
        last_log_at=datetime(2026, 4, 7, 8, 1, tzinfo=UTC),
    )
    stale_current = previous.model_copy(
        update={
            "session_epoch": 2,
            "thread_id": "thread-2",
            "last_log_offset": None,
            "last_log_at": None,
        }
    )

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=stale_current,
        action="clear_session",
        reason=None,
    )

    assert published.last_log_offset == 77
    assert published.last_log_at == datetime(2026, 4, 7, 8, 1, tzinfo=UTC)
    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"

@pytest.mark.asyncio
async def test_publish_reset_artifacts_tolerates_event_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ManagedSessionStore(tmp_path / "store")
    artifact_storage = _LocalArtifactStorage(tmp_path / "published")
    supervisor = ManagedSessionSupervisor(
        store=store,
        log_streamer=RuntimeLogStreamer(artifact_storage),
        artifact_storage=artifact_storage,
        poll_interval_seconds=0.01,
    )
    previous = _record(tmp_path)
    store.save(previous)
    current = previous.model_copy(
        update={
            "session_epoch": 2,
            "thread_id": "thread-2",
            "updated_at": datetime(2026, 4, 7, 8, 0, tzinfo=UTC),
        }
    )

    def _raise(*args, **kwargs) -> None:
        raise RuntimeError("publisher unavailable")

    monkeypatch.setattr(supervisor._log_streamer, "emit_observability_event", _raise)

    published = await supervisor.publish_reset_artifacts(
        previous_record=previous,
        record=current,
        action="clear_session",
        reason="reset stale context",
    )

    assert published.latest_control_event_ref == "sess-1/session.control_event.epoch-2.json"
    assert published.latest_reset_boundary_ref == "sess-1/session.reset_boundary.epoch-2.json"
