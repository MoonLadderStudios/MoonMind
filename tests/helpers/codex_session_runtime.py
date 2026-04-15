from __future__ import annotations

from pathlib import Path

from moonmind.schemas.managed_session_models import LaunchCodexManagedSessionRequest


def write_fake_app_server(
    tmp_path: Path,
    *,
    completion_notification_method: str | None = "turn/completed",
    complete_turn_on_read: bool = True,
    omit_turns_on_read: bool = False,
    omit_turns_when_incomplete: bool = False,
    assistant_text: str = "OK",
    thread_status_type: str = "idle",
    thread_status_reason: str | None = None,
    fail_thread_resume: bool = False,
    resume_requires_existing_rollout_path: bool = False,
    start_thread_id: str = "vendor-thread-1",
    start_thread_path: str | None = "/tmp/vendor-thread-1.jsonl",
    rollout_entries_on_read: list[dict] | None = None,
    steer_record_path: Path | None = None,
    interrupt_record_path: Path | None = None,
    codex_home_record_path: Path | None = None,
) -> Path:
    script = tmp_path / "fake_app_server.py"
    completion_block = """
        turn_completed = True
        sys.stdout.write(json.dumps({
            "method": COMPLETION_NOTIFICATION_METHOD,
            "params": {
                "threadId": thread_id,
                "turn": {"id": "vendor-turn-1", "items": [], "status": "completed", "error": None},
            },
        }) + "\\n")
""".rstrip()
    if not completion_notification_method:
        completion_block = ""
    script_template = """
import json
import os
import sys

INTERRUPT_RECORD_PATH = __INTERRUPT_RECORD_PATH__
STEER_RECORD_PATH = __STEER_RECORD_PATH__
CODEX_HOME_RECORD_PATH = __CODEX_HOME_RECORD_PATH__
FAIL_THREAD_RESUME = __FAIL_THREAD_RESUME__
RESUME_REQUIRES_EXISTING_ROLLOUT_PATH = __RESUME_REQUIRES_EXISTING_ROLLOUT_PATH__
START_THREAD_ID = __START_THREAD_ID__
START_THREAD_PATH = __START_THREAD_PATH__
COMPLETION_NOTIFICATION_METHOD = __COMPLETION_NOTIFICATION_METHOD__
COMPLETE_TURN_ON_READ = __COMPLETE_TURN_ON_READ__
OMIT_TURNS_ON_READ = __OMIT_TURNS_ON_READ__
OMIT_TURNS_WHEN_INCOMPLETE = __OMIT_TURNS_WHEN_INCOMPLETE__
ASSISTANT_TEXT = __ASSISTANT_TEXT__
THREAD_STATUS_TYPE = __THREAD_STATUS_TYPE__
THREAD_STATUS_REASON = __THREAD_STATUS_REASON__
ROLLOUT_ENTRIES_ON_READ = __ROLLOUT_ENTRIES_ON_READ__
turn_completed = False

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
        turn_completed = COMPLETE_TURN_ON_READ and not COMPLETION_NOTIFICATION_METHOD
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
    elif method == "turn/steer":
        if STEER_RECORD_PATH:
            with open(STEER_RECORD_PATH, "w", encoding="utf-8") as handle:
                json.dump(message["params"], handle)
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {"status": "running"},
        }) + "\\n")
        sys.stdout.flush()
    elif method == "thread/read":
        thread_id = message["params"]["threadId"]
        if START_THREAD_PATH and ROLLOUT_ENTRIES_ON_READ:
            os.makedirs(os.path.dirname(START_THREAD_PATH), exist_ok=True)
            with open(START_THREAD_PATH, "a", encoding="utf-8") as rollout_handle:
                for rollout_entry in ROLLOUT_ENTRIES_ON_READ:
                    rollout_handle.write(json.dumps(rollout_entry) + "\\n")
        turn_status = "completed" if turn_completed else "inProgress"
        turn_items = []
        if turn_completed:
            turn_items = [
                {"type": "agentMessage", "id": "msg-1", "text": ASSISTANT_TEXT, "phase": "final_answer", "memoryCitation": None}
            ]
        should_omit_turns = OMIT_TURNS_ON_READ and (turn_completed or OMIT_TURNS_WHEN_INCOMPLETE)
        turns = []
        preview = ""
        if not should_omit_turns:
            turns = [
                {
                    "id": "vendor-turn-1",
                    "status": turn_status,
                    "error": None,
                    "items": turn_items,
                }
            ]
            preview = ASSISTANT_TEXT
        status_payload = {"type": THREAD_STATUS_TYPE}
        if THREAD_STATUS_REASON:
            status_payload["reason"] = THREAD_STATUS_REASON
        sys.stdout.write(json.dumps({
            "id": msg_id,
            "result": {
                "thread": {
                    "id": thread_id,
                    "preview": preview,
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1,
                    "updatedAt": 2,
                    "status": status_payload,
                    "path": f"/tmp/{thread_id}.jsonl",
                    "cwd": "/work/repo",
                    "cliVersion": "0.118.0",
                    "source": "app-server",
                    "agentNickname": None,
                    "agentRole": None,
                    "gitInfo": None,
                    "name": None,
                    "turns": turns,
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
            "__STEER_RECORD_PATH__",
            repr(str(steer_record_path) if steer_record_path is not None else ""),
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
        .replace(
            "__COMPLETION_NOTIFICATION_METHOD__",
            repr(completion_notification_method),
        )
        .replace(
            "__COMPLETE_TURN_ON_READ__",
            "True" if complete_turn_on_read else "False",
        )
        .replace("__OMIT_TURNS_ON_READ__", "True" if omit_turns_on_read else "False")
        .replace(
            "__OMIT_TURNS_WHEN_INCOMPLETE__",
            "True" if omit_turns_when_incomplete else "False",
        )
        .replace("__START_THREAD_ID__", repr(start_thread_id))
        .replace("__START_THREAD_PATH__", repr(start_thread_path))
        .replace("__ASSISTANT_TEXT__", repr(assistant_text))
        .replace("__THREAD_STATUS_TYPE__", repr(thread_status_type))
        .replace("__THREAD_STATUS_REASON__", repr(thread_status_reason))
        .replace("__ROLLOUT_ENTRIES_ON_READ__", repr(rollout_entries_on_read or []))
        .replace("__COMPLETION_BLOCK__", completion_block),
        encoding="utf-8",
    )
    return script


def launch_request(tmp_path: Path) -> LaunchCodexManagedSessionRequest:
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
