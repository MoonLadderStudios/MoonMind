from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionLocator,
    InterruptCodexManagedSessionTurnRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
)
from moonmind.workflows.automation import models as automation_models
from moonmind.workflows.automation.preflight import CodexPreflightResult
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON,
    CodexAppServerRpcClient,
    CodexManagedSessionRuntime,
    CodexSessionRuntimeState,
    _ROLLOUT_RECOVERY_MAX_BYTES,
    _RolloutLiveMirror,
    _is_empty_assistant_failure_reason,
    _run_ready,
)
from tests.helpers.codex_session_runtime import (
    launch_request,
    write_fake_app_server,
)


def _iso_timestamp(*, minutes_offset: int) -> str:
    return (
        datetime.now(UTC) + timedelta(minutes=minutes_offset)
    ).isoformat().replace("+00:00", "Z")


def _write_fake_codex_logs(
    codex_home_path: str | Path,
    *,
    entries: list[str],
    filename: str = "logs_1.sqlite",
) -> Path:
    log_path = Path(codex_home_path) / filename
    connection = sqlite3.connect(log_path)
    try:
        connection.execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, feedback_log_body TEXT)"
        )
        for entry in entries:
            connection.execute(
                "INSERT INTO logs (feedback_log_body) VALUES (?)",
                (entry,),
            )
        connection.commit()
    finally:
        connection.close()
    return log_path


def _write_fake_codex_logs_with_timestamps(
    codex_home_path: str | Path,
    *,
    entries: list[tuple[int, str]],
    filename: str = "logs_1.sqlite",
) -> Path:
    log_path = Path(codex_home_path) / filename
    connection = sqlite3.connect(log_path)
    try:
        connection.execute(
            "CREATE TABLE logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts INTEGER, "
            "feedback_log_body TEXT"
            ")"
        )
        for timestamp, entry in entries:
            connection.execute(
                "INSERT INTO logs (ts, feedback_log_body) VALUES (?, ?)",
                (timestamp, entry),
            )
        connection.commit()
    finally:
        connection.close()
    return log_path


def _runtime_for_rollout_mirror(tmp_path: Path) -> CodexManagedSessionRuntime:
    request = launch_request(tmp_path)
    return CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "pass"),
    )


