from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    LaunchCodexManagedSessionRequest,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexAppServerRpcClient,
    CodexManagedSessionRuntime,
)


def _write_fake_app_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_app_server.py"
    script.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    msg_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({
            "method": "configWarning",
            "params": {"summary": "fake-warning", "details": None},
        }) + "\\n")
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {
                "userAgent": "fake/0.1",
                "codexHome": "/tmp/fake-codex-home",
                "platformFamily": "unix",
                "platformOs": "linux",
            },
        }) + "\\n")
        sys.stdout.flush()
    elif method == "thread/start":
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {
                "thread": {
                    "id": "vendor-thread-1",
                    "preview": "",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 1,
                    "status": {"type": "idle"},
                    "path": "/tmp/vendor-thread-1.jsonl",
                    "cwd": "/work/repo",
                    "cliVersion": "0.118.0",
                    "source": "app-server",
                    "agentNickname": None,
                    "agentRole": None,
                    "gitInfo": None,
                    "name": None,
                    "turns": [],
                },
                "model": "gpt-5.4",
                "modelProvider": "openai",
                "serviceTier": None,
                "cwd": "/work/repo",
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {
                    "type": "workspaceWrite",
                    "writableRoots": [],
                    "readOnlyAccess": {"type": "fullAccess"},
                    "networkAccess": False,
                    "excludeTmpdirEnvVar": False,
                    "excludeSlashTmp": False,
                },
                "reasoningEffort": "high",
            },
        }) + "\\n")
        sys.stdout.flush()
    elif method == "turn/start":
        thread_id = message["params"]["threadId"]
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {"turn": {"id": "vendor-turn-1", "items": [], "status": "inProgress", "error": None}},
        }) + "\\n")
        sys.stdout.write(json.dumps({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": "vendor-turn-1", "items": [], "status": "completed", "error": None},
            },
        }) + "\\n")
        sys.stdout.flush()
    elif method == "thread/read":
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {
                "thread": {
                    "id": "vendor-thread-1",
                    "preview": "OK",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 2,
                    "status": {"type": "idle"},
                    "path": "/tmp/vendor-thread-1.jsonl",
                    "cwd": "/work/repo",
                    "cliVersion": "0.118.0",
                    "source": "app-server",
                    "agentNickname": None,
                    "agentRole": None,
                    "gitInfo": None,
                    "name": None,
                    "turns": [
                        {
                            "id": "vendor-turn-1",
                            "status": "completed",
                            "error": None,
                            "items": [
                                {"type": "agentMessage", "id": "msg-1", "text": "OK", "phase": "final_answer", "memoryCitation": None}
                            ],
                        }
                    ],
                }
            },
        }) + "\\n")
        sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps({"id": msg_id, "result": {}}) + "\\n")
        sys.stdout.flush()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


def _launch_request(tmp_path: Path) -> LaunchCodexManagedSessionRequest:
    workspace_path = tmp_path / "repo"
    session_workspace_path = tmp_path / "session"
    artifact_spool_path = tmp_path / "artifacts"
    codex_home_path = tmp_path / "codex-home"
    workspace_path.mkdir()
    session_workspace_path.mkdir()
    artifact_spool_path.mkdir()
    codex_home_path.mkdir()
    return LaunchCodexManagedSessionRequest(
        taskRunId="task-1",
        sessionId="sess-1",
        threadId="logical-thread-1",
        workspacePath=str(workspace_path),
        sessionWorkspacePath=str(session_workspace_path),
        artifactSpoolPath=str(artifact_spool_path),
        codexHomePath=str(codex_home_path),
        imageRef="ghcr.io/moonladderstudios/moonmind:latest",
    )


def test_app_server_client_ignores_notifications_until_matching_response(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(tmp_path)
    client = CodexAppServerRpcClient(
        command=("python3", str(script)),
        client_name="MoonMindTest",
        client_version="0.1",
    )

    initialized = client.initialize()

    assert initialized["codexHome"] == "/tmp/fake-codex-home"
    client.close()


def test_runtime_launch_session_persists_logical_thread_mapping(tmp_path: Path) -> None:
    script = _write_fake_app_server(tmp_path)
    request = _launch_request(tmp_path)
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


def test_runtime_send_turn_returns_completed_response_with_assistant_text(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(tmp_path)
    request = _launch_request(tmp_path)
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


def test_runtime_clear_session_rotates_logical_thread_and_epoch(tmp_path: Path) -> None:
    script = _write_fake_app_server(tmp_path)
    request = _launch_request(tmp_path)
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
