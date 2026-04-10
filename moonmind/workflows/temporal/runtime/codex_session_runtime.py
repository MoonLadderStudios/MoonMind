"""Container-side transitional Codex managed-session runtime."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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
from moonmind.workflows.codex_session_timeouts import (
    DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS,
)


_STATE_FILENAME = ".moonmind-codex-session-state.json"
_READY_LOOP_SECONDS = 3600.0
_DEFAULT_TURN_COMPLETION_TIMEOUT_SECONDS = (
    float(DEFAULT_CODEX_TURN_COMPLETION_TIMEOUT_SECONDS)
)
_STDOUT_EOF = object()
_AUTH_SEED_EXCLUDED_NAMES = frozenset({"config.toml", "sessions"})
_AUTH_SEED_EXCLUDED_PREFIXES: tuple[str, ...] = ("logs_", "state_")
_ROLLOUT_RECOVERY_MAX_BYTES = 4 * 1024 * 1024


class CodexSessionRuntimeState(BaseModel):
    """Persisted logical-to-vendor session mapping for one container session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    logical_thread_id: str = Field(..., alias="logicalThreadId")
    vendor_thread_id: str = Field(..., alias="vendorThreadId")
    vendor_thread_path: str | None = Field(None, alias="vendorThreadPath")
    container_id: str = Field(..., alias="containerId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    launched_at: float | None = Field(None, alias="launchedAt")
    last_control_action: str | None = Field(None, alias="lastControlAction")
    last_control_at: float | None = Field(None, alias="lastControlAt")
    last_assistant_text: str | None = Field(None, alias="lastAssistantText")
    last_turn_id: str | None = Field(None, alias="lastTurnId")
    last_turn_status: str | None = Field(None, alias="lastTurnStatus")
    last_turn_error: str | None = Field(None, alias="lastTurnError")


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
        notification_timeout_seconds: float | None = None,
    ) -> None:
        self._command = tuple(command)
        self._client_name = client_name
        self._client_version = client_version
        self._cwd = cwd
        self._env = dict(env or {})
        self._notification_timeout_seconds = notification_timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._stderr_capture: tempfile.SpooledTemporaryFile[str] | None = None
        self._stdout_reader: threading.Thread | None = None
        self._stdout_queue: queue.Queue[object] = queue.Queue()
        self._stdout_reader_error: Exception | None = None
        self._next_id = 1
        self._notifications: list[dict[str, Any]] = []
        self._responses: dict[int, dict[str, Any]] = {}
        self._initialize_result: dict[str, Any] | None = None

    def _ensure_started(self) -> None:
        if self._process is not None:
            return
        self._stderr_capture = tempfile.SpooledTemporaryFile(
            max_size=1_000_000,
            mode="w+",
            encoding="utf-8",
        )
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_capture,
            text=True,
            bufsize=1,
            cwd=self._cwd,
            env={**os.environ, **self._env},
        )
        self._stdout_reader = threading.Thread(
            target=self._drain_stdout,
            name="codex-app-server-stdout",
            daemon=True,
        )
        self._stdout_reader.start()

    def _drain_stdout(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                self._stdout_queue.put(json.loads(stripped))
        except Exception as exc:
            self._stdout_reader_error = exc
        finally:
            self._stdout_queue.put(_STDOUT_EOF)

    def _stderr_text(self) -> str:
        if self._stderr_capture is None:
            return ""
        self._stderr_capture.flush()
        self._stderr_capture.seek(0)
        return self._stderr_capture.read().strip()

    def _read_message(self, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        self._ensure_started()
        try:
            if timeout_seconds is None:
                message = self._stdout_queue.get()
            else:
                message = self._stdout_queue.get(timeout=timeout_seconds)
        except queue.Empty as exc:
            raise TimeoutError(
                "timed out waiting for codex app-server message"
            ) from exc
        if message is _STDOUT_EOF:
            stderr_text = self._stderr_text()
            if self._stdout_reader_error is not None:
                raise RuntimeError(
                    "codex app-server emitted invalid JSON"
                    + (f": {self._stdout_reader_error}" if str(self._stdout_reader_error) else "")
                    + (f"; stderr: {stderr_text}" if stderr_text else "")
                ) from self._stdout_reader_error
            raise RuntimeError(
                "codex app-server closed unexpectedly"
                + (f": {stderr_text}" if stderr_text else "")
            )
        return message if isinstance(message, dict) else {}

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
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
        )
        return self._initialize_result

    def wait_for_notification(
        self,
        method: str | None,
        *,
        predicate: Callable[[Mapping[str, Any]], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        deadline = None
        effective_timeout = (
            self._notification_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if effective_timeout is not None:
            deadline = time.monotonic() + effective_timeout
        while True:
            for index, notification in enumerate(self._notifications):
                if method is not None and notification.get("method") != method:
                    continue
                if predicate is not None and not predicate(notification):
                    continue
                return self._notifications.pop(index)

            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    wait_label = "notification" if method is None else f"notification {method}"
                    raise TimeoutError(
                        f"timed out waiting for codex app-server {wait_label}"
                    )
            message = self._read_message(timeout_seconds=remaining)
            if (method is None or message.get("method") == method) and (
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
        if process.stdout is not None:
            process.stdout.close()
        if self._stdout_reader is not None:
            self._stdout_reader.join(timeout=2)
            self._stdout_reader = None
        if self._stderr_capture is not None:
            self._stderr_capture.close()
            self._stderr_capture = None


class CodexManagedSessionRuntime:
    """Local runtime implementation invoked inside the session container."""

    def __init__(
        self,
        *,
        workspace_path: str,
        session_workspace_path: str,
        artifact_spool_path: str,
        codex_home_path: str,
        auth_volume_path: str | None = None,
        image_ref: str,
        control_url: str,
        container_id: str,
        app_server_command: Sequence[str] = ("codex", "app-server"),
        turn_completion_timeout_seconds: float = _DEFAULT_TURN_COMPLETION_TIMEOUT_SECONDS,
    ) -> None:
        self._workspace_path = Path(workspace_path)
        self._session_workspace_path = Path(session_workspace_path)
        self._artifact_spool_path = Path(artifact_spool_path)
        self._codex_home_path = Path(codex_home_path)
        self._auth_volume_path = (
            Path(auth_volume_path) if str(auth_volume_path or "").strip() else None
        )
        self._image_ref = image_ref
        self._control_url = control_url
        self._container_id = container_id
        self._app_server_command = tuple(app_server_command)
        self._turn_completion_timeout_seconds = turn_completion_timeout_seconds
        self._client: CodexAppServerRpcClient | None = None

    @property
    def _state_path(self) -> Path:
        return self._session_workspace_path / _STATE_FILENAME

    def _ensure_directories(self) -> None:
        self._workspace_path.mkdir(parents=True, exist_ok=True)
        self._session_workspace_path.mkdir(parents=True, exist_ok=True)
        self._artifact_spool_path.mkdir(parents=True, exist_ok=True)
        self._codex_home_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _should_seed_auth_entry(path: Path) -> bool:
        name = path.name
        if name in _AUTH_SEED_EXCLUDED_NAMES:
            return False
        return not any(name.startswith(prefix) for prefix in _AUTH_SEED_EXCLUDED_PREFIXES)

    def _seed_codex_home_from_auth_volume(self) -> None:
        if self._auth_volume_path is None:
            return
        source_root = self._auth_volume_path
        if not source_root.exists():
            raise RuntimeError(
                f"MANAGED_AUTH_VOLUME_PATH does not exist: {source_root}"
            )
        if not source_root.is_dir():
            raise RuntimeError(
                f"MANAGED_AUTH_VOLUME_PATH must be a directory: {source_root}"
            )

        self._ensure_directories()
        for source_path in sorted(source_root.iterdir()):
            if not self._should_seed_auth_entry(source_path):
                continue
            destination = self._codex_home_path / source_path.name
            if source_path.is_symlink():
                continue
            if source_path.is_dir():
                shutil.copytree(source_path, destination, dirs_exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

    def _append_spool(self, stream_name: str, text: str) -> None:
        if stream_name not in {"stdout", "stderr"}:
            raise ValueError(f"unsupported stream for session spool: {stream_name}")
        self._ensure_directories()
        target = self._artifact_spool_path / f"{stream_name}.log"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def _app_server_client(self) -> CodexAppServerRpcClient:
        if self._client is None:
            self._client = CodexAppServerRpcClient(
                command=self._app_server_command,
                client_name="MoonMind",
                client_version="phase4",
                cwd=str(self._workspace_path),
                env={"CODEX_HOME": str(self._codex_home_path)},
                notification_timeout_seconds=self._turn_completion_timeout_seconds,
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
        if state.last_turn_id:
            merged.setdefault("lastTurnId", state.last_turn_id)
        if state.last_turn_status:
            merged.setdefault("lastTurnStatus", state.last_turn_status)
        if state.last_turn_error:
            merged.setdefault("lastTurnError", state.last_turn_error)
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
    def _assistant_text_from_turn_payload(turn_payload: Mapping[str, Any]) -> str:
        items = turn_payload.get("items")
        if not isinstance(items, list):
            return ""
        for item in reversed(items):
            if not isinstance(item, Mapping):
                continue
            if item.get("type") != "agentMessage":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""

    @classmethod
    def _extract_assistant_text(
        cls,
        thread_payload: Mapping[str, Any],
        *,
        vendor_turn_id: str | None = None,
    ) -> str:
        thread = thread_payload.get("thread")
        if not isinstance(thread, Mapping):
            return ""
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return ""
        if vendor_turn_id:
            turn_payload = cls._find_turn_payload(
                thread_payload,
                vendor_turn_id=vendor_turn_id,
            )
            if isinstance(turn_payload, Mapping):
                return cls._assistant_text_from_turn_payload(turn_payload)
            return ""
        for turn in reversed(turns):
            if not isinstance(turn, Mapping):
                continue
            text = cls._assistant_text_from_turn_payload(turn)
            if text:
                return text
        return ""

    @staticmethod
    def _thread_status_type(thread_payload: Mapping[str, Any]) -> str:
        thread = thread_payload.get("thread")
        if not isinstance(thread, Mapping):
            return ""
        status = thread.get("status")
        if not isinstance(status, Mapping):
            return ""
        return str(status.get("type") or "").strip().lower()

    @staticmethod
    def _thread_status_reason(thread_payload: Mapping[str, Any]) -> str | None:
        thread = thread_payload.get("thread")
        if not isinstance(thread, Mapping):
            return None
        status = thread.get("status")
        if not isinstance(status, Mapping):
            return None
        for field_name in ("reason", "message", "error"):
            value = status.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type not in {"output_text", "text"}:
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()

    @staticmethod
    def _payload_references_turn(payload: Any, vendor_turn_id: str) -> bool:
        if not vendor_turn_id:
            return False
        if isinstance(payload, Mapping):
            direct_turn_id = str(
                payload.get("turnId") or payload.get("turn_id") or ""
            ).strip()
            if direct_turn_id == vendor_turn_id:
                return True
            turn_payload = payload.get("turn")
            if isinstance(turn_payload, Mapping):
                turn_id = str(turn_payload.get("id") or "").strip()
                if turn_id == vendor_turn_id:
                    return True
            for nested_key in ("payload", "data", "delta", "item", "event"):
                if CodexManagedSessionRuntime._payload_references_turn(
                    payload.get(nested_key),
                    vendor_turn_id,
                ):
                    return True
            return False
        if isinstance(payload, list):
            return any(
                CodexManagedSessionRuntime._payload_references_turn(item, vendor_turn_id)
                for item in payload
            )
        return False

    def _allowed_rollout_path(self, path_value: str | None) -> str | None:
        normalized = self._normalized_thread_path(path_value)
        if normalized is None:
            return None
        candidate = Path(normalized)
        try:
            resolved = candidate.resolve()
            sessions_root = (self._codex_home_path / "sessions").resolve()
            resolved.relative_to(sessions_root)
        except (OSError, RuntimeError, ValueError):
            return None
        return str(resolved) if resolved.is_file() else None

    def _extract_assistant_text_from_rollout(
        self,
        vendor_thread_path: str | None,
        *,
        vendor_turn_id: str,
    ) -> str:
        rollout_path = self._allowed_rollout_path(vendor_thread_path)
        if rollout_path is None:
            return ""
        last_text = ""
        try:
            rollout_file = Path(rollout_path)
            if rollout_file.stat().st_size > _ROLLOUT_RECOVERY_MAX_BYTES:
                return ""
            with rollout_file.open(encoding="utf-8") as handle:
                for raw_line in handle:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    if not self._payload_references_turn(payload, vendor_turn_id):
                        continue
                    entry_type = str(payload.get("type") or "").strip().lower()
                    if entry_type == "response_item":
                        response_payload = payload.get("payload")
                        if not isinstance(response_payload, Mapping):
                            continue
                        if (
                            str(response_payload.get("type") or "").strip().lower()
                            != "message"
                            or str(response_payload.get("role") or "").strip().lower()
                            != "assistant"
                        ):
                            continue
                        text = self._content_text(response_payload.get("content"))
                        if text:
                            last_text = text
                    elif entry_type == "event_msg":
                        event_payload = payload.get("payload")
                        if not isinstance(event_payload, Mapping):
                            continue
                        if (
                            str(event_payload.get("type") or "").strip().lower()
                            != "agent_message"
                        ):
                            continue
                        text = str(event_payload.get("message") or "").strip()
                        if text:
                            last_text = text
        except OSError:
            return ""
        return last_text

    def _resolved_rollout_path(
        self,
        *,
        state: CodexSessionRuntimeState,
        thread_payload: Mapping[str, Any],
    ) -> str | None:
        thread = thread_payload.get("thread")
        runtime_path = None
        if isinstance(thread, Mapping):
            runtime_path = self._normalized_thread_path(thread.get("path"))
        allowed_runtime_path = self._allowed_rollout_path(runtime_path)
        if allowed_runtime_path is not None:
            state.vendor_thread_path = allowed_runtime_path
            return allowed_runtime_path
        allowed_state_path = self._allowed_rollout_path(state.vendor_thread_path)
        if allowed_state_path is not None:
            return allowed_state_path
        recovered_path = self._find_vendor_thread_path(state.vendor_thread_id)
        if recovered_path is not None:
            state.vendor_thread_path = recovered_path
        return recovered_path

    def _assistant_text_for_completed_turn(
        self,
        *,
        state: CodexSessionRuntimeState,
        thread_payload: Mapping[str, Any],
        vendor_turn_id: str,
    ) -> str:
        assistant_text = self._extract_assistant_text(
            thread_payload,
            vendor_turn_id=vendor_turn_id,
        )
        if assistant_text:
            return assistant_text
        vendor_thread_path = self._resolved_rollout_path(
            state=state,
            thread_payload=thread_payload,
        )
        return self._extract_assistant_text_from_rollout(
            vendor_thread_path,
            vendor_turn_id=vendor_turn_id,
        )

    @staticmethod
    def _find_turn_payload(
        thread_payload: Mapping[str, Any],
        *,
        vendor_turn_id: str,
    ) -> Mapping[str, Any] | None:
        thread = thread_payload.get("thread")
        if not isinstance(thread, Mapping):
            return None
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return None
        for turn in reversed(turns):
            if not isinstance(turn, Mapping):
                continue
            if str(turn.get("id") or "").strip() != vendor_turn_id:
                continue
            return turn
        return None

    def _wait_for_turn_completion(
        self,
        *,
        client: CodexAppServerRpcClient,
        vendor_thread_id: str,
        vendor_turn_id: str,
    ) -> tuple[Mapping[str, Any], tuple[str, str | None]]:
        deadline = time.monotonic() + self._turn_completion_timeout_seconds
        while True:
            thread_payload = client.request("thread/read", {"threadId": vendor_thread_id})
            turn_payload = self._find_turn_payload(
                thread_payload,
                vendor_turn_id=vendor_turn_id,
            )
            if isinstance(turn_payload, Mapping):
                outcome = self._terminal_turn_outcome(turn_payload)
                if outcome is not None:
                    return thread_payload, outcome
            else:
                thread_outcome = self._terminal_thread_outcome(thread_payload)
                if thread_outcome is not None:
                    return thread_payload, thread_outcome

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "timed out waiting for codex app-server turn completion "
                    f"after {self._turn_completion_timeout_seconds} seconds"
                )

            try:
                client.wait_for_notification(
                    None,
                    timeout_seconds=min(1.0, remaining),
                )
            except TimeoutError:
                continue

    def _find_vendor_thread_path(self, vendor_thread_id: str) -> str | None:
        sessions_root = self._codex_home_path / "sessions"
        if not sessions_root.is_dir():
            return None
        matches = sorted(sessions_root.rglob(f"*{vendor_thread_id}.jsonl"))
        if not matches:
            return None
        return str(matches[-1])

    @staticmethod
    def _normalized_thread_path(path_value: str | None) -> str | None:
        normalized = str(path_value or "").strip()
        return normalized or None

    @staticmethod
    def _existing_thread_path(path_value: str | None) -> str | None:
        normalized = CodexManagedSessionRuntime._normalized_thread_path(path_value)
        if normalized is None:
            return None
        return normalized if Path(normalized).is_file() else None

    def _resume_thread(
        self,
        *,
        client: CodexAppServerRpcClient,
        state: CodexSessionRuntimeState,
        allow_fallback_start: bool = True,
    ) -> str:
        thread_path = self._existing_thread_path(
            state.vendor_thread_path
        ) or self._find_vendor_thread_path(state.vendor_thread_id)
        params: dict[str, Any] = {"threadId": state.vendor_thread_id}
        if thread_path:
            params["path"] = thread_path
        try:
            resumed = client.request("thread/resume", params)
            thread_payload = resumed.get("thread")
        except RuntimeError as exc:
            message = str(exc)
            if "no rollout found" not in message and "thread not found" not in message:
                raise
            if not allow_fallback_start:
                raise
            started = client.request("thread/start", {"cwd": str(self._workspace_path)})
            thread_payload = started.get("thread")
        if not isinstance(thread_payload, Mapping):
            raise RuntimeError(
                "codex app-server thread/resume did not return a thread"
            )
        vendor_thread_id = str(thread_payload.get("id") or "").strip()
        if not vendor_thread_id:
            raise RuntimeError(
                "codex app-server thread/resume returned a blank thread id"
            )
        recovered_thread_path = (
            thread_path if vendor_thread_id == state.vendor_thread_id else None
        )
        state.vendor_thread_id = vendor_thread_id
        state.vendor_thread_path = self._normalized_thread_path(
            thread_payload.get("path") or recovered_thread_path
        )
        return vendor_thread_id

    @staticmethod
    def _terminal_turn_outcome(
        turn_payload: Mapping[str, Any],
    ) -> tuple[str, str | None] | None:
        raw_status = str(turn_payload.get("status") or "").strip().lower()
        error_value = turn_payload.get("error")
        error_text = str(error_value).strip() if error_value not in (None, "") else None
        if raw_status == "completed":
            return "completed", None
        if raw_status in {"failed", "error"}:
            return "failed", error_text
        if raw_status in {"interrupted", "canceled", "cancelled"}:
            return "interrupted", error_text or raw_status
        if error_text:
            return "failed", error_text
        return None

    @classmethod
    def _terminal_thread_outcome(
        cls,
        thread_payload: Mapping[str, Any],
    ) -> tuple[str, str | None] | None:
        status_type = cls._thread_status_type(thread_payload)
        if status_type == "idle":
            return "completed", None
        if status_type in {"failed", "error"}:
            return "failed", cls._thread_status_reason(thread_payload)
        if status_type in {"interrupted", "cancelled", "canceled"}:
            return (
                "interrupted",
                cls._thread_status_reason(thread_payload) or status_type,
            )
        return None

    def _finalize_turn(
        self,
        *,
        state: CodexSessionRuntimeState,
        turn_id: str,
        status: str,
        assistant_text: str | None = None,
        error_text: str | None = None,
    ) -> None:
        previous_status = state.last_turn_status
        state.active_turn_id = None
        state.last_turn_id = turn_id
        state.last_turn_status = status
        state.last_turn_error = error_text
        if status == "completed":
            state.last_assistant_text = assistant_text or None
        self._save_state(state)
        if status == "completed" and assistant_text and previous_status != "completed":
            self._append_spool("stdout", f"assistant: {assistant_text}\n")
        if status in {"failed", "interrupted"} and error_text and previous_status != status:
            self._append_spool("stderr", f"turn {status}: {error_text}\n")

    def _refresh_turn_state(
        self,
        state: CodexSessionRuntimeState,
    ) -> CodexSessionRuntimeState:
        active_turn_id = str(state.active_turn_id or "").strip()
        if not active_turn_id:
            return state

        client = self._app_server_client()
        client.initialize()
        try:
            thread_payload = client.request(
                "thread/read",
                {"threadId": state.vendor_thread_id},
            )
        except RuntimeError as exc:
            try:
                vendor_thread_id = self._resume_thread(
                    client=client,
                    state=state,
                    allow_fallback_start=False,
                )
                thread_payload = client.request("thread/read", {"threadId": vendor_thread_id})
            except RuntimeError as inner_exc:
                self._finalize_turn(
                    state=state,
                    turn_id=active_turn_id,
                    status="failed",
                    error_text=str(inner_exc),
                )
                return state

        turn_payload = self._find_turn_payload(
            thread_payload,
            vendor_turn_id=active_turn_id,
        )
        outcome = None
        if isinstance(turn_payload, Mapping):
            outcome = self._terminal_turn_outcome(turn_payload)
            if outcome is None:
                return state
        else:
            outcome = self._terminal_thread_outcome(thread_payload)
            if outcome is None:
                return state

        status, error_text = outcome
        assistant_text = ""
        if status == "completed":
            assistant_text = self._assistant_text_for_completed_turn(
                state=state,
                thread_payload=thread_payload,
                vendor_turn_id=active_turn_id,
            )
        if status == "completed" and not assistant_text:
            error_text = "codex app-server turn/completed produced no assistant output"
            self._append_spool(
                "stderr",
                (
                    "codex app-server turn completed without assistant output: "
                    f"{active_turn_id}\n"
                ),
            )
            status = "failed"
        self._finalize_turn(
            state=state,
            turn_id=active_turn_id,
            status=status,
            assistant_text=assistant_text,
            error_text=error_text,
        )
        return state

    @staticmethod
    def _handle_status_for_state(state: CodexSessionRuntimeState) -> str:
        if state.active_turn_id:
            return "busy"
        if state.last_turn_status == "failed":
            return "failed"
        if state.last_turn_status == "interrupted":
            return "interrupted"
        return "ready"

    def launch_session(
        self,
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        self._ensure_directories()
        self._seed_codex_home_from_auth_volume()
        client = self._app_server_client()
        client.initialize()
        started = client.request("thread/start", {"cwd": str(self._workspace_path)})
        thread_payload = started.get("thread")
        if not isinstance(thread_payload, Mapping):
            raise RuntimeError("codex app-server thread/start did not return a thread")
        vendor_thread_id = str(thread_payload.get("id") or "").strip()
        if not vendor_thread_id:
            raise RuntimeError("codex app-server thread/start returned a blank thread id")
        vendor_thread_path = self._normalized_thread_path(thread_payload.get("path"))

        state = CodexSessionRuntimeState(
            sessionId=request.session_id,
            sessionEpoch=request.session_epoch,
            logicalThreadId=request.thread_id,
            vendorThreadId=vendor_thread_id,
            vendorThreadPath=vendor_thread_path,
            containerId=self._container_id,
            activeTurnId=None,
            launchedAt=time.time(),
            lastControlAction="start_session",
            lastControlAt=time.time(),
        )
        self._save_state(state)
        self._append_spool(
            "stdout",
            f"session started: {request.session_id} thread={request.thread_id}\n",
        )
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
        state = self._refresh_turn_state(state)
        status = self._handle_status_for_state(state)
        return self._handle(state, status=status)

    def send_turn(
        self,
        request: SendCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        state = self._validate_locator(request)
        client = self._app_server_client()
        client.initialize()
        vendor_thread_id = self._resume_thread(client=client, state=state)

        started = client.request(
            "turn/start",
            {
                "threadId": vendor_thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": request.instructions,
                    }
                ],
            },
        )
        turn_payload = started.get("turn")
        if not isinstance(turn_payload, Mapping):
            raise RuntimeError("codex app-server turn/start did not return a turn")
        vendor_turn_id = str(turn_payload.get("id") or "").strip()
        if not vendor_turn_id:
            raise RuntimeError("codex app-server turn/start returned a blank turn id")

        state.active_turn_id = vendor_turn_id
        state.last_turn_id = vendor_turn_id
        state.last_turn_status = "running"
        state.last_turn_error = None
        state.last_control_action = "send_turn"
        state.last_control_at = time.time()
        self._save_state(state)
        self._append_spool("stdout", f"turn started: {vendor_turn_id}\n")
        try:
            thread_payload, (status, error_text) = self._wait_for_turn_completion(
                client=client,
                vendor_thread_id=vendor_thread_id,
                vendor_turn_id=vendor_turn_id,
            )
        except RuntimeError as exc:
            message = str(exc).strip()
            if message.startswith(
                "timed out waiting for codex app-server turn completion"
            ):
                return CodexManagedSessionTurnResponse(
                    sessionState=self._session_state(state),
                    turnId=vendor_turn_id,
                    status="running",
                    metadata={},
                )
            self._finalize_turn(
                state=state,
                turn_id=vendor_turn_id,
                status="failed",
                error_text=message,
            )
            return CodexManagedSessionTurnResponse(
                sessionState=self._session_state(state),
                turnId=vendor_turn_id,
                status="failed",
                metadata={"reason": message},
            )

        assistant_text = ""
        metadata: dict[str, Any] = {}
        if status == "completed":
            assistant_text = self._assistant_text_for_completed_turn(
                state=state,
                thread_payload=thread_payload,
                vendor_turn_id=vendor_turn_id,
            )
            if not assistant_text:
                error_text = "codex app-server turn/completed produced no assistant output"
                self._append_spool(
                    "stderr",
                    (
                        "codex app-server turn completed without assistant output: "
                        f"{vendor_turn_id}\n"
                    ),
                )
                status = "failed"
            else:
                metadata["assistantText"] = assistant_text
        if error_text:
            metadata["reason"] = error_text
        self._finalize_turn(
            state=state,
            turn_id=vendor_turn_id,
            status=status,
            assistant_text=assistant_text,
            error_text=error_text,
        )
        return CodexManagedSessionTurnResponse(
            sessionState=self._session_state(state),
            turnId=vendor_turn_id,
            status=status,
            metadata=metadata,
        )

    def steer_turn(
        self,
        request: SteerCodexManagedSessionTurnRequest,
    ) -> CodexManagedSessionTurnResponse:
        state = self._validate_locator(request)
        self._append_spool("stderr", f"steer not supported for turn {request.turn_id}\n")
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
        if state.active_turn_id != request.turn_id:
            return CodexManagedSessionTurnResponse(
                sessionState=self._session_state(state),
                turnId=request.turn_id,
                status="failed",
                metadata={
                    "reason": (
                        "interrupt_turn requires the active managed-session turn id"
                    )
                },
            )
        client = self._app_server_client()
        client.initialize()
        interrupt_params = {
            "threadId": state.vendor_thread_id,
            "turnId": request.turn_id,
        }
        if request.reason:
            interrupt_params["reason"] = request.reason
        client.request("turn/interrupt", interrupt_params)
        state.active_turn_id = None
        state.last_turn_id = request.turn_id
        state.last_turn_status = "interrupted"
        state.last_turn_error = request.reason or "interrupt requested"
        state.last_control_action = "interrupt_turn"
        state.last_control_at = time.time()
        self._save_state(state)
        self._append_spool("stderr", f"interrupt requested: {request.turn_id}\n")
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
        vendor_thread_path = self._existing_thread_path(thread_payload.get("path"))

        state.session_epoch += 1
        state.logical_thread_id = request.new_thread_id
        state.vendor_thread_id = vendor_thread_id
        state.vendor_thread_path = vendor_thread_path
        state.active_turn_id = None
        state.last_control_action = "clear_session"
        state.last_control_at = time.time()
        self._save_state(state)
        self._append_spool(
            "stdout",
            f"session cleared: epoch={state.session_epoch} thread={state.logical_thread_id}\n",
        )
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
        self._append_spool("stdout", f"session terminated: {request.session_id}\n")
        return self._handle(state, status="terminated")

    def fetch_session_summary(
        self,
        request: FetchCodexManagedSessionSummaryRequest,
    ) -> CodexManagedSessionSummary:
        state = self._validate_locator(request)
        state = self._refresh_turn_state(state)
        return CodexManagedSessionSummary(
            sessionState=self._session_state(state),
            latestSummaryRef=None,
            latestCheckpointRef=None,
            latestControlEventRef=None,
            metadata={
                "lastAssistantText": state.last_assistant_text,
                "lastTurnId": state.last_turn_id,
                "lastTurnStatus": state.last_turn_status,
                "lastTurnError": state.last_turn_error,
            },
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


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _require_writable_directory(
    path_value: str,
    env_name: str,
    *,
    create: bool,
) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError(f"{env_name} must be an absolute path: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        raise RuntimeError(f"{env_name} must exist: {path}")
    if not path.is_dir():
        raise RuntimeError(f"{env_name} must be a directory: {path}")
    if not os.access(path, os.W_OK):
        raise RuntimeError(f"{env_name} must be writable: {path}")
    return str(path)


def _validated_runtime_environment() -> dict[str, str]:
    if shutil.which("codex") is None:
        raise RuntimeError("codex is required on PATH")

    workspace_path = _require_writable_directory(
        _require_env("MOONMIND_SESSION_WORKSPACE_PATH"),
        "MOONMIND_SESSION_WORKSPACE_PATH",
        create=False,
    )
    session_workspace_path = _require_writable_directory(
        _require_env("MOONMIND_SESSION_WORKSPACE_STATE_PATH"),
        "MOONMIND_SESSION_WORKSPACE_STATE_PATH",
        create=True,
    )
    artifact_spool_path = _require_writable_directory(
        _require_env("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"),
        "MOONMIND_SESSION_ARTIFACT_SPOOL_PATH",
        create=True,
    )
    codex_home_path = _require_writable_directory(
        _require_env("MOONMIND_SESSION_CODEX_HOME_PATH"),
        "MOONMIND_SESSION_CODEX_HOME_PATH",
        create=True,
    )
    image_ref = _require_env("MOONMIND_SESSION_IMAGE_REF")

    return {
        "workspace_path": workspace_path,
        "session_workspace_path": session_workspace_path,
        "artifact_spool_path": artifact_spool_path,
        "codex_home_path": codex_home_path,
        "image_ref": image_ref,
    }


def _runtime_from_environment() -> CodexManagedSessionRuntime:
    env = _validated_runtime_environment()
    workspace_path = env["workspace_path"]
    session_workspace_path = env["session_workspace_path"]
    artifact_spool_path = env["artifact_spool_path"]
    codex_home_path = env["codex_home_path"]
    image_ref = env["image_ref"]
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
    timeout_seconds = float(
        os.environ.get(
            "MOONMIND_SESSION_TURN_COMPLETION_TIMEOUT_SECONDS",
            str(_DEFAULT_TURN_COMPLETION_TIMEOUT_SECONDS),
        )
    )
    auth_volume_path = str(os.environ.get("MANAGED_AUTH_VOLUME_PATH") or "").strip() or None
    return CodexManagedSessionRuntime(
        workspace_path=workspace_path,
        session_workspace_path=session_workspace_path,
        artifact_spool_path=artifact_spool_path,
        codex_home_path=codex_home_path,
        auth_volume_path=auth_volume_path,
        image_ref=image_ref,
        control_url=control_url,
        container_id=container_id,
        turn_completion_timeout_seconds=timeout_seconds,
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
    _validated_runtime_environment()
    return _emit_json({"ready": True})


def _run_serve() -> int:
    _validated_runtime_environment()
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
