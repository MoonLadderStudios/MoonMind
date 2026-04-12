from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
import sqlite3
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    CodexManagedSessionLocator,
    InterruptCodexManagedSessionTurnRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
)
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexAppServerRpcClient,
    CodexManagedSessionRuntime,
    _ROLLOUT_RECOVERY_MAX_BYTES,
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
    assert "turn completed without assistant output" in stderr_path.read_text(
        encoding="utf-8"
    )


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


def test_runtime_send_turn_falls_back_to_new_thread_when_resume_fails(
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