def _write_flaky_sqlite_init_app_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_flaky_sqlite_init_app_server.py"
    marker_path = tmp_path / "sqlite-init-failed-once.marker"
    script.write_text(
        f"""
import json
import os
import sys

MARKER_PATH = {str(marker_path)!r}

if not os.path.exists(MARKER_PATH):
    with open(MARKER_PATH, "w", encoding="utf-8") as marker:
        marker.write("failed once\\n")
    sys.stderr.write(
        "Error: failed to initialize sqlite state runtime under /tmp/codex-home\\n"
    )
    sys.stderr.flush()
    sys.exit(1)

for line in sys.stdin:
    message = json.loads(line)
    msg_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({{
            "id": msg_id,
            "result": {{
                "userAgent": "fake/0.1",
                "codexHome": "/tmp/fake-codex-home",
                "platformFamily": "unix",
                "platformOs": "linux",
            }},
        }}) + "\\n")
        sys.stdout.flush()
    elif method == "thread/start":
        sys.stdout.write(json.dumps({{
            "id": msg_id,
            "result": {{
                "thread": {{
                    "id": "vendor-thread-recovered",
                    "preview": "",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 1,
                    "status": {{"type": "idle"}},
                    "path": "/tmp/vendor-thread-recovered.jsonl",
                    "cwd": "/work/repo",
                    "cliVersion": "0.118.0",
                    "source": "app-server",
                    "agentNickname": None,
                    "agentRole": None,
                    "gitInfo": None,
                    "name": None,
                    "turns": [],
                }}
            }},
        }}) + "\\n")
        sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps({{"id": msg_id, "result": {{}}}}) + "\\n")
        sys.stdout.flush()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


def _rollout_state(*, rollout_path: Path) -> CodexSessionRuntimeState:
    return CodexSessionRuntimeState(
        sessionId="sess-1",
        sessionEpoch=1,
        logicalThreadId="logical-thread-1",
        vendorThreadId="vendor-thread-1",
        vendorThreadPath=str(rollout_path),
        containerId="ctr-1",
        activeTurnId="vendor-turn-1",
        lastTurnId="vendor-turn-1",
        lastTurnStatus="running",
    )

def test_app_server_client_ignores_notifications_until_matching_response(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    client = CodexAppServerRpcClient(
        command=("python3", str(script)),
        client_name="MoonMindTest",
        client_version="0.1",
    )

    initialized = client.initialize()

    assert initialized["codexHome"] == "/tmp/fake-codex-home"
    client.close()

def test_runtime_launch_session_persists_logical_thread_mapping(tmp_path: Path) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    handle = runtime.launch_session(request)

    assert handle.status == "ready"
    assert handle.session_state.thread_id == "logical-thread-1"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"
    state_payload = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["logicalThreadId"] == "logical-thread-1"
    assert state_payload["vendorThreadId"] == "vendor-thread-1"
    assert state_payload["vendorThreadPath"] == "/tmp/vendor-thread-1.jsonl"


def test_runtime_state_save_failure_preserves_last_valid_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    state_path = (
        Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    )
    previous_payload = state_path.read_text(encoding="utf-8")
    state = runtime._load_state()
    state.last_control_action = "send_turn"

    def _fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise OSError("simulated atomic replace failure")

    with monkeypatch.context() as replace_failure:
        replace_failure.setattr(os, "replace", _fail_replace)

        with pytest.raises(OSError, match="simulated atomic replace failure"):
            runtime._save_state(state)

    assert state_path.read_text(encoding="utf-8") == previous_payload
    assert (
        CodexSessionRuntimeState.model_validate_json(previous_payload).session_epoch
        == 1
    )
    assert not list(state_path.parent.glob(f"{state_path.name}.*.tmp"))

    handle = runtime.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
        )
    )

    assert handle.status == "ready"
    assert handle.session_state.session_epoch == 2


def test_runtime_clear_session_recovers_sqlite_state_runtime_init_failure(
    tmp_path: Path,
) -> None:
    launch_script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(launch_script)),
    )
    runtime.launch_session(request)
    runtime.close()

    sqlite_path = Path(request.codex_home_path) / "state_1.sqlite"
    connection = sqlite3.connect(sqlite_path)
    try:
        connection.execute("CREATE TABLE state_probe (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    shm_path = sqlite_path.with_name(sqlite_path.name + "-shm")
    shm_path.write_bytes(b"stale-shm")

    retry_script = _write_flaky_sqlite_init_app_server(tmp_path)
    retry_runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(retry_script)),
    )

    try:
        handle = retry_runtime.clear_session(
            CodexManagedSessionClearRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                newThreadId="logical-thread-2",
                reason="retry_after_empty_assistant_output",
            )
        )

        assert handle.status == "ready"
        assert handle.session_state.session_epoch == 2
        assert handle.session_state.thread_id == "logical-thread-2"
        assert handle.metadata["vendorThreadId"] == "vendor-thread-recovered"
        assert not shm_path.exists()
        stderr_text = (Path(request.artifact_spool_path) / "stderr.log").read_text(
            encoding="utf-8"
        )
        assert "codex sqlite state runtime recovery" in stderr_text
    finally:
        retry_runtime.close()

def test_runtime_send_turn_returns_terminal_completed_response(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["assistantText"] == "OK"

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.session_state.active_turn_id is None
    assert handle.metadata["lastAssistantText"] == "OK"


def test_app_server_rpc_trace_summary_includes_redacted_error_message() -> None:
    summary = CodexAppServerRpcClient._message_summary(
        {
            "id": 6,
            "error": {
                "code": -32600,
                "message": "failed to read rollout with token=supersecret",
            },
        }
    )

    assert summary["hasError"] is True
    assert summary["errorCode"] == -32600
    assert summary["errorMessage"] == "failed to read rollout with token=[REDACTED]"
    assert "supersecret" not in summary["errorMessage"]


def test_redaction_preserves_falsy_non_none_values() -> None:
    assert CodexAppServerRpcClient._redact_trace_text(0) == "0"
    assert CodexAppServerRpcClient._redact_trace_text(False) == "False"
    assert CodexManagedSessionRuntime._redact_diagnostic_text(0) == "0"
    assert CodexManagedSessionRuntime._redact_diagnostic_text(False) == "False"


def test_empty_assistant_failure_reason_normalizes_text() -> None:
    assert _is_empty_assistant_failure_reason(
        "  Codex app-server turn/completed produced no assistant output  "
    )
    assert not _is_empty_assistant_failure_reason("provider request failed")


def test_runtime_send_turn_mirrors_rollout_updates_to_stdout_spool(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    rollout_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "15"
        / "rollout-2026-04-15T06-04-58-vendor-thread-1.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text("", encoding="utf-8")
    script = write_fake_app_server(
        tmp_path,
        start_thread_path=str(rollout_path),
        rollout_entries_on_read=[
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": "{}",
                    "call_id": "call-1",
                },
            },
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "live-output-works\n",
                },
            },
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "Streaming update",
                    "phase": "commentary",
                },
            },
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Streaming update"},
                    ],
                    "phase": "commentary",
                },
            },
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    stdout_text = (Path(request.artifact_spool_path) / "stdout.log").read_text(
        encoding="utf-8"
    )
    assert "turn started: vendor-turn-1" in stdout_text
    assert "tool call: exec_command" in stdout_text
    assert "tool output:\nlive-output-works\n" in stdout_text
    assert stdout_text.count("assistant: Streaming update\n") == 1

def test_runtime_rollout_live_mirror_preserves_incomplete_tail(
    tmp_path: Path,
) -> None:
    runtime = _runtime_for_rollout_mirror(tmp_path)
    rollout_path = (
        runtime._codex_home_path
        / "sessions"
        / "2026"
        / "04"
        / "15"
        / "rollout-2026-04-15T06-04-58-vendor-thread-1.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    complete_line = json.dumps(
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
            },
        }
    )
    partial_line = json.dumps(
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "complete after next poll",
            },
        }
    )
    split_at = partial_line.index("complete after")
    rollout_path.write_text(
        complete_line + "\n" + partial_line[:split_at],
        encoding="utf-8",
    )
    mirror = _RolloutLiveMirror(path=str(rollout_path), offset=0)

    runtime._publish_rollout_live_updates(
        state=_rollout_state(rollout_path=rollout_path),
        vendor_turn_id="vendor-turn-1",
        thread_payload={},
        mirror=mirror,
    )

    stdout_path = runtime._artifact_spool_path / "stdout.log"
    assert stdout_path.read_text(encoding="utf-8") == "tool call: exec_command\n"
    assert mirror.offset == len((complete_line + "\n").encode("utf-8"))

    with rollout_path.open("a", encoding="utf-8") as handle:
        handle.write(partial_line[split_at:] + "\n")

    runtime._publish_rollout_live_updates(
        state=_rollout_state(rollout_path=rollout_path),
        vendor_turn_id="vendor-turn-1",
        thread_payload={},
        mirror=mirror,
    )

    assert stdout_path.read_text(encoding="utf-8") == (
        "tool call: exec_command\n"
        "tool output:\ncomplete after next poll\n"
    )
    assert mirror.offset == rollout_path.stat().st_size

def test_runtime_rollout_live_mirror_keeps_repeated_identical_tool_events(
    tmp_path: Path,
) -> None:
    runtime = _runtime_for_rollout_mirror(tmp_path)
    rollout_path = (
        runtime._codex_home_path
        / "sessions"
        / "2026"
        / "04"
        / "15"
        / "rollout-2026-04-15T06-04-58-vendor-thread-1.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    duplicate_call = {
        "timestamp": _iso_timestamp(minutes_offset=0),
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
        },
    }
    rollout_path.write_text(
        json.dumps(duplicate_call) + "\n" + json.dumps(duplicate_call) + "\n",
        encoding="utf-8",
    )
    mirror = _RolloutLiveMirror(path=str(rollout_path), offset=0)

    runtime._publish_rollout_live_updates(
        state=_rollout_state(rollout_path=rollout_path),
        vendor_turn_id="vendor-turn-1",
        thread_payload={},
        mirror=mirror,
    )

    stdout_text = (runtime._artifact_spool_path / "stdout.log").read_text(
        encoding="utf-8"
    )
    assert stdout_text == "tool call: exec_command\ntool call: exec_command\n"
    assert mirror.offset == rollout_path.stat().st_size

    runtime._publish_rollout_live_updates(
        state=_rollout_state(rollout_path=rollout_path),
        vendor_turn_id="vendor-turn-1",
        thread_payload={},
        mirror=mirror,
    )

    assert (runtime._artifact_spool_path / "stdout.log").read_text(
        encoding="utf-8"
    ) == stdout_text


def test_runtime_live_mirror_caches_only_active_turn_assistant_text(
    tmp_path: Path,
) -> None:
    runtime = _runtime_for_rollout_mirror(tmp_path)
    rollout_path = (
        runtime._codex_home_path
        / "sessions"
        / "2026"
        / "07"
        / "10"
        / "rollout-2026-07-10T18-34-47-vendor-thread-1.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    entries = [
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "Delayed previous-turn message",
            },
        },
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "vendor-turn-1",
            },
        },
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "Active-turn message",
            },
        },
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "vendor-turn-1",
            },
        },
        {
            "timestamp": _iso_timestamp(minutes_offset=0),
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "Delayed message after completion",
            },
        },
    ]
    rollout_path.write_text(
        "".join(f"{json.dumps(entry)}\n" for entry in entries),
        encoding="utf-8",
    )
    mirror = _RolloutLiveMirror(
        path=str(rollout_path),
        offset=0,
        turn_started_at=time.time(),
    )

    runtime._publish_rollout_live_updates(
        state=_rollout_state(rollout_path=rollout_path),
        vendor_turn_id="vendor-turn-1",
        thread_payload={},
        mirror=mirror,
    )

    assert mirror.last_assistant_text == "Active-turn message"
    assert mirror.last_assistant_text_matches_active_turn is True
    assert mirror.inside_active_turn is False


def test_runtime_session_status_fails_when_completed_turn_has_no_assistant_output(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path, assistant_text="")
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert (
        response.metadata["reason"]
        == "codex app-server turn/completed produced no assistant output"
    )
    assert response.metadata["failureCause"] == "app_server_protocol_empty_turn"
    assert response.metadata["retryRecommendedAction"] == "clear_session"
    evidence = response.metadata["turnFailureEvidence"]
    assert evidence["failureCause"] == "app_server_protocol_empty_turn"
    assert evidence["session"]["vendorTurnId"] == response.turn_id
    assert evidence["threadPayloadSummary"]["activeTurn"]["found"] is True
    assert any(
        entry["direction"] == "request" and entry["method"] == "turn/start"
        for entry in evidence["appServerRpcTrace"]
    )

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert (
        handle.metadata["lastTurnError"]
        == "codex app-server turn/completed produced no assistant output"
    )

    state_payload = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload.get("activeTurnId") is None
    assert (
        state_payload.get("lastTurnError")
        == "codex app-server turn/completed produced no assistant output"
    )
    stderr_path = Path(request.artifact_spool_path) / "stderr.log"
    assert stderr_path.exists()
    assert "turn failed:" in stderr_path.read_text(encoding="utf-8")


def test_runtime_send_turn_reads_completed_assistant_items_from_item_list(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        items_list_assistant_text="OK",
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata["assistantText"] == "OK"
    assert "turnFailureEvidence" not in response.metadata

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.metadata["lastAssistantText"] == "OK"
    assert handle.metadata["lastTurnStatus"] == "completed"


def test_runtime_send_turn_accepts_item_completed_notification_contract(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        completion_notification_method="item/completed",
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata["assistantText"] == "OK"

def test_runtime_send_turn_completes_via_thread_read_without_notification(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        completion_notification_method=None,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["assistantText"] == "OK"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.session_state.active_turn_id is None
    assert handle.metadata["lastAssistantText"] == "OK"

def test_runtime_send_turn_completes_when_thread_read_omits_turns(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T07:21:32.088Z",
                "type": "response_item",
                "turnId": "vendor-turn-1",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Ignore this echoed user input",
                        },
                        {
                            "type": "output_text",
                            "text": "Recovered from rollout transcript",
                        }
                    ],
                    "phase": "final",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["assistantText"] == "Recovered from rollout transcript"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.metadata["lastAssistantText"] == "Recovered from rollout transcript"

def test_runtime_send_turn_recovers_last_agent_message_from_task_complete_event(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-10T17:55:16.922Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": "vendor-turn-1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-10T17:57:55.661Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "vendor-turn-1",
                            "last_agent_message": "Recovered from task_complete event",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["assistantText"] == "Recovered from task_complete event"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.metadata["lastAssistantText"] == "Recovered from task_complete event"

def test_runtime_send_turn_fails_empty_task_complete_event(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-04-10T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            }
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["failureClass"] == "transient"
    assert response.metadata["reason"] == (
        "codex app-server turn/completed produced no assistant output"
    )
    assert response.metadata["retryRecommendedAction"] == "clear_session"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert handle.metadata["failureClass"] == "transient"
    assert handle.metadata["lastTurnError"] == (
        "codex app-server turn/completed produced no assistant output"
    )
    assert "lastAssistantText" not in handle.metadata


def test_runtime_send_turn_preserves_live_assistant_output_outside_scan_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime."
        "_ROLLOUT_RECOVERY_MAX_BYTES",
        1024,
    )
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "07"
        / "10"
        / "rollout-2026-07-10T18-34-47-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "turn_id": "vendor-turn-1",
                    "message": "Implemented and verified the requested change.",
                },
            },
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "turn_id": "vendor-turn-1",
                    "output": "x" * 2048,
                },
            },
            {
                "timestamp": _iso_timestamp(minutes_offset=0),
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            },
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    monkeypatch.setattr(
        runtime,
        "_new_rollout_live_mirror",
        lambda _state: _RolloutLiveMirror(
            path=str(transcript_path),
            offset=transcript_path.stat().st_size,
            last_assistant_text=(
                "Implemented and verified the requested change."
            ),
            last_assistant_text_matches_active_turn=True,
        ),
    )

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed", response.model_dump(
        by_alias=True, mode="json"
    )
    assert response.metadata["assistantText"] == (
        "Implemented and verified the requested change."
    )
    assert "failureCause" not in response.metadata


def test_runtime_send_turn_allows_zero_add_on_credits_with_included_usage_remaining(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="Completed with included plan usage.",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-04-10T17:57:54.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "vendor-turn-1",
                },
            },
            {
                "timestamp": "2026-04-10T17:57:55.100Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {
                            "used_percent": 0.0,
                            "window_minutes": 10080,
                        },
                        "credits": {
                            "has_credits": False,
                            "unlimited": False,
                            "balance": "0",
                        },
                        "plan_type": "pro",
                        "rate_limit_reached_type": None,
                    },
                },
            },
            {
                "timestamp": "2026-04-10T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            },
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata["assistantText"] == "Completed with included plan usage."
    assert "failureClass" not in response.metadata
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert "failureClass" not in handle.metadata
    assert "lastTurnError" not in handle.metadata


def test_runtime_send_turn_classifies_exhausted_included_usage_window(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-04-10T17:57:54.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "vendor-turn-1",
                },
            },
            {
                "timestamp": "2026-04-10T17:57:55.100Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "codex",
                        "primary": {"used_percent": 100.0},
                        "credits": {
                            "has_credits": False,
                            "unlimited": False,
                            "balance": "0",
                        },
                        "plan_type": "pro",
                        "rate_limit_reached_type": None,
                    },
                },
            },
            {
                "timestamp": "2026-04-10T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            },
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.turn_id == "vendor-turn-1"
    assert response.metadata == {
        "reason": _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON,
        "failureClass": "integration_error",
    }
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.metadata["failureClass"] == "integration_error"
    assert (
        handle.metadata["lastTurnError"]
        == _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON
    )
    assert "retryRecommendedAction" not in response.metadata


def test_runtime_provider_failure_reason_honors_explicit_reached_type() -> None:
    reason = CodexManagedSessionRuntime._provider_failure_reason_from_rollout_event(
        {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": 99.0},
                "credits": None,
                "rate_limit_reached_type": "primary",
            },
        }
    )

    assert reason == _CODEX_PROVIDER_USAGE_LIMIT_REACHED_REASON


def test_runtime_send_turn_recovers_usage_limit_from_recent_log_for_empty_task_complete(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-04-10T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            }
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    quota_summary = (
        "You've hit your usage limit. To get more access now, send a request "
        "to your admin or try again at May 13th, 2026 1:35 AM."
    )
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        entries=[
            (
                int(time.time()) + 1,
                "session_loop{thread_id=vendor-thread-1}:run_turn: "
                f"Turn error: {quota_summary}",
            )
        ],
    )

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata == {
        "failureClass": "permanent",
        "reason": quota_summary,
    }


def test_runtime_send_turn_recovers_auth_failure_from_recent_log_for_empty_task_complete(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "07"
        / "13"
        / "rollout-2026-07-13T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-07-13T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            }
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    auth_summary = (
        "Your access token could not be refreshed because your refresh token "
        "was already used. Please log out and sign in again."
    )
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        entries=[
            (
                int(time.time()) + 1,
                "model_client:auth: Failed to refresh token: " + auth_summary,
            )
        ],
    )

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata == {
        "failureClass": "permanent",
        "reason": auth_summary,
    }


def test_runtime_send_turn_waits_for_auth_log_after_system_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "07"
        / "13"
        / "rollout-2026-07-13T16-58-45-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        omit_turns_on_read=True,
        thread_status_type="systemError",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-07-13T16:58:47.056Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            }
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    auth_summary = (
        "Your access token could not be refreshed because your refresh token "
        "was already used. Please log out and sign in again."
    )
    recovery_attempts = iter((None, auth_summary))
    monkeypatch.setattr(
        runtime,
        "_extract_turn_error_from_logs",
        lambda *_args, **_kwargs: next(recovery_attempts),
    )

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata == {
        "failureClass": "permanent",
        "reason": auth_summary,
    }
    assert "retryRecommendedAction" not in response.metadata


def test_runtime_send_turn_honors_system_error_with_visible_in_progress_turn(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        completion_notification_method=None,
        complete_turn_on_read=False,
        thread_status_type="systemError",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    auth_summary = (
        "Your access token could not be refreshed because your refresh token "
        "was already used. Please log out and sign in again."
    )
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        entries=[
            (
                int(time.time()) + 1,
                "model_client:auth: Failed to refresh token: " + auth_summary,
            )
        ],
    )

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata == {
        "failureClass": "permanent",
        "reason": auth_summary,
    }


def test_system_error_thread_status_is_terminal_failure() -> None:
    outcome = CodexManagedSessionRuntime._terminal_thread_outcome(
        {"thread": {"status": {"type": "systemError"}}}
    )

    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.error_text == "systemerror"
    assert outcome.failure_class == "permanent"


def _spool_skill_outcome_path(request: LaunchCodexManagedSessionRequest) -> Path:
    return Path(request.artifact_spool_path) / "skill_outcome.json"

def test_runtime_no_op_signal_upgrades_empty_turn_to_completed(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload={
            "schema_version": 1,
            "status": "no_op",
            "reason": "no_open_prs_matched",
            "evidence": {"requested": 0},
        },
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.metadata["disposition"] == "no_op"
    assert response.metadata["reason"] == "no_open_prs_matched"
    assert "failureClass" not in response.metadata

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert handle.metadata["lastTurnStatus"] == "completed"
    assert handle.metadata["disposition"] == "no_op"

def test_runtime_no_op_signal_ignored_when_assistant_text_present(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="all done",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload={
            "schema_version": 1,
            "status": "no_op",
            "reason": "ignored",
        },
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert "disposition" not in response.metadata


@pytest.mark.parametrize("declared_status", ["failed", "partial"])
def test_runtime_skill_failure_signal_overrides_assistant_text(
    tmp_path: Path,
    declared_status: str,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="Queued 0 workflows after API validation errors.",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload={
            "schema_version": 1,
            "status": declared_status,
            "reason": "child_workflow_queue_failed",
            "evidence": {"requested": 2, "created": 0},
        },
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "permanent"
    assert response.metadata["reason"] == "child_workflow_queue_failed"


def test_runtime_caches_valid_skill_outcome_for_current_turn(tmp_path: Path) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )
    turn_started_at = time.time()
    outcome_path = _spool_skill_outcome_path(request)
    outcome_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "failed",
                "reason": "child_workflow_queue_failed",
            }
        ),
        encoding="utf-8",
    )

    first = runtime._read_skill_outcome(turn_started_at=turn_started_at)
    outcome_path.unlink()
    second = runtime._read_skill_outcome(turn_started_at=turn_started_at)

    assert first is not None
    assert second == first

def test_runtime_no_op_signal_ignored_when_schema_version_wrong(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload={
            "schema_version": 99,
            "status": "no_op",
            "reason": "wrong version",
        },
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"

def test_runtime_no_op_signal_ignored_when_malformed_json(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload="{not valid json",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"

def test_runtime_no_op_signal_ignored_when_status_not_no_op(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        skill_outcome_path=_spool_skill_outcome_path(request),
        skill_outcome_payload={
            "schema_version": 1,
            "status": "success",
            "reason": "ignored",
        },
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"

def test_runtime_stale_no_op_marker_from_prior_turn_does_not_upgrade_empty_turn(
    tmp_path: Path,
) -> None:
    """A skill_outcome.json left in the spool by an earlier turn MUST NOT
    upgrade a later empty turn to ``completed``. The runtime resets the
    marker at every send_turn so cross-turn pollution cannot mask a real
    transient failure."""
    request = launch_request(tmp_path)
    # Stale marker present from a prior turn (no skill writes during this run).
    stale_path = _spool_skill_outcome_path(request)
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "no_op",
                "reason": "stale_from_prior_turn",
            }
        ),
        encoding="utf-8",
    )
    # Make the stale marker easy to detect by date even on coarse filesystems:
    # backdate it well outside the freshness skew window.
    stale_mtime = stale_path.stat().st_mtime - 600.0
    os.utime(stale_path, (stale_mtime, stale_mtime))

    script = write_fake_app_server(tmp_path, assistant_text="")
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert "disposition" not in response.metadata
    # send_turn cleared the stale marker before the turn body executed.
    assert not stale_path.exists()

def test_runtime_send_turn_fails_empty_task_complete_with_structured_error(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    script = write_fake_app_server(
        tmp_path,
        assistant_text="",
        start_thread_path=str(transcript_path),
        rollout_entries_on_read=[
            {
                "timestamp": "2026-04-10T17:57:55.661Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "error": {"message": "provider returned structured failure"},
                },
            }
        ],
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["reason"] == "provider returned structured failure"

def test_runtime_send_turn_stays_running_when_rollout_turn_has_not_completed(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T17:55:16.922Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "vendor-turn-1",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "running"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id == "vendor-turn-1"
    assert response.metadata == {}
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "busy"
    assert handle.metadata["lastTurnStatus"] == "running"

def test_runtime_session_status_fails_empty_task_complete_after_running_turn(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T17:55:16.922Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "vendor-turn-1",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )
    assert response.status == "running"
    assert response.session_state.active_turn_id == "vendor-turn-1"
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": "2026-04-10T17:57:55.661Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "vendor-turn-1",
                        "last_agent_message": None,
                    },
                }
            )
            + "\n"
        )

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )

    assert handle.status == "failed"
    assert handle.session_state.active_turn_id is None
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert handle.metadata["failureClass"] == "transient"
    assert handle.metadata["lastTurnError"] == (
        "codex app-server turn/completed produced no assistant output"
    )
    assert "lastAssistantText" not in handle.metadata

def test_runtime_send_turn_stays_running_when_large_rollout_tail_has_active_turn(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        ("x" * (_ROLLOUT_RECOVERY_MAX_BYTES + 16))
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-04-10T17:55:16.922Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "vendor-turn-1",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "running"
    assert response.session_state.active_turn_id == "vendor-turn-1"

def test_runtime_send_turn_ignores_transient_log_error_when_rollout_has_final_answer(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T17-55-14-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-10T17:55:16.922Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": "vendor-turn-1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-10T17:57:55.661Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "vendor-turn-1",
                            "last_agent_message": "Recovered after retry",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        entries=[
            (
                "turn{turn.id=vendor-turn-1 model=qwen/qwen3.6-plus}: "
                'event.name="codex.api_request" '
                'http.response.status_code=503 error.message="transient upstream error"'
            )
        ],
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.metadata["assistantText"] == "Recovered after retry"
    assert "reason" not in response.metadata

def test_runtime_send_turn_recovers_terminal_rollout_without_turn_reference(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    future_timestamp = _iso_timestamp(minutes_offset=5)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "timestamp": future_timestamp,
                        "type": "event_msg",
                        "payload": {
                            "type": "task_started",
                            "turn_id": "codex-turn-1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": future_timestamp,
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": "Recovered final answer without vendor turn id",
                            "phase": "final",
                            "memory_citation": None,
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": future_timestamp,
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Recovered final answer without vendor turn id",
                                }
                            ],
                            "phase": "final",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": future_timestamp,
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "codex-turn-1",
                            "last_agent_message": (
                                "Recovered final answer without vendor turn id"
                            ),
                        },
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    assert response.turn_id == "vendor-turn-1"
    assert (
        response.metadata["assistantText"]
        == "Recovered final answer without vendor turn id"
    )
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    assert (
        handle.metadata["lastAssistantText"]
        == "Recovered final answer without vendor turn id"
    )

def test_runtime_send_turn_ignores_stale_terminal_rollout_without_turn_reference(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    past_timestamp = _iso_timestamp(minutes_offset=-5)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "timestamp": past_timestamp,
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": "Stale final answer from a previous turn",
                            "phase": "final",
                            "memory_citation": None,
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": past_timestamp,
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Stale final answer from a previous turn",
                                }
                            ],
                            "phase": "final",
                        },
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert (
        response.metadata["reason"]
        == "codex app-server turn/completed produced no assistant output"
    )
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert handle.metadata["failureClass"] == "transient"

def test_runtime_send_turn_fails_when_rollout_only_has_other_turn_output(
    tmp_path: Path,
) -> None:
    past_timestamp = _iso_timestamp(minutes_offset=-5)
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": past_timestamp,
                "type": "response_item",
                "turnId": "vendor-turn-0",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Stale text from a previous turn",
                        }
                    ],
                    "phase": "final_answer",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert (
        response.metadata["reason"]
        == "codex app-server turn/completed produced no assistant output"
    )
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.metadata["lastTurnStatus"] == "failed"

def test_runtime_send_turn_ignores_rollout_paths_outside_codex_sessions(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = tmp_path / "vendor-thread-1.jsonl"
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T07:21:32.088Z",
                "type": "response_item",
                "turnId": "vendor-turn-1",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Do not read arbitrary rollout files",
                        }
                    ],
                    "phase": "final",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert (
        response.metadata["reason"]
        == "codex app-server turn/completed produced no assistant output"
    )

def test_runtime_send_turn_fails_from_rollout_completion_when_thread_read_is_not_loaded(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T07:21:32.860Z",
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "vendor-turn-1",
                    "last_agent_message": None,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        entries=[
            (
                "session_loop{thread_id=vendor-thread-1}:turn{turn.id=vendor-turn-1}: "
                "Turn error: unexpected status 404 Not Found: The free model has been "
                "deprecated. Transition to qwen/qwen3.6-plus for continued paid access."
            )
        ],
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
        thread_status_type="notLoaded",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    expected_reason = (
        "unexpected status 404 Not Found: The free model has been deprecated. "
        "Transition to qwen/qwen3.6-plus for continued paid access."
    )
    assert response.status == "failed"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id is None
    assert response.metadata["reason"] == expected_reason
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "failed"
    assert handle.session_state.active_turn_id is None
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert handle.metadata["lastTurnError"] == expected_reason

def test_runtime_send_turn_prefers_rollout_task_complete_failure_over_agent_message(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        (
            json.dumps(
                {
                    "timestamp": "2026-04-10T07:21:32.088Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "turn_id": "vendor-turn-1",
                        "message": "Interim text that should not mask a failure",
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "timestamp": "2026-04-10T07:21:33.088Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "vendor-turn-1",
                        "error": "provider returned exit code 1",
                    },
                }
            )
            + "\n"
        ),
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
        thread_status_type="notLoaded",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["reason"] == "provider returned exit code 1"

def test_runtime_send_turn_prefers_failed_thread_status_over_rollout_success(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T07:21:32.088Z",
                "type": "response_item",
                "turnId": "vendor-turn-1",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Do not hide the thread failure",
                        }
                    ],
                    "phase": "final",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        start_thread_path=str(transcript_path),
        thread_status_type="failed",
        thread_status_reason="provider failed the thread",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["reason"] == "provider failed the thread"

def test_runtime_extract_turn_error_from_logs_prefers_highest_numeric_shard(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        filename="logs_9.sqlite",
        entries=[
            (
                "session_loop{thread_id=vendor-thread-1}:turn{turn.id=vendor-turn-1}: "
                "Turn error: stale shard"
            )
        ],
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        filename="logs_10.sqlite",
        entries=[
            (
                "session_loop{thread_id=vendor-thread-1}:turn{turn.id=vendor-turn-1}: "
                "Turn error: latest shard"
            )
        ],
    )

    assert runtime._extract_turn_error_from_logs("vendor-turn-1") == "latest shard"

def test_runtime_recent_log_excerpts_only_include_active_turn(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        entries=[
            (
                "session_loop{thread_id=vendor-thread-1}:turn{turn.id=other-turn}: "
                "Turn error: unrelated prior failure"
            ),
            (
                "session_loop{thread_id=vendor-thread-1}:turn{turn.id=vendor-turn-1}: "
                "Turn error: active failure"
            ),
        ],
    )

    excerpts = runtime._recent_runtime_log_excerpts(vendor_turn_id="vendor-turn-1")

    assert [entry["text"] for entry in excerpts] == [
        "session_loop{thread_id=vendor-thread-1}:turn{turn.id=vendor-turn-1}: "
        "Turn error: active failure"
    ]

def test_runtime_extract_turn_error_from_logs_recovers_provider_error_message(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    _write_fake_codex_logs(
        request.codex_home_path,
        entries=[
            (
                "turn{turn.id=vendor-turn-1 model=qwen/qwen3.6-plus}: "
                'event.name="codex.api_request" '
                'http.response.status_code=404 error.message="http 404"'
            )
        ],
    )

    assert runtime._extract_turn_error_from_logs("vendor-turn-1") == "http 404"

def test_runtime_extract_turn_error_from_logs_recovers_recent_provider_error_without_turn_id(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    turn_started_at = int(time.time())
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        entries=[
            (
                turn_started_at - 60,
                "Turn error: You've hit your usage limit from an older turn.",
            ),
            (
                turn_started_at,
                "Received message "
                '{"type":"error","error":{"type":"usage_limit_reached",'
                '"message":"The usage limit has been reached"},'
                '"status_code":429}',
            ),
        ],
    )

    assert (
        runtime._extract_turn_error_from_logs(
            "vendor-turn-without-log-row",
            turn_started_at=turn_started_at,
        )
        == "The usage limit has been reached (status 429)"
    )

def test_runtime_extract_turn_error_from_logs_ignores_provider_error_before_turn_start(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    turn_started_at = int(time.time())
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        entries=[
            (
                turn_started_at - 1,
                "Turn error: You've hit your usage limit from a previous turn.",
            ),
        ],
    )

    assert (
        runtime._extract_turn_error_from_logs(
            "vendor-turn-without-log-row",
            turn_started_at=turn_started_at,
        )
        is None
    )

def test_runtime_extract_turn_error_from_logs_applies_global_provider_row_limit(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", "-c", "raise SystemExit(0)"),
    )
    turn_started_at = int(time.time())
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        filename="logs_10.sqlite",
        entries=[
            (turn_started_at, "provider marker 429 without parseable error")
            for _ in range(200)
        ],
    )
    _write_fake_codex_logs_with_timestamps(
        request.codex_home_path,
        filename="logs_9.sqlite",
        entries=[
            (
                turn_started_at,
                "Turn error: You've hit your usage limit in an older shard.",
            ),
        ],
    )

    assert (
        runtime._extract_turn_error_from_logs(
            "vendor-turn-without-log-row",
            turn_started_at=turn_started_at,
        )
        is None
    )

@pytest.mark.parametrize(
    ("thread_status_type", "thread_status_reason", "expected_status", "expected_reason"),
    [
        ("failed", "thread failure", "failed", "thread failure"),
        ("interrupted", "operator stopped", "interrupted", "operator stopped"),
        ("cancelled", None, "interrupted", "cancelled"),
    ],
)
def test_runtime_send_turn_uses_terminal_thread_status_when_turn_missing(
    tmp_path: Path,
    thread_status_type: str,
    thread_status_reason: str | None,
    expected_status: str,
    expected_reason: str,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        completion_notification_method=None,
        complete_turn_on_read=False,
        omit_turns_on_read=True,
        omit_turns_when_incomplete=True,
        thread_status_type=thread_status_type,
        thread_status_reason=thread_status_reason,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == expected_status
    assert response.metadata["reason"] == expected_reason

def test_runtime_send_turn_recovers_vendor_thread_path_from_sessions_dir(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload.pop("vendorThreadPath", None)
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    recovered_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "08"
        / "rollout-2026-04-08T00-00-00-vendor-thread-1.jsonl"
    )
    recovered_path.parent.mkdir(parents=True, exist_ok=True)
    recovered_path.write_text("", encoding="utf-8")

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadPath"] == str(recovered_path)

def test_runtime_send_turn_falls_back_to_new_thread_when_recovery_fails(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path, fail_thread_resume=True)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    updated_state = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert updated_state["vendorThreadId"] == "vendor-thread-1"

def test_runtime_send_turn_drops_stale_vendor_thread_path_when_fallback_starts_new_thread(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        fail_thread_resume=True,
        start_thread_id="vendor-thread-2",
        start_thread_path=None,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    updated_state = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert updated_state["vendorThreadId"] == "vendor-thread-2"
    assert "vendorThreadPath" not in updated_state


def test_runtime_send_turn_waits_for_fallback_started_turn_visibility(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "06"
        / "28"
        / "rollout-2026-06-28T13-46-25-vendor-thread-2.jsonl"
    )
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")
    script = write_fake_app_server(
        tmp_path,
        fail_thread_resume=True,
        start_thread_id="vendor-thread-2",
        start_thread_path=str(transcript_path),
        omit_turns_on_initial_reads=1,
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        missing_turn_visibility_grace_seconds=1.0,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
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
    state_payload = json.loads(
        (
            Path(request.session_workspace_path)
            / ".moonmind-codex-session-state.json"
        ).read_text(encoding="utf-8")
    )
    assert state_payload["vendorThreadId"] == "vendor-thread-2"
    assert state_payload["lastTurnStatus"] == "completed"
    stderr_path = Path(request.artifact_spool_path) / "stderr.log"
    assert not stderr_path.exists() or "turn failed:" not in stderr_path.read_text(
        encoding="utf-8"
    )


def test_runtime_send_turn_marks_missing_fallback_turn_subtype_after_grace(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "06"
        / "28"
        / "rollout-2026-06-28T13-46-25-vendor-thread-2.jsonl"
    )
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")
    script = write_fake_app_server(
        tmp_path,
        fail_thread_resume=True,
        start_thread_id="vendor-thread-2",
        start_thread_path=str(transcript_path),
        omit_turns_on_read=True,
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        missing_turn_visibility_grace_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureCause"] == "app_server_protocol_empty_turn"
    assert (
        response.metadata["failureSubtype"]
        == "missing_turn_idle_empty_rollout"
    )
    evidence = response.metadata["turnFailureEvidence"]
    assert evidence["threadPayloadSummary"]["turnCount"] == 0
    assert evidence["rolloutScan"]["entriesScanned"] == 0


def test_runtime_send_turn_starts_new_thread_when_rollout_recovery_file_is_empty(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    empty_rollout = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "05"
        / "18"
        / "rollout-2026-05-18T23-33-21-vendor-thread-1.jsonl"
    )
    empty_rollout.parent.mkdir(parents=True)
    empty_rollout.write_text("", encoding="utf-8")
    script = write_fake_app_server(
        tmp_path,
        fail_thread_resume=True,
        thread_recovery_error_message=(
            "failed to read thread: thread-store internal error: failed to read "
            f"thread {empty_rollout}: rollout at {empty_rollout} is empty"
        ),
        start_thread_id="vendor-thread-2",
        start_thread_path=None,
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["vendorThreadPath"] = str(empty_rollout)
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadId"] == "vendor-thread-2"
    assert "vendorThreadPath" not in updated_state

def test_runtime_send_turn_retries_transient_empty_rollout_thread_read(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    empty_rollout = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "05"
        / "18"
        / "rollout-2026-05-18T23-33-21-vendor-thread-1.jsonl"
    )
    empty_rollout.parent.mkdir(parents=True)
    empty_rollout.write_text("", encoding="utf-8")
    thread_read_error = (
        "failed to read thread: thread-store internal error: failed to read "
        f"thread {empty_rollout}: rollout at {empty_rollout} is empty"
    )
    script = write_fake_app_server(
        tmp_path,
        fail_thread_read=True,
        thread_read_fail_limit=1,
        thread_read_fail_after_attempts=1,
        thread_read_error_message=thread_read_error,
        start_thread_path=str(empty_rollout),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
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


def test_runtime_send_turn_stays_running_when_empty_rollout_retry_times_out(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    empty_rollout = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "05"
        / "18"
        / "rollout-2026-05-18T23-33-21-vendor-thread-1.jsonl"
    )
    empty_rollout.parent.mkdir(parents=True)
    empty_rollout.write_text("", encoding="utf-8")
    thread_read_error = (
        "failed to read thread: thread-store internal error: failed to read "
        f"thread {empty_rollout}: rollout at {empty_rollout} is empty"
    )
    script = write_fake_app_server(
        tmp_path,
        fail_thread_read=True,
        thread_read_fail_limit=None,
        thread_read_fail_after_attempts=0,
        thread_read_error_message=thread_read_error,
        start_thread_path=str(empty_rollout),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "running"
    assert response.turn_id == "vendor-turn-1"
    assert response.session_state.active_turn_id == "vendor-turn-1"
    assert response.metadata == {}


def test_runtime_send_turn_classifies_empty_rollout_thread_read_as_recoverable(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    empty_rollout = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "05"
        / "18"
        / "rollout-2026-05-18T23-33-21-vendor-thread-1.jsonl"
    )
    empty_rollout.parent.mkdir(parents=True)
    empty_rollout.write_text("", encoding="utf-8")
    thread_read_error = (
        "failed to read thread: thread-store internal error: failed to read "
        f"thread {empty_rollout}: rollout at {empty_rollout} is empty"
    )
    script = write_fake_app_server(
        tmp_path,
        fail_thread_read=True,
        thread_read_fail_limit=None,
        thread_read_fail_after_attempts=1,
        thread_read_error_message=thread_read_error,
        start_thread_path=str(empty_rollout),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "failed"
    assert response.metadata["failureClass"] == "transient"
    assert response.metadata["failureCause"] == "app_server_protocol_empty_turn"
    assert response.metadata["retryRecommendedAction"] == "clear_session"
    assert f"rollout at {empty_rollout} is empty" in response.metadata["reason"]
    evidence = response.metadata["turnFailureEvidence"]
    assert evidence["failureCause"] == "app_server_protocol_empty_turn"
    assert evidence["session"]["vendorTurnId"] == response.turn_id
    assert evidence["threadPayloadSummary"] is None
    assert evidence["rolloutScan"]["rolloutPath"] == str(empty_rollout)
    assert evidence["rolloutScan"]["entriesScanned"] == 0
    assert any(
        entry["direction"] == "request" and entry["method"] == "thread/read"
        for entry in evidence["appServerRpcTrace"]
    )

    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["lastFailureClass"] == "transient"


def test_runtime_send_turn_starts_new_thread_when_resumed_rollout_read_is_empty(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    empty_rollout = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "06"
        / "02"
        / "rollout-2026-06-02T23-39-56-vendor-thread-1.jsonl"
    )
    empty_rollout.parent.mkdir(parents=True)
    empty_rollout.write_text("", encoding="utf-8")
    script = write_fake_app_server(
        tmp_path,
        fail_thread_read=True,
        thread_read_error_message=(
            "failed to read thread: thread-store internal error: failed to read "
            f"thread {empty_rollout}: rollout at {empty_rollout} is empty"
        ),
        start_thread_path=str(empty_rollout),
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
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


def test_runtime_send_turn_refreshes_resumed_thread_payload_from_read(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    stale_rollout = tmp_path / "stale" / "vendor-thread-1.jsonl"
    stale_rollout.parent.mkdir(parents=True)
    stale_rollout.write_text("", encoding="utf-8")
    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["vendorThreadPath"] = str(stale_rollout)
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadPath"] == "/tmp/vendor-thread-1.jsonl"


def test_runtime_send_turn_ignores_nonexistent_vendor_thread_path_from_state(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        resume_requires_existing_rollout_path=True,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["vendorThreadPath"] = "/tmp/vendor-thread-1.jsonl"
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )

    assert response.status == "completed"
    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "ready"
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadId"] == "vendor-thread-1"
    assert updated_state["vendorThreadPath"] == "/tmp/vendor-thread-1.jsonl"

def test_runtime_clear_session_rotates_logical_thread_and_epoch(tmp_path: Path) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)

    handle = runtime.clear_session(
        CodexManagedSessionClearRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            newThreadId="logical-thread-2",
        )
    )

    assert handle.status == "ready"
    assert handle.session_state.session_epoch == 2
    assert handle.session_state.thread_id == "logical-thread-2"
    assert handle.metadata["vendorThreadId"] == "vendor-thread-1"

def test_runtime_session_status_remains_busy_without_completion_notification(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(
        tmp_path,
        completion_notification_method=None,
        complete_turn_on_read=False,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    response = runtime.send_turn(
        SendCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            instructions="Reply with exactly the word OK",
        )
    )
    assert response.status == "running"

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )
    assert handle.status == "busy"

    state_payload = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["activeTurnId"] == "vendor-turn-1"

def test_runtime_session_status_prefers_failed_thread_status_over_rollout_success(
    tmp_path: Path,
) -> None:
    request = launch_request(tmp_path)
    transcript_path = (
        Path(request.codex_home_path)
        / "sessions"
        / "2026"
        / "04"
        / "10"
        / "rollout-2026-04-10T07-21-32-vendor-thread-1.jsonl"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-10T07:21:32.088Z",
                "type": "response_item",
                "turnId": "vendor-turn-1",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Do not hide the refreshed thread failure",
                        }
                    ],
                    "phase": "final",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    script = write_fake_app_server(
        tmp_path,
        omit_turns_on_read=True,
        omit_turns_when_incomplete=True,
        start_thread_path=str(transcript_path),
        thread_status_type="failed",
        thread_status_reason="refresh saw a provider failure",
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["activeTurnId"] = "vendor-turn-1"
    state_payload["lastTurnId"] = "vendor-turn-1"
    state_payload["lastTurnStatus"] = "running"
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    handle = runtime.session_status(
        CodexManagedSessionLocator(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
        )
    )

    assert handle.status == "failed"
    assert handle.session_state.active_turn_id is None
    assert handle.metadata["lastTurnStatus"] == "failed"
    assert handle.metadata["lastTurnError"] == "refresh saw a provider failure"

def test_runtime_interrupt_turn_uses_app_server_transport(tmp_path: Path) -> None:
    interrupt_record_path = tmp_path / "interrupt.json"
    script = write_fake_app_server(
        tmp_path,
        interrupt_record_path=interrupt_record_path,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["activeTurnId"] = "vendor-turn-1"
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    response = runtime.interrupt_turn(
        InterruptCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            turnId="vendor-turn-1",
            reason="operator requested interrupt",
        )
    )

    assert response.status == "interrupted"
    assert json.loads(interrupt_record_path.read_text(encoding="utf-8")) == {
        "threadId": "vendor-thread-1",
        "turnId": "vendor-turn-1",
        "reason": "operator requested interrupt",
    }
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state.get("activeTurnId") is None

def test_runtime_steer_turn_uses_app_server_transport(tmp_path: Path) -> None:
    steer_record_path = tmp_path / "steer.json"
    script = write_fake_app_server(
        tmp_path,
        steer_record_path=steer_record_path,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["activeTurnId"] = "vendor-turn-1"
    state_payload["lastTurnId"] = "vendor-turn-1"
    state_payload["lastTurnStatus"] = "running"
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    response = runtime.steer_turn(
        SteerCodexManagedSessionTurnRequest(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="ctr-1",
            threadId="logical-thread-1",
            turnId="vendor-turn-1",
            instructions="Prefer the simpler implementation.",
            metadata={"reason": "operator steer"},
        )
    )

    assert response.status == "running"
    assert response.turn_id == "vendor-turn-1"
    assert json.loads(steer_record_path.read_text(encoding="utf-8")) == {
        "threadId": "vendor-thread-1",
        "turnId": "vendor-turn-1",
        "input": [
            {
                "type": "text",
                "text": "Prefer the simpler implementation.",
            }
        ],
        "metadata": {"reason": "operator steer"},
    }
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state.get("activeTurnId") == "vendor-turn-1"
    assert updated_state["lastControlAction"] == "steer_turn"

def test_runtime_steer_turn_tolerates_null_metadata(tmp_path: Path) -> None:
    steer_record_path = tmp_path / "steer.json"
    script = write_fake_app_server(
        tmp_path,
        steer_record_path=steer_record_path,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )
    runtime.launch_session(request)
    state_path = Path(request.session_workspace_path) / ".moonmind-codex-session-state.json"
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["activeTurnId"] = "vendor-turn-1"
    state_payload["lastTurnId"] = "vendor-turn-1"
    state_payload["lastTurnStatus"] = "running"
    state_path.write_text(json.dumps(state_payload) + "\n", encoding="utf-8")

    steer_request = SteerCodexManagedSessionTurnRequest.model_construct(
        session_id="sess-1",
        session_epoch=1,
        container_id="ctr-1",
        thread_id="logical-thread-1",
        turn_id="vendor-turn-1",
        instructions="Keep going.",
        metadata=None,
    )

    response = runtime.steer_turn(steer_request)

    assert response.status == "running"
    assert response.metadata == {}
    assert json.loads(steer_record_path.read_text(encoding="utf-8")) == {
        "threadId": "vendor-thread-1",
        "turnId": "vendor-turn-1",
        "input": [{"type": "text", "text": "Keep going."}],
    }

def test_runtime_launch_session_exports_codex_home(tmp_path: Path) -> None:
    codex_home_record_path = tmp_path / "codex-home.txt"
    script = write_fake_app_server(
        tmp_path,
        codex_home_record_path=codex_home_record_path,
    )
    request = launch_request(tmp_path)
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)

    assert codex_home_record_path.read_text(encoding="utf-8").splitlines()[-1] == str(
        Path(request.codex_home_path)
    )

def test_runtime_launch_session_seeds_auth_volume_without_overwriting_materialized_config(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")
    (auth_volume_path / "config.toml").write_text("model = 'gpt-5.4'\n", encoding="utf-8")
    (auth_volume_path / "logs_1.sqlite").write_text("log", encoding="utf-8")
    Path(request.codex_home_path, "config.toml").write_text(
        "model = 'qwen/qwen3.6-plus'\n",
        encoding="utf-8",
    )

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)

    assert Path(request.codex_home_path, "auth.json").is_file()
    assert Path(request.codex_home_path, "config.toml").read_text(encoding="utf-8") == (
        "model = 'qwen/qwen3.6-plus'\n"
    )
    assert not Path(request.codex_home_path, "logs_1.sqlite").exists()

def test_runtime_launch_session_seeds_auth_directories_and_excludes_sessions(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")
    account_dir = auth_volume_path / "accounts"
    account_dir.mkdir()
    (account_dir / "default.json").write_text('{"account":"default"}', encoding="utf-8")
    sessions_dir = auth_volume_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "rollout.jsonl").write_text("secret transcript", encoding="utf-8")
    tmp_dir = auth_volume_path / ".tmp" / "plugins" / ".git" / "objects" / "pack"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "pack-readonly.pack").write_text("plugin cache", encoding="utf-8")
    transient_tmp_dir = auth_volume_path / "tmp" / "arg0" / "codex-helpers"
    transient_tmp_dir.mkdir(parents=True)
    (transient_tmp_dir / "apply_patch").symlink_to(
        transient_tmp_dir / "missing-apply-patch"
    )
    symlink_path = auth_volume_path / "linked-auth.json"
    symlink_path.symlink_to(auth_volume_path / "auth.json")

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)

    assert Path(request.codex_home_path, "auth.json").is_file()
    assert Path(request.codex_home_path, "accounts", "default.json").is_file()
    assert not Path(request.codex_home_path, "sessions").exists()
    assert not Path(request.codex_home_path, ".tmp").exists()
    assert not Path(request.codex_home_path, "tmp").exists()
    assert not Path(request.codex_home_path, "linked-auth.json").exists()


def test_runtime_launch_session_skips_unreadable_plugin_install_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")
    remote_plugin_dir = (
        auth_volume_path
        / "plugins"
        / "cache"
        / "openai-curated-remote"
        / "github"
    )
    remote_plugin_dir.mkdir(parents=True)
    marker_path = remote_plugin_dir / ".codex-remote-plugin-install.json"
    marker_path.write_text('{"installed":true}', encoding="utf-8")
    cache_index_path = remote_plugin_dir / "plugin-cache-index.json"
    cache_index_path.write_text('{"cache":true}', encoding="utf-8")
    skill_path = remote_plugin_dir / "0.1.5" / "skills" / "github" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# GitHub\n", encoding="utf-8")

    real_copy2 = shutil.copy2

    def _copy2_without_marker(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> str:
        if Path(source).name == ".codex-remote-plugin-install.json":
            raise AssertionError("plugin install metadata should not be copied")
        if Path(source) == cache_index_path:
            raise PermissionError("optional plugin cache metadata is unreadable")
        return str(real_copy2(source, destination, *args, **kwargs))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.shutil.copy2",
        _copy2_without_marker,
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)

    codex_home_path = Path(request.codex_home_path)
    assert (codex_home_path / "auth.json").is_file()
    assert (
        codex_home_path
        / "plugins"
        / "cache"
        / "openai-curated-remote"
        / "github"
        / "0.1.5"
        / "skills"
        / "github"
        / "SKILL.md"
    ).is_file()
    assert not (
        codex_home_path
        / "plugins"
        / "cache"
        / "openai-curated-remote"
        / "github"
        / ".codex-remote-plugin-install.json"
    ).exists()
    assert not (
        codex_home_path
        / "plugins"
        / "cache"
        / "openai-curated-remote"
        / "github"
        / "plugin-cache-index.json"
    ).exists()


def test_runtime_launch_session_fails_on_unreadable_auth_seed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    auth_path = auth_volume_path / "auth.json"
    auth_path.write_text('{"token":"oauth"}', encoding="utf-8")

    def _raise_permission(
        source: str | os.PathLike[str],
        _destination: str | os.PathLike[str],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        if Path(source) == auth_path:
            raise PermissionError("auth seed unreadable")
        raise AssertionError(f"unexpected copy source: {source}")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.shutil.copy2",
        _raise_permission,
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    with pytest.raises(PermissionError, match="auth seed unreadable"):
        runtime.launch_session(request)


def test_runtime_launch_session_auth_seed_overwrites_read_only_files_on_retry(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    source_auth = auth_volume_path / "auth.json"
    source_auth.write_text('{"token":"oauth"}', encoding="utf-8")
    source_auth.chmod(0o444)

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)
    destination_auth = Path(request.codex_home_path, "auth.json")
    assert destination_auth.stat().st_mode & 0o777 == 0o444

    source_auth.chmod(0o644)
    source_auth.write_text('{"token":"oauth-refresh"}', encoding="utf-8")
    source_auth.chmod(0o444)

    runtime.launch_session(request)

    assert destination_auth.read_text(encoding="utf-8") == (
        '{"token":"oauth-refresh"}'
    )
    assert destination_auth.stat().st_mode & 0o777 == 0o444


def test_runtime_syncs_rotated_auth_to_durable_volume(tmp_path: Path) -> None:
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    durable_auth_path = auth_volume_path / "auth.json"
    durable_auth_path.write_text(
        '{"credentialVersion":"seed"}', encoding="utf-8"
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )
    runtime._seed_codex_home_from_auth_volume()
    Path(request.codex_home_path, "auth.json").write_text(
        '{"credentialVersion":"rotated"}', encoding="utf-8"
    )

    runtime._sync_codex_auth_to_volume()

    assert durable_auth_path.read_text(encoding="utf-8") == (
        '{"credentialVersion":"rotated"}'
    )
    assert (auth_volume_path / ".moonmind-auth-sync.lock").is_file()


def test_runtime_auth_sync_preserves_concurrent_reconnect(tmp_path: Path) -> None:
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    durable_auth_path = auth_volume_path / "auth.json"
    durable_auth_path.write_text(
        '{"credentialVersion":"seed"}', encoding="utf-8"
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )
    runtime._seed_codex_home_from_auth_volume()
    Path(request.codex_home_path, "auth.json").write_text(
        '{"credentialVersion":"rotated-by-run"}', encoding="utf-8"
    )
    durable_auth_path.write_text(
        '{"credentialVersion":"reconnected"}', encoding="utf-8"
    )

    runtime._sync_codex_auth_to_volume()

    assert durable_auth_path.read_text(encoding="utf-8") == (
        '{"credentialVersion":"reconnected"}'
    )


def test_runtime_auth_sync_defers_when_volume_lock_is_busy(tmp_path: Path) -> None:
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    durable_auth_path = auth_volume_path / "auth.json"
    durable_auth_path.write_text(
        '{"credentialVersion":"seed"}', encoding="utf-8"
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )
    runtime._seed_codex_home_from_auth_volume()
    Path(request.codex_home_path, "auth.json").write_text(
        '{"credentialVersion":"rotated"}', encoding="utf-8"
    )
    lock_path = auth_volume_path / ".moonmind-auth-sync.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime._sync_codex_auth_to_volume()

    assert durable_auth_path.read_text(encoding="utf-8") == (
        '{"credentialVersion":"seed"}'
    )
    assert "auth sync deferred" in Path(
        request.artifact_spool_path, "stderr.log"
    ).read_text(encoding="utf-8")

    runtime._sync_codex_auth_to_volume()

    assert durable_auth_path.read_text(encoding="utf-8") == (
        '{"credentialVersion":"rotated"}'
    )


def test_runtime_auth_sync_contains_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    durable_auth_path = auth_volume_path / "auth.json"
    durable_auth_path.write_text(
        '{"credentialVersion":"seed"}', encoding="utf-8"
    )
    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )
    runtime._seed_codex_home_from_auth_volume()
    Path(request.codex_home_path, "auth.json").write_text(
        '{"credentialVersion":"rotated"}', encoding="utf-8"
    )

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected auth-volume write failure")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.shutil.copy2",
        fail_copy,
    )

    runtime._sync_codex_auth_to_volume()

    assert durable_auth_path.read_text(encoding="utf-8") == (
        '{"credentialVersion":"seed"}'
    )
    assert "rotated auth persistence failed" in Path(
        request.artifact_spool_path, "stderr.log"
    ).read_text(encoding="utf-8")


def test_runtime_launch_session_rejects_missing_auth_volume_path(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "missing-auth-volume"

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    with pytest.raises(RuntimeError, match="MANAGED_AUTH_VOLUME_PATH does not exist"):
        runtime.launch_session(request)

def test_runtime_launch_session_rejects_file_auth_volume_path(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume-file"
    auth_volume_path.write_text("not a directory", encoding="utf-8")

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    with pytest.raises(RuntimeError, match="MANAGED_AUTH_VOLUME_PATH must be a directory"):
        runtime.launch_session(request)

def test_runtime_launch_session_rejects_auth_volume_equal_to_codex_home(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    with pytest.raises(
        RuntimeError,
        match="MANAGED_AUTH_VOLUME_PATH must not equal MOONMIND_SESSION_CODEX_HOME_PATH",
    ):
        runtime.launch_session(request)

def test_runtime_launch_session_points_codex_config_env_at_per_run_home(
    tmp_path: Path,
) -> None:
    script = write_fake_app_server(tmp_path)
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")

    runtime = CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        auth_volume_path=str(auth_volume_path),
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
        app_server_command=("python3", str(script)),
    )

    runtime.launch_session(request)

    assert runtime._client is not None
    assert runtime._client._env["CODEX_HOME"] == request.codex_home_path
    assert runtime._client._env["CODEX_CONFIG_HOME"] == request.codex_home_path
    assert runtime._client._env["CODEX_CONFIG_PATH"] == str(
        Path(request.codex_home_path) / "config.toml"
    )

def test_run_ready_requires_runtime_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace_path = tmp_path / "repo"
    workspace_path.mkdir()
    monkeypatch.setenv("MOONMIND_SESSION_WORKSPACE_PATH", str(workspace_path))
    monkeypatch.setenv("MOONMIND_SESSION_WORKSPACE_STATE_PATH", str(tmp_path / "session"))
    monkeypatch.setenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("MOONMIND_SESSION_CODEX_HOME_PATH", str(tmp_path / "codex-home"))
    monkeypatch.delenv("MOONMIND_SESSION_IMAGE_REF", raising=False)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.shutil.which",
        lambda _name: "/usr/bin/codex",
    )

    with pytest.raises(RuntimeError, match="MOONMIND_SESSION_IMAGE_REF is required"):
        _run_ready()


def test_run_ready_fails_when_docker_sidecar_preflight_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "repo"
    workspace_path.mkdir()
    monkeypatch.setenv("MOONMIND_SESSION_WORKSPACE_PATH", str(workspace_path))
    monkeypatch.setenv(
        "MOONMIND_SESSION_WORKSPACE_STATE_PATH",
        str(tmp_path / "session"),
    )
    monkeypatch.setenv(
        "MOONMIND_SESSION_ARTIFACT_SPOOL_PATH",
        str(tmp_path / "artifacts"),
    )
    monkeypatch.setenv(
        "MOONMIND_SESSION_CODEX_HOME_PATH",
        str(tmp_path / "codex-home"),
    )
    monkeypatch.setenv("MOONMIND_SESSION_IMAGE_REF", "ghcr.io/acme/moonmind:runtime")
    monkeypatch.setenv("MOONMIND_MANAGED_SESSION_DOCKER_MODE", "docker-sidecar")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/moonmind-docker/docker.sock")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.shutil.which",
        lambda _name: "/usr/bin/codex",
    )

    def fake_preflight(**_kwargs: object) -> CodexPreflightResult:
        return CodexPreflightResult(
            status=automation_models.CodexPreflightStatus.FAILED,
            message="Docker sidecar preflight failed: docker info did not succeed.",
            failure_class="system_error",
            diagnostics_ref="preflight://docker-sidecar",
        )

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.codex_session_runtime.run_docker_sidecar_preflight_check",
        fake_preflight,
    )

    with pytest.raises(RuntimeError, match="Docker sidecar preflight failed"):
        _run_ready()


def _make_runtime_for_turn_items_test(tmp_path: Path) -> CodexManagedSessionRuntime:
    request = launch_request(tmp_path)
    return CodexManagedSessionRuntime(
        workspace_path=request.workspace_path,
        session_workspace_path=request.session_workspace_path,
        artifact_spool_path=request.artifact_spool_path,
        codex_home_path=request.codex_home_path,
        image_ref=request.image_ref,
        control_url="docker-exec://mm-codex-session-sess-1",
        container_id="ctr-1",
    )


class _StubTurnItemsClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "thread/turns/items/list"
        self.calls.append(dict(params))
        if not self._responses:
            raise AssertionError("no remaining stubbed responses")
        next_value = self._responses.pop(0)
        if isinstance(next_value, Exception):
            raise next_value
        return next_value  # type: ignore[return-value]


def test_assistant_text_from_turn_items_list_preserves_partial_pages_on_runtime_error(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime_for_turn_items_test(tmp_path)
    client = _StubTurnItemsClient(
        responses=[
            {
                "data": [
                    {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "partial assistant text",
                        "phase": "final_answer",
                    }
                ],
                "nextCursor": "cursor-2",
            },
            RuntimeError("transport closed"),
        ]
    )

    result = runtime._assistant_text_from_turn_items_list(
        client=client,  # type: ignore[arg-type]
        vendor_thread_id="thread-1",
        vendor_turn_id="turn-1",
    )

    assert result == "partial assistant text"
    assert len(client.calls) == 2


def test_assistant_text_from_turn_items_list_returns_empty_when_first_page_fails(
    tmp_path: Path,
) -> None:
    runtime = _make_runtime_for_turn_items_test(tmp_path)
    client = _StubTurnItemsClient(responses=[RuntimeError("transport closed")])

    result = runtime._assistant_text_from_turn_items_list(
        client=client,  # type: ignore[arg-type]
        vendor_thread_id="thread-1",
        vendor_turn_id="turn-1",
    )

    assert result == ""
    assert len(client.calls) == 1
