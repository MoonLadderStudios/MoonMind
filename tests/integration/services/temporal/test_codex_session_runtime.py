from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionLocator,
    SendCodexManagedSessionTurnRequest,
)
from moonmind.workflows.temporal.runtime.codex_session_runtime import (
    CodexManagedSessionRuntime,
)
from tests.helpers.codex_session_runtime import (
    launch_request,
    write_fake_app_server,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_runtime_send_turn_recovers_task_complete_message_from_rollout_transcript(
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
                            "last_agent_message": (
                                "Recovered from durable task_complete event"
                            ),
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
    assert response.metadata["assistantText"] == (
        "Recovered from durable task_complete event"
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
    assert handle.metadata["lastAssistantText"] == (
        "Recovered from durable task_complete event"
    )


def test_runtime_launch_session_seeds_auth_volume_and_uses_per_run_codex_home(
    tmp_path: Path,
) -> None:
    codex_home_record_path = tmp_path / "codex-home.txt"
    script = write_fake_app_server(
        tmp_path,
        codex_home_record_path=codex_home_record_path,
    )
    request = launch_request(tmp_path)
    auth_volume_path = tmp_path / "auth-volume"
    auth_volume_path.mkdir()
    (auth_volume_path / "auth.json").write_text('{"token":"oauth"}', encoding="utf-8")
    (auth_volume_path / "logs_1.sqlite").write_text("runtime log", encoding="utf-8")
    (auth_volume_path / "sessions").mkdir()

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
    assert not Path(request.codex_home_path, "logs_1.sqlite").exists()
    assert not Path(request.codex_home_path, "sessions").exists()
    assert codex_home_record_path.read_text(encoding="utf-8").splitlines()[-1] == str(
        Path(request.codex_home_path)
    )
