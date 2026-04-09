from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionClearRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexAppServerRpcClient,
    CodexManagedSessionRuntime,
    _run_ready,
)


def _write_fake_app_server(
    tmp_path: Path,
    *,
    emit_completion: bool = True,
    fail_thread_resume: bool = False,
    resume_requires_existing_rollout_path: bool = False,
    start_thread_id: str = "vendor-thread-1",
    start_thread_path: str | None = "/tmp/vendor-thread-1.jsonl",
    interrupt_record_path: Path | None = None,
    codex_home_record_path: Path | None = None,
) -> Path:
    script = tmp_path / "fake_app_server.py"
    completion_block = """
        sys.stdout.write(json.dumps({
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": "vendor-turn-1", "items": [], "status": "completed", "error": None},
            },
        }) + "\\n")
""".rstrip()
    if not emit_completion:
        completion_block = ""
    script_template = """
import json
import os
import sys

INTERRUPT_RECORD_PATH = __INTERRUPT_RECORD_PATH__
CODEX_HOME_RECORD_PATH = __CODEX_HOME_RECORD_PATH__
FAIL_THREAD_RESUME = __FAIL_THREAD_RESUME__
RESUME_REQUIRES_EXISTING_ROLLOUT_PATH = __RESUME_REQUIRES_EXISTING_ROLLOUT_PATH__
START_THREAD_ID = __START_THREAD_ID__
START_THREAD_PATH = __START_THREAD_PATH__

for line in sys.stdin:
    message = json.loads(line)
    msg_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        capabilities = message["params"].get("capabilities") or {}
        assert capabilities.get("experimentalApi") is True
        if CODEX_HOME_RECORD_PATH:
            with open(CODEX_HOME_RECORD_PATH, "w", encoding="utf-8") as handle:
                handle.write(sys.argv[0] + "\\n")
                handle.write(os.environ.get("CODEX_HOME", ""))
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
                    "id": START_THREAD_ID,
                    "preview": "",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 1,
                    "status": {"type": "idle"},
                    "path": START_THREAD_PATH,
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
    elif method == "thread/resume":
        thread_path = message["params"].get("path")
        if RESUME_REQUIRES_EXISTING_ROLLOUT_PATH:
            if not thread_path:
                sys.stdout.write(json.dumps({
                    "id": msg_id,
                    "error": {"code": -32600, "message": "thread not found"},
                }) + "\\n")
                sys.stdout.flush()
                continue
            if not os.path.isfile(thread_path):
                sys.stdout.write(json.dumps({
                    "id": msg_id,
                    "error": {
                        "code": -32600,
                        "message": f"failed to load rollout `{thread_path}`: No such file or directory (os error 2)",
                    },
                }) + "\\n")
                sys.stdout.flush()
                continue
        if FAIL_THREAD_RESUME:
            sys.stdout.write(json.dumps({
                "id": msg_id,
                "error": {"code": -32600, "message": "no rollout found for thread id vendor-thread-1"},
            }) + "\\n")
            sys.stdout.flush()
            continue
        if thread_path is not None:
            assert str(thread_path).endswith("vendor-thread-1.jsonl")
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
                    "path": thread_path or "/tmp/vendor-thread-1.jsonl",
                    "cwd": "/work/repo",
                    "cliVersion": "0.118.0",
                    "source": "app-server",
                    "agentNickname": None,
                    "agentRole": None,
                    "gitInfo": None,
                    "name": None,
                    "turns": [],
                }
            },
        }) + "\\n")
        sys.stdout.flush()
    elif method == "turn/start":
        thread_id = message["params"]["threadId"]
        input_items = message["params"]["input"]
        assert isinstance(input_items, list)
        assert input_items[0]["type"] == "text"
        assert input_items[0]["text"] == "Reply with exactly the word OK"
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {"turn": {"id": "vendor-turn-1", "items": [], "status": "inProgress", "error": None}},
        }) + "\\n")
__COMPLETION_BLOCK__
        sys.stdout.flush()
    elif method == "turn/interrupt":
        if INTERRUPT_RECORD_PATH:
            with open(INTERRUPT_RECORD_PATH, "w", encoding="utf-8") as handle:
                json.dump(message["params"], handle)
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {"status": "interrupted"},
        }) + "\\n")
        sys.stdout.flush()
    elif method == "thread/read":
        thread_id = message["params"]["threadId"]
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {
                "thread": {
                    "id": thread_id,
                    "preview": "OK",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 2,
                    "status": {"type": "idle"},
                    "path": f"/tmp/{thread_id}.jsonl",
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
""".strip() + "\n"
    script.write_text(
        script_template.replace(
            "__INTERRUPT_RECORD_PATH__",
            repr(str(interrupt_record_path) if interrupt_record_path is not None else ""),
        )
        .replace(
            "__CODEX_HOME_RECORD_PATH__",
            repr(str(codex_home_record_path) if codex_home_record_path is not None else ""),
        )
        .replace("__FAIL_THREAD_RESUME__", "True" if fail_thread_resume else "False")
        .replace(
            "__RESUME_REQUIRES_EXISTING_ROLLOUT_PATH__",
            "True" if resume_requires_existing_rollout_path else "False",
        )
        .replace("__START_THREAD_ID__", repr(start_thread_id))
        .replace("__START_THREAD_PATH__", repr(start_thread_path))
        .replace("__COMPLETION_BLOCK__", completion_block),
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
    assert "vendorThreadPath" not in state_payload


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


def test_runtime_send_turn_recovers_vendor_thread_path_from_sessions_dir(
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
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadPath"] == str(recovered_path)


def test_runtime_send_turn_falls_back_to_new_thread_when_resume_fails(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(tmp_path, fail_thread_resume=True)
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
    updated_state = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert updated_state["vendorThreadId"] == "vendor-thread-1"


def test_runtime_send_turn_drops_stale_vendor_thread_path_when_fallback_starts_new_thread(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(
        tmp_path,
        fail_thread_resume=True,
        start_thread_id="vendor-thread-2",
        start_thread_path=None,
    )
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
    script = _write_fake_app_server(
        tmp_path,
        resume_requires_existing_rollout_path=True,
    )
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
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_state["vendorThreadId"] == "vendor-thread-1"
    assert updated_state["vendorThreadPath"] == "/tmp/vendor-thread-1.jsonl"


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


def test_runtime_send_turn_times_out_without_completion_notification(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(tmp_path, emit_completion=False)
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
        turn_completion_timeout_seconds=0.01,
    )
    runtime.launch_session(request)

    with pytest.raises(RuntimeError, match="timed out waiting for codex app-server"):
        runtime.send_turn(
            SendCodexManagedSessionTurnRequest(
                sessionId="sess-1",
                sessionEpoch=1,
                containerId="ctr-1",
                threadId="logical-thread-1",
                instructions="Reply with exactly the word OK",
            )
        )

    state_payload = json.loads(
        (Path(request.session_workspace_path) / ".moonmind-codex-session-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state_payload["activeTurnId"] == "vendor-turn-1"


def test_runtime_interrupt_turn_uses_app_server_transport(tmp_path: Path) -> None:
    interrupt_record_path = tmp_path / "interrupt.json"
    script = _write_fake_app_server(
        tmp_path,
        interrupt_record_path=interrupt_record_path,
    )
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


def test_runtime_launch_session_exports_codex_home(tmp_path: Path) -> None:
    codex_home_record_path = tmp_path / "codex-home.txt"
    script = _write_fake_app_server(
        tmp_path,
        codex_home_record_path=codex_home_record_path,
    )
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

    assert codex_home_record_path.read_text(encoding="utf-8").splitlines()[-1] == str(
        Path(request.codex_home_path)
    )


def test_runtime_launch_session_seeds_auth_volume_without_overwriting_materialized_config(
    tmp_path: Path,
) -> None:
    script = _write_fake_app_server(tmp_path)
    request = _launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")
    (auth_volume_path / "config.toml").write_text("model = 'gpt-5.4'\n", encoding="utf-8")
    (auth_volume_path / "logs_1.sqlite").write_text("log", encoding="utf-8")
    Path(request.codex_home_path, "config.toml").write_text(
        "model = 'qwen/qwen3.6-plus:free'\n",
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
        "model = 'qwen/qwen3.6-plus:free'\n"
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
