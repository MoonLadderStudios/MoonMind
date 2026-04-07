"""Container-side transitional Codex managed-session runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionClearRequest,
    CodexManagedSessionHandle,
    CodexManagedSessionLocator,
    CodexManagedSessionState,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    FetchCodexManagedSessionSummaryRequest,
    InterruptCodexManagedSessionTurnRequest,
    LaunchCodexManagedSessionRequest,
    PublishCodexManagedSessionArtifactsRequest,
    SendCodexManagedSessionTurnRequest,
    SteerCodexManagedSessionTurnRequest,
    TerminateCodexManagedSessionRequest,
)


_STATE_FILENAME = ".moonmind-codex-session-state.json"
_READY_LOOP_SECONDS = 3600.0


class CodexSessionRuntimeState(BaseModel):
    """Persisted logical-to-vendor session mapping for one container session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    logical_thread_id: str = Field(..., alias="logicalThreadId")
    vendor_thread_id: str = Field(..., alias="vendorThreadId")
    container_id: str = Field(..., alias="containerId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    launched_at: float | None = Field(None, alias="launchedAt")
    last_control_action: str | None = Field(None, alias="lastControlAction")
    last_control_at: float | None = Field(None, alias="lastControlAt")
    last_assistant_text: str | None = Field(None, alias="lastAssistantText")


class CodexAppServerRpcClient:
    """Minimal JSON-RPC stdio client for ``codex app-server``."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        client_name: str,
        client_version: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._command = tuple(command)
        self._client_name = client_name
        self._client_version = client_version
        self._cwd = cwd
        self._env = dict(env or {})
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._notifications: list[dict[str, Any]] = []
        self._responses: dict[int, dict[str, Any]] = {}
        self._initialize_result: dict[str, Any] | None = None

    def _ensure_started(self) -> None:
        if self._process is not None:
            return
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self._cwd,
            env={**os.environ, **self._env},
        )

    def _read_message(self) -> dict[str, Any]:
        self._ensure_started()
        assert self._process is not None
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            stderr_text = ""
            if self._process.stderr is not None:
                stderr_text = self._process.stderr.read()
            raise RuntimeError(
                "codex app-server closed unexpectedly"
                + (f": {stderr_text.strip()}" if stderr_text.strip() else "")
            )
        return json.loads(line)

    def _write_message(self, message: Mapping[str, Any]) -> None:
        self._ensure_started()
        assert self._process is not None
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _stash_message(self, message: Mapping[str, Any]) -> None:
        if "method" in message:
            self._notifications.append(dict(message))
            return
        raw_id = message.get("id")
        if isinstance(raw_id, int):
            self._responses[raw_id] = dict(message)

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method != "initialize" and self._initialize_result is None:
            self.initialize()

        message_id = self._next_id
        self._next_id += 1
        self._write_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": method,
                "params": dict(params or {}),
            }
        )

        while True:
            queued = self._responses.pop(message_id, None)
            if queued is not None:
                message = queued
            else:
                message = self._read_message()

            if "method" in message:
                self._notifications.append(message)
                continue

            raw_id = message.get("id")
            if raw_id != message_id:
                self._stash_message(message)
                continue

            if "error" in message:
                raise RuntimeError(
                    f"codex app-server request {method} failed: {message['error']}"
                )
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def initialize(self) -> dict[str, Any]:
        if self._initialize_result is not None:
            return self._initialize_result
        self._initialize_result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self._client_name,
                    "version": self._client_version,
                }
            },
        )
        return self._initialize_result

    def wait_for_notification(
        self,
        method: str,
        *,
        predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        while True:
            for index, notification in enumerate(self._notifications):
                if notification.get("method") != method:
                    continue
                if predicate is not None and not predicate(notification):
                    continue
                return self._notifications.pop(index)

            message = self._read_message()
            if message.get("method") == method and (
                predicate is None or predicate(message)
            ):
                return message
            self._stash_message(message)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


class CodexManagedSessionRuntime:
    """Local runtime implementation invoked inside the session container."""

    def __init__(
        self,
        *,
        workspace_path: str,
        session_workspace_path: str,
        artifact_spool_path: str,
        codex_home_path: str,
        image_ref: str,
        control_url: str,
        container_id: str,
        app_server_command: Sequence[str] = ("codex", "app-server"),
    ) -> None:
        self._workspace_path = Path(workspace_path)
        self._session_workspace_path = Path(session_workspace_path)
        self._artifact_spool_path = Path(artifact_spool_path)
        self._codex_home_path = Path(codex_home_path)
        self._image_ref = image_ref
        self._control_url = control_url
        self._container_id = container_id
        self._app_server_command = tuple(app_server_command)
        self._client: CodexAppServerRpcClient | None = None

    @property
    def _state_path(self) -> Path:
        return self._session_workspace_path / _STATE_FILENAME

    def _ensure_directories(self) -> None:
        self._workspace_path.mkdir(parents=True, exist_ok=True)
        self._session_workspace_path.mkdir(parents=True, exist_ok=True)
        self._artifact_spool_path.mkdir(parents=True, exist_ok=True)
        self._codex_home_path.mkdir(parents=True, exist_ok=True)

    def _app_server_client(self) -> CodexAppServerRpcClient:
        if self._client is None:
            self._client = CodexAppServerRpcClient(
                command=self._app_server_command,
                client_name="MoonMind",
                client_version="phase4",
                cwd=str(self._workspace_path),
                env={"HOME": str(self._codex_home_path)},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _load_state(self) -> CodexSessionRuntimeState:
        if not self._state_path.is_file():
            raise RuntimeError(
                f"managed session state file is missing: {self._state_path}"
            )
        return CodexSessionRuntimeState.model_validate_json(
            self._state_path.read_text(encoding="utf-8")
        )

    def _save_state(self, state: CodexSessionRuntimeState) -> None:
        self._ensure_directories()
        self._state_path.write_text(
            state.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _session_state(self, state: CodexSessionRuntimeState) -> CodexManagedSessionState:
        return CodexManagedSessionState(
            sessionId=state.session_id,
            sessionEpoch=state.session_epoch,
            containerId=state.container_id,
            threadId=state.logical_thread_id,
            activeTurnId=state.active_turn_id,
        )

    def _handle(
        self,
        state: CodexSessionRuntimeState,
        *,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> CodexManagedSessionHandle:
        merged = {
            "vendorThreadId": state.vendor_thread_id,
            **dict(metadata or {}),
        }
        if state.last_assistant_text:
            merged.setdefault("lastAssistantText", state.last_assistant_text)
        return CodexManagedSessionHandle(
            sessionState=self._session_state(state),
            status=status,
            imageRef=self._image_ref,
            controlUrl=self._control_url,
            metadata=merged,
        )

    def _validate_locator(self, request: CodexManagedSessionLocator) -> CodexSessionRuntimeState:
        state = self._load_state()
        if state.session_id != request.session_id:
            raise RuntimeError("sessionId does not match the active managed session")
        if state.session_epoch != request.session_epoch:
            raise RuntimeError("sessionEpoch does not match the active managed session")
        if state.container_id != request.container_id:
            raise RuntimeError("containerId does not match the active managed session")
        if state.logical_thread_id != request.thread_id:
            raise RuntimeError("threadId does not match the active managed session")
        return state

    @staticmethod
    def _extract_assistant_text(thread_payload: Mapping[str, Any]) -> str:
        thread = thread_payload.get("thread")
        if not isinstance(thread, Mapping):
            return ""
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return ""
        for turn in reversed(turns):
            if not isinstance(turn, Mapping):
                continue
            items = turn.get("items")
            if not isinstance(items, list):
                continue
            for item in reversed(items):
                if not isinstance(item, Mapping):
                    continue
                if item.get("type") != "agentMessage":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    def launch_session(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        self._ensure_directories()
        client = self._app_server_client()
        client.initialize()
        started = client.request("thread/start", {"cwd": str(self._workspace_path)})
        thread_payload = started.get("thread")
        if not isinstance(thread_payload, Mapping):
            raise RuntimeError("codex app-server thread/start did not return a thread")
        vendor_thread_id = str(thread_payload.get("id") or "").strip()
        if not vendor_thread_id:
            raise RuntimeError("codex app-server thread/start returned a blank thread id")

        state = CodexSessionRuntimeState(
            sessionId=request.session_id,
            sessionEpoch=request.session_epoch,
            logicalThreadId=request.thread_id,
            vendorThreadId=vendor_thread_id,
            containerId=self._container_id,
            activeTurnId=None,
            launchedAt=time.time(),
            lastControlAction="start_session",
            lastControlAt=time.time(),
        )
        self._save_state(state)
        return self._handle(
            state,
            status="ready",
            metadata={
                "approvalPolicy": started.get("approvalPolicy"),
                "model": started.get("model"),
            },
        )

    def session_status(
        self,
        request: CodexManagedSessionLocator,
    ) -> CodexManagedSessionHandle:
        state = self._validate_locator(request)
        status = "busy" if state.active_turn_id else "ready"
        return self._handle(state, status=status)

    def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        state = self._validate_locator(request)
        client = self._app_server_client()
        client.initialize()

        started = client.request(
            "turn/start",
            {
                "threadId": state.vendor_thread_id,
                "instructions": request.instructions,
                "inputRefs": list(request.input_refs),
                "metadata": request.metadata,
            },
        )
        turn_payload = started.get("turn")
        if not isinstance(turn_payload, Mapping):
            raise RuntimeError("codex app-server turn/start did not return a turn")
        vendor_turn_id = str(turn_payload.get("id") or "").strip()
        if not vendor_turn_id:
            raise RuntimeError("codex app-server turn/start returned a blank turn id")

        state.active_turn_id = vendor_turn_id
        state.last_control_action = "send_turn"
        state.last_control_at = time.time()
        self._save_state(state)

        client.wait_for_notification(
            "turn/completed",
            predicate=lambda message: (
                isinstance(message.get("params"), Mapping)
                and message["params"].get("threadId") == state.vendor_thread_id
            ),
        )
        thread_payload = client.request("thread/read", {"threadId": state.vendor_thread_id})
        assistant_text = self._extract_assistant_text(thread_payload)

        state.active_turn_id = None
        state.last_assistant_text = assistant_text or None
        self._save_state(state)
        return CodexManagedSessionTurnResponse(
            sessionState=self._session_state(state),
            turnId=vendor_turn_id,
            status="completed",
            metadata={"assistantText": assistant_text},
        )

    def steer_turn(
        self,
        request: SteerCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        state = self._validate_locator(request)
        return CodexManagedSessionTurnResponse(
            sessionState=self._session_state(state),
            turnId=request.turn_id,
            status="failed",
            metadata={
                "reason": (
                    "steer_turn is not supported by the transitional synchronous "
                    "Codex managed-session runtime"
                )
            },
        )

    def interrupt_turn(
        self,
        request: InterruptCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        state = self._validate_locator(request)
        state.active_turn_id = None
        state.last_control_action = "interrupt_turn"
        state.last_control_at = time.time()
        self._save_state(state)
        return CodexManagedSessionTurnResponse(
            sessionState=self._session_state(state),
            turnId=request.turn_id,
            status="interrupted",
            metadata={"reason": request.reason or "interrupt requested"},
        )

    def clear_session(
        self,
        request: CodexManagedSessionClearRequest,
    ) -> CodexManagedSessionHandle:
        state = self._validate_locator(request)
        client = self._app_server_client()
        client.initialize()
        started = client.request("thread/start", {"cwd": str(self._workspace_path)})
        thread_payload = started.get("thread")
        if not isinstance(thread_payload, Mapping):
            raise RuntimeError("codex app-server thread/start did not return a thread")
        vendor_thread_id = str(thread_payload.get("id") or "").strip()
        if not vendor_thread_id:
            raise RuntimeError("codex app-server thread/start returned a blank thread id")

        state.session_epoch += 1
        state.logical_thread_id = request.new_thread_id
        state.vendor_thread_id = vendor_thread_id
        state.active_turn_id = None
        state.last_control_action = "clear_session"
        state.last_control_at = time.time()
        self._save_state(state)
        return self._handle(state, status="ready")

    def terminate_session(
        self,
        request: TerminateCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        state = self._validate_locator(request)
        state.active_turn_id = None
        state.last_control_action = "terminate_session"
        state.last_control_at = time.time()
        self._save_state(state)
        return self._handle(state, status="terminated")

    def fetch_session_summary(
        self,
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        state = self._validate_locator(request)
        return CodexManagedSessionSummary(
            sessionState=self._session_state(state),
            latestSummaryRef=None,
            latestCheckpointRef=None,
            latestControlEventRef=None,
            metadata={"lastAssistantText": state.last_assistant_text},
        )

    def publish_session_artifacts(
        self,
        request: PublishCodexManagedSessionArtifactsRequest,
    ) -> CodexManagedSessionArtifactsPublication:
        state = self._validate_locator(request)
        return CodexManagedSessionArtifactsPublication(
            sessionState=self._session_state(state),
            publishedArtifactRefs=(),
            latestSummaryRef=None,
            latestCheckpointRef=None,
            latestControlEventRef=None,
            metadata=dict(request.metadata),
        )


def _runtime_from_environment() -> CodexManagedSessionRuntime:
    workspace_path = os.environ["MOONMIND_SESSION_WORKSPACE_PATH"]
    session_workspace_path = os.environ["MOONMIND_SESSION_WORKSPACE_STATE_PATH"]
    artifact_spool_path = os.environ["MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"]
    codex_home_path = os.environ["MOONMIND_SESSION_CODEX_HOME_PATH"]
    image_ref = os.environ["MOONMIND_SESSION_IMAGE_REF"]
    container_id = os.environ.get("MOONMIND_SESSION_CONTAINER_ID", "").strip()
    if not container_id:
        state_path = Path(session_workspace_path) / _STATE_FILENAME
        if state_path.is_file():
            state = CodexSessionRuntimeState.model_validate_json(
                state_path.read_text(encoding="utf-8")
            )
            container_id = state.container_id
    if not container_id:
        raise RuntimeError("MOONMIND_SESSION_CONTAINER_ID is required")

    control_url = os.environ.get("MOONMIND_SESSION_CONTROL_URL", "").strip()
    if not control_url:
        control_url = f"docker-exec://{os.environ.get('HOSTNAME', 'codex-session')}"
    return CodexManagedSessionRuntime(
        workspace_path=workspace_path,
        session_workspace_path=session_workspace_path,
        artifact_spool_path=artifact_spool_path,
        codex_home_path=codex_home_path,
        image_ref=image_ref,
        control_url=control_url,
        container_id=container_id,
    )


def _emit_json(payload: BaseModel | Mapping[str, Any], *, exit_code: int = 0) -> int:
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(by_alias=True, exclude_none=True)
    else:
        text = json.dumps(payload)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return exit_code


def _run_ready() -> int:
    ready = shutil.which("codex") is not None
    return _emit_json({"ready": ready})


def _run_serve() -> int:
    stopping = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    while not stopping:
        time.sleep(_READY_LOOP_SECONDS)
    return 0


def _invoke_action(action: str, payload: Mapping[str, Any]) -> BaseModel:
    runtime = _runtime_from_environment()
    try:
        if action == "launch_session":
            return runtime.launch_session(
                LaunchCodexManagedSessionRequest.model_validate(payload)
            )
        if action == "session_status":
            return runtime.session_status(
                CodexManagedSessionLocator.model_validate(payload)
            )
        if action == "send_turn":
            return runtime.send_turn(
                SendCodexManagedSessionTurnRequest.model_validate(payload)
            )
        if action == "steer_turn":
            return runtime.steer_turn(
                SteerCodexManagedSessionTurnRequest.model_validate(payload)
            )
        if action == "interrupt_turn":
            return runtime.interrupt_turn(
                InterruptCodexManagedSessionTurnRequest.model_validate(payload)
            )
        if action == "clear_session":
            return runtime.clear_session(
                CodexManagedSessionClearRequest.model_validate(payload)
            )
        if action == "terminate_session":
            return runtime.terminate_session(
                TerminateCodexManagedSessionRequest.model_validate(payload)
            )
        if action == "fetch_session_summary":
            return runtime.fetch_session_summary(
                FetchCodexManagedSessionSummaryRequest.model_validate(payload)
            )
        if action == "publish_session_artifacts":
            return runtime.publish_session_artifacts(
                PublishCodexManagedSessionArtifactsRequest.model_validate(payload)
            )
        raise RuntimeError(f"unsupported managed-session action: {action}")
    finally:
        runtime.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex_session_runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve")
    subparsers.add_parser("ready")
    invoke_parser = subparsers.add_parser("invoke")
    invoke_parser.add_argument("action")
    args = parser.parse_args(argv)

    try:
        if args.command == "serve":
            return _run_serve()
        if args.command == "ready":
            return _run_ready()
        if args.command == "invoke":
            raw_payload = sys.stdin.read()
            payload = json.loads(raw_payload or "{}")
            return _emit_json(_invoke_action(args.action, payload))
        raise RuntimeError(f"unsupported command: {args.command}")
    except Exception as exc:
        return _emit_json({"error": str(exc), "ready": False}, exit_code=1)


if __name__ == "__main__":
    raise SystemExit(main())
