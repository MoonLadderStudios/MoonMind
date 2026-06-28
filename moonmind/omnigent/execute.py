"""Run one Omnigent streaming execution inside a Temporal activity."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from temporalio import activity

from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.skills.artifact_store import FileArtifactStore

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|api[_-]?key|authorization|cookie|jwt|credential)",
    re.IGNORECASE,
)
_GITHUB_PR_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+"
)
_TERMINAL_STATUSES = {"completed", "failed", "canceled", "cancelled", "timed_out"}
_SUCCESS_STATUSES = {"completed", "succeeded", "success"}
_ARTIFACT_ROOT_ENV = "MOONMIND_OMNIGENT_ARTIFACT_ROOT"
_DEFAULT_ARTIFACT_ROOT = "/work/agent_jobs/omnigent_artifacts"


class OmnigentClientError(RuntimeError):
    """Raised when Omnigent transport returns a failed or malformed response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass(frozen=True, slots=True)
class OmnigentArtifact:
    name: str
    ref: str
    content_type: str


class _ArtifactRecorder:
    def __init__(self, *, root: Path) -> None:
        self._store = FileArtifactStore(root)
        self.artifacts: list[OmnigentArtifact] = []

    def put_json(
        self,
        name: str,
        payload: Mapping[str, Any] | Sequence[Any],
        *,
        link_type: str,
    ) -> str:
        artifact = self._store.put_json(
            _redact(payload),
            metadata={
                "name": name,
                "producer": "activity:integration.omnigent.execute",
                "link_type": link_type,
                "source_issue": "MM-993",
                "source_issue_trace": "MM-993",
            },
        )
        self.artifacts.append(
            OmnigentArtifact(
                name=name,
                ref=artifact.artifact_ref,
                content_type="application/json",
            )
        )
        return artifact.artifact_ref

    def put_text(
        self,
        name: str,
        payload: str,
        *,
        content_type: str,
        link_type: str,
    ) -> str:
        artifact = self._store.put_bytes(
            payload.encode("utf-8"),
            content_type=content_type,
            metadata={
                "name": name,
                "producer": "activity:integration.omnigent.execute",
                "link_type": link_type,
                "source_issue": "MM-993",
                "source_issue_trace": "MM-993",
            },
        )
        self.artifacts.append(
            OmnigentArtifact(
                name=name,
                ref=artifact.artifact_ref,
                content_type=content_type,
            )
        )
        return artifact.artifact_ref


class OmnigentHttpClient:
    """Thin activity-side Omnigent HTTP/SSE transport client."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(60.0, read=None),
        )

    async def __aenter__(self) -> "OmnigentHttpClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_agents(self) -> Any:
        return await self._request_json("GET", "/api/agents")

    async def create_session(self, payload: Mapping[str, Any]) -> Any:
        return await self._request_json("POST", "/v1/sessions", json_payload=payload)

    async def get_session(self, session_id: str) -> Any:
        return await self._request_json(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}",
        )

    async def post_event(self, session_id: str, payload: Mapping[str, Any]) -> Any:
        return await self._request_json(
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/events",
            json_payload=payload,
        )

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        path = f"/v1/sessions/{quote(session_id, safe='')}/stream"
        async with self._client.stream("GET", path) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise OmnigentClientError(
                    f"Omnigent stream failed with HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_body=await response.aread(),
                )
            async for line in response.aiter_lines():
                event = _parse_sse_line(line)
                if event is not None:
                    yield event

    async def list_changed_files(self, session_id: str) -> Any:
        return await self._request_json(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}/resources/environments/default/changes",
        )

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        response = await self._client.get(
            f"/v1/sessions/{quote(session_id, safe='')}/resources/environments/default/filesystem/"
            f"{quote(path, safe='')}"
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise OmnigentClientError(
                f"Omnigent workspace file fetch failed with HTTP {response.status_code}",
                status_code=response.status_code,
                response_body=_response_body(response),
            )
        return response.content

    async def list_session_files(self, session_id: str) -> Any:
        return await self._request_json(
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}/resources/files",
        )

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        response = await self._client.get(
            f"/v1/sessions/{quote(session_id, safe='')}/resources/files/"
            f"{quote(file_id, safe='')}/content"
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise OmnigentClientError(
                f"Omnigent session file fetch failed with HTTP {response.status_code}",
                status_code=response.status_code,
                response_body=_response_body(response),
            )
        return response.content

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
    ) -> Any:
        response = await self._client.request(method, path, json=json_payload)
        if response.status_code < 200 or response.status_code >= 300:
            raise OmnigentClientError(
                f"Omnigent request {method} {path} failed with HTTP {response.status_code}",
                status_code=response.status_code,
                response_body=_response_body(response),
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise OmnigentClientError(
                f"Omnigent request {method} {path} returned non-JSON content",
                status_code=response.status_code,
                response_body=response.text[:4096],
            ) from exc


async def run_omnigent_execution(request: AgentExecutionRequest) -> AgentRunResult:
    """Execute an Omnigent run via the streaming activity boundary."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    recorder = _build_recorder(request)
    diagnostics: dict[str, Any] = {
        "provider": "omnigent",
        "endpointRef": "default",
        "sourceIssue": "MM-993",
        "sourceIssueTrace": "MM-993",
        "artifactNames": [],
        "patch": {
            "status": "patch_unavailable",
            "reason": "no_supported_patch_source",
        },
        "childSessionIds": [],
        "githubPrUrls": [],
    }
    client = OmnigentHttpClient(
        base_url=resolved_server_url(),
        token=os.environ.get("OMNIGENT_API_TOKEN", "").strip() or None,
    )

    try:
        async with client:
            result = await _execute_with_client(
                request=request,
                client=client,
                recorder=recorder,
                diagnostics=diagnostics,
            )
    except OmnigentClientError as exc:
        diagnostics["terminalStatus"] = "failed"
        diagnostics["error"] = {
            "message": str(exc),
            "statusCode": exc.status_code,
            "responseBody": _redact(exc.response_body),
        }
        diagnostics_ref = _write_diagnostics(recorder, diagnostics)
        return AgentRunResult(
            outputRefs=[diagnostics_ref],
            summary=str(exc)[:4096],
            diagnosticsRef=diagnostics_ref,
            failureClass="integration_error",
            providerErrorCode=str(exc.status_code or "omnigent_http_error"),
            metadata={
                "providerName": "omnigent",
                "normalizedStatus": "failed",
                "diagnosticsRef": diagnostics_ref,
                "sourceIssue": "MM-993",
                "sourceIssueTrace": "MM-993",
            },
        )

    return result


async def _execute_with_client(
    *,
    request: AgentExecutionRequest,
    client: OmnigentHttpClient,
    recorder: _ArtifactRecorder,
    diagnostics: dict[str, Any],
) -> AgentRunResult:
    session_request = _build_session_request(request)
    session_request_ref = recorder.put_json(
        "input.omnigent.session_create.request.json",
        session_request,
        link_type="input.omnigent.session_create.request",
    )
    session_response = await client.create_session(session_request)
    session_response_ref = recorder.put_json(
        "input.omnigent.session_create.response.json",
        _ensure_mapping(session_response, "session_create response"),
        link_type="input.omnigent.session_create.response",
    )
    session_id = _extract_text(session_response, "id", "session_id", "sessionId")
    if not session_id:
        raise OmnigentClientError(
            "Omnigent session create response did not include a session id"
        )
    diagnostics["omnigentSessionId"] = session_id
    diagnostics["sessionCreateRequestRef"] = session_request_ref
    diagnostics["sessionCreateResponseRef"] = session_response_ref

    initial_snapshot = await client.get_session(session_id)
    initial_snapshot_ref = recorder.put_json(
        "runtime.omnigent.snapshot.initial.json",
        _ensure_mapping(initial_snapshot, "initial snapshot"),
        link_type="runtime.omnigent.snapshot.initial",
    )
    _heartbeat({"phase": "initial_snapshot", "omnigentSessionId": session_id})

    first_message = _build_first_message(request)
    first_message_ref = recorder.put_json(
        "input.omnigent.first_message.request.json",
        first_message,
        link_type="input.omnigent.first_message.request",
    )
    first_response = await client.post_event(session_id, first_message)
    first_response_ref = recorder.put_json(
        "input.omnigent.first_message.response.json",
        _ensure_mapping(first_response, "first message response"),
        link_type="input.omnigent.first_message.response",
    )
    diagnostics["firstMessageRequestRef"] = first_message_ref
    diagnostics["firstMessageResponseRef"] = first_response_ref

    raw_events: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    transcript_rows: list[dict[str, Any]] = []
    child_session_ids: list[str] = []
    final_response_parts: list[str] = []
    terminal_status: str | None = None

    async for event in client.stream_events(session_id):
        raw_events.append(event)
        normalized = _normalize_sse_event(event, session_id=session_id)
        normalized_events.append(normalized)
        transcript = _transcript_row(normalized)
        if transcript:
            transcript_rows.append(transcript)
        text_delta = _text_delta(normalized)
        if text_delta:
            final_response_parts.append(text_delta)
        child_id = _child_session_id(normalized)
        if child_id and child_id not in child_session_ids:
            child_session_ids.append(child_id)
        status = _status_from_event(normalized)
        if status:
            terminal_status = status
        if len(raw_events) % 8 == 0:
            _heartbeat(
                {
                    "phase": "stream",
                    "omnigentSessionId": session_id,
                    "events": len(raw_events),
                }
            )
        if terminal_status in _TERMINAL_STATUSES:
            break

    raw_sse_ref = recorder.put_text(
        "runtime.omnigent.sse.raw.jsonl",
        _jsonl(raw_events),
        content_type="application/jsonl",
        link_type="runtime.merged_logs",
    )
    normalized_sse_ref = recorder.put_text(
        "runtime.omnigent.sse.normalized.jsonl",
        _jsonl(normalized_events),
        content_type="application/jsonl",
        link_type="output.logs",
    )
    transcript_ref = recorder.put_text(
        "output.omnigent.transcript.jsonl",
        _jsonl(transcript_rows),
        content_type="application/jsonl",
        link_type="output.agent_result",
    )
    child_sessions_ref = recorder.put_text(
        "runtime.omnigent.child_sessions.jsonl",
        _jsonl({"omnigentSessionId": child_id} for child_id in child_session_ids),
        content_type="application/jsonl",
        link_type="runtime.omnigent.child_sessions",
    )

    final_snapshot = await client.get_session(session_id)
    final_snapshot_ref = recorder.put_json(
        "output.omnigent.snapshot.final.json",
        _ensure_mapping(final_snapshot, "final snapshot"),
        link_type="output.omnigent.snapshot.final",
    )
    terminal_status = terminal_status or _snapshot_status(final_snapshot) or "completed"
    diagnostics["terminalStatus"] = terminal_status
    diagnostics["childSessionIds"] = child_session_ids
    diagnostics["childSessionsRef"] = child_sessions_ref

    final_response = _final_response_markdown(final_response_parts, final_snapshot)
    final_response_ref = recorder.put_text(
        "output.omnigent.final_response.md",
        final_response,
        content_type="text/markdown",
        link_type="output.primary",
    )
    github_pr_urls = _detect_pr_urls(
        final_response,
        _json_dumps(final_snapshot),
        _jsonl(transcript_rows),
    )
    diagnostics["githubPrUrls"] = github_pr_urls
    github_pr_ref = recorder.put_json(
        "output.github.pr.metadata.json",
        {"githubPrUrls": github_pr_urls},
        link_type="output.github.pr.metadata",
    )

    await _harvest_resources(
        client=client,
        session_id=session_id,
        recorder=recorder,
        diagnostics=diagnostics,
    )
    patch_unavailable_ref = recorder.put_json(
        "output.workspace.patch_unavailable.json",
        diagnostics["patch"],
        link_type="output.patch",
    )
    capture_manifest_ref = recorder.put_json(
        "output.omnigent.capture_manifest.json",
        {
            "schemaVersion": "v1",
            "sourceIssue": "MM-993",
            "sourceIssueTrace": "MM-993",
            "artifacts": [
                {
                    "name": artifact.name,
                    "ref": artifact.ref,
                    "contentType": artifact.content_type,
                }
                for artifact in recorder.artifacts
            ],
        },
        link_type="step.evidence",
    )
    diagnostics["captureManifestRef"] = capture_manifest_ref
    diagnostics["artifactNames"] = [artifact.name for artifact in recorder.artifacts]
    diagnostics_ref = _write_diagnostics(recorder, diagnostics)

    output_refs = list(
        dict.fromkeys(
            [artifact.ref for artifact in recorder.artifacts] + [diagnostics_ref]
        )
    )
    metadata: dict[str, Any] = {
        "providerName": "omnigent",
        "omnigentSessionId": session_id,
        "captureManifestRef": capture_manifest_ref,
        "diagnosticsRef": diagnostics_ref,
        "initialSnapshotRef": initial_snapshot_ref,
        "finalResponseRef": final_response_ref,
        "finalSnapshotRef": final_snapshot_ref,
        "transcriptRef": transcript_ref,
        "normalizedSseRef": normalized_sse_ref,
        "rawSseRef": raw_sse_ref,
        "patchUnavailableRef": patch_unavailable_ref,
        "githubPrMetadataRef": github_pr_ref,
        "githubPrUrls": github_pr_urls,
        "childSessionIds": child_session_ids,
        "childSessionsRef": child_sessions_ref,
        "sourceIssue": "MM-993",
        "sourceIssueTrace": "MM-993",
    }
    if github_pr_urls:
        metadata["githubPrUrl"] = github_pr_urls[0]
    if terminal_status not in _SUCCESS_STATUSES:
        return AgentRunResult(
            outputRefs=output_refs,
            summary=f"Omnigent session ended with status {terminal_status}",
            diagnosticsRef=diagnostics_ref,
            failureClass="execution_error",
            providerErrorCode=f"omnigent_{terminal_status}",
            metadata={**metadata, "normalizedStatus": "failed"},
        )
    return AgentRunResult(
        outputRefs=output_refs,
        summary=_summary_from_final_response(final_response),
        diagnosticsRef=diagnostics_ref,
        metadata={**metadata, "normalizedStatus": "completed"},
    )


async def _harvest_resources(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    recorder: _ArtifactRecorder,
    diagnostics: dict[str, Any],
) -> list[str]:
    refs: list[str] = []
    try:
        changed_files = await client.list_changed_files(session_id)
    except OmnigentClientError as exc:
        diagnostics["changedFilesError"] = _client_error_payload(exc)
        changed_files = []
    changed_files_payload = _normalize_index_payload(changed_files)
    refs.append(
        recorder.put_json(
            "output.workspace.changed_files.index.json",
            changed_files_payload,
            link_type="output.workspace.changed_files",
        )
    )

    workspace_manifest: list[dict[str, Any]] = []
    for path in _changed_file_paths(changed_files_payload):
        try:
            content = await client.get_workspace_file(session_id, path)
        except OmnigentClientError as exc:
            workspace_manifest.append(
                {"path": path, "error": _client_error_payload(exc)}
            )
            continue
        safe_path = _safe_artifact_path(path)
        ref = recorder.put_text(
            f"output.workspace.files/{safe_path}.current",
            content.decode("utf-8", errors="replace"),
            content_type="application/octet-stream",
            link_type="output.workspace.file",
        )
        workspace_manifest.append({"path": path, "ref": ref, "bytes": len(content)})
        refs.append(ref)
    refs.append(
        recorder.put_json(
            "output.workspace.manifest.json",
            {"files": workspace_manifest},
            link_type="output.workspace.manifest",
        )
    )

    try:
        session_files = await client.list_session_files(session_id)
    except OmnigentClientError as exc:
        diagnostics["sessionFilesError"] = _client_error_payload(exc)
        session_files = []
    session_files_payload = _normalize_index_payload(session_files)
    refs.append(
        recorder.put_json(
            "output.omnigent.session_files.index.json",
            session_files_payload,
            link_type="output.omnigent.session_files.index",
        )
    )
    for item in _session_file_items(session_files_payload):
        file_id = _extract_text(item, "id", "file_id", "fileId")
        if not file_id:
            continue
        filename = _extract_text(item, "filename", "name") or file_id
        refs.append(
            recorder.put_json(
                "output.omnigent.session_files/"
                f"{_safe_artifact_path(file_id)}/metadata.json",
                item,
                link_type="output.omnigent.session_file.metadata",
            )
        )
        try:
            content = await client.get_session_file_content(session_id, file_id)
        except OmnigentClientError as exc:
            diagnostics.setdefault("sessionFileContentErrors", []).append(
                {"fileId": file_id, "error": _client_error_payload(exc)}
            )
            continue
        refs.append(
            recorder.put_text(
                "output.omnigent.session_files/"
                f"{_safe_artifact_path(file_id)}/{_safe_artifact_path(filename)}",
                content.decode("utf-8", errors="replace"),
                content_type="application/octet-stream",
                link_type="output.omnigent.session_file.content",
            )
        )
    return refs


def _build_session_request(request: AgentExecutionRequest) -> dict[str, Any]:
    params = request.parameters or {}
    omnigent = (
        params.get("omnigent") if isinstance(params.get("omnigent"), Mapping) else {}
    )
    session = (
        omnigent.get("session")
        if isinstance(omnigent.get("session"), Mapping)
        else {}
    )
    agent = (
        omnigent.get("agent") if isinstance(omnigent.get("agent"), Mapping) else {}
    )
    workspace_spec = request.workspace_spec or {}
    host_type = str(session.get("hostType") or session.get("host_type") or "managed")
    workspace = _extract_text(session, "workspace") or _workspace_from_spec(
        workspace_spec
    )
    payload: dict[str, Any] = {
        "agent_id": _extract_text(agent, "agentId", "agent_id") or request.agent_id,
        "title": _extract_text(params, "title", "summary") or "MoonMind Omnigent run",
        "labels": {
            "moonmind.correlation_id": request.correlation_id,
            "moonmind.idempotency_key": request.idempotency_key,
            "moonmind.source_issue": "MM-993",
            "moonmind.source_issue_trace": "MM-993",
        },
        "host_type": host_type,
        "workspace": workspace,
    }
    host_id = _extract_text(session, "hostId", "host_id")
    if host_type == "external" and host_id:
        payload["host_id"] = host_id
    for source, target in (
        ("modelOverride", "model_override"),
        ("reasoningEffort", "reasoning_effort"),
        ("terminalLaunchArgs", "terminal_launch_args"),
    ):
        value = session.get(source) or session.get(target)
        if value is not None:
            payload[target] = value
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _build_first_message(request: AgentExecutionRequest) -> dict[str, Any]:
    text = _prompt_text(request)
    data = {"role": "user", "content": [{"type": "input_text", "text": text}]}
    digest = hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()
    marker = (
        "\n\nMoonMind-Omnigent-Run:\n"
        f"  correlation_id: {request.correlation_id}\n"
        f"  idempotency_key: {request.idempotency_key}\n"
        f"  first_message_digest: {digest}\n"
    )
    data["content"][0]["text"] = f"{text.rstrip()}{marker}"
    return {"type": "message", "data": data}


def _prompt_text(request: AgentExecutionRequest) -> str:
    params = request.parameters or {}
    omnigent = (
        params.get("omnigent") if isinstance(params.get("omnigent"), Mapping) else {}
    )
    prompt = (
        omnigent.get("prompt") if isinstance(omnigent.get("prompt"), Mapping) else {}
    )
    for value in (
        prompt.get("text"),
        params.get("description"),
        request.runtime_command.instruction_body if request.runtime_command else None,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    parts = ["Execute the MoonMind task with Omnigent."]
    if request.instruction_ref:
        parts.append(f"Instruction artifact: {request.instruction_ref}")
    if request.input_refs:
        parts.append("Input artifacts: " + ", ".join(request.input_refs))
    if request.workspace_spec:
        parts.append("Workspace spec: " + _json_dumps(request.workspace_spec))
    return "\n".join(parts)


def _build_recorder(request: AgentExecutionRequest) -> _ArtifactRecorder:
    root = Path(os.environ.get(_ARTIFACT_ROOT_ENV, _DEFAULT_ARTIFACT_ROOT))
    correlation = _safe_artifact_path(request.correlation_id)
    idem = _safe_artifact_path(request.idempotency_key)
    return _ArtifactRecorder(root=root / correlation / idem)


def _write_diagnostics(
    recorder: _ArtifactRecorder,
    diagnostics: Mapping[str, Any],
) -> str:
    return recorder.put_json(
        "runtime.omnigent.diagnostics.json",
        diagnostics,
        link_type="runtime.diagnostics",
    )


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        stripped = stripped[5:].strip()
    if stripped == "[DONE]":
        return {"type": "done", "data": {}}
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise OmnigentClientError(f"Malformed Omnigent SSE JSON frame: {exc}") from exc
    if not isinstance(payload, dict):
        raise OmnigentClientError("Malformed Omnigent SSE frame: expected JSON object")
    return payload


def _normalize_sse_event(event: Mapping[str, Any], *, session_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": "v1",
        "capturedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "eventType": str(event.get("type") or event.get("event") or "unknown"),
        "itemId": event.get("item_id") or event.get("itemId"),
        "responseId": event.get("response_id") or event.get("responseId"),
        "payload": _redact(dict(event)),
        "redaction": {"applied": True, "rules": ["secret-key-recursive"]},
    }


def _transcript_row(event: Mapping[str, Any]) -> dict[str, Any] | None:
    text = _text_delta(event)
    if not text:
        return None
    return {
        "schemaVersion": "v1",
        "capturedAt": event.get("capturedAt"),
        "role": "assistant",
        "text": text,
        "eventType": event.get("eventType"),
    }


def _text_delta(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    for value in (
        payload.get("delta"),
        payload.get("text"),
        data.get("delta"),
        data.get("text"),
        data.get("content"),
    ):
        if isinstance(value, str):
            return value
    return ""


def _child_session_id(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    for key in ("child_session_id", "childSessionId", "session_id", "sessionId"):
        value = payload.get(key)
        if (
            isinstance(value, str)
            and value.strip()
            and "child" in str(event.get("eventType", "")).lower()
        ):
            return value.strip()
    return ""


def _status_from_event(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    for source in (payload, data):
        status = source.get("status")
        if isinstance(status, str) and status.strip().lower() in _TERMINAL_STATUSES:
            return status.strip().lower()
    if str(event.get("eventType") or "").lower() in {
        "done",
        "completed",
        "session.completed",
    }:
        return "completed"
    return ""


def _snapshot_status(snapshot: Any) -> str:
    if isinstance(snapshot, Mapping):
        status = _extract_text(snapshot, "status", "state")
        if status:
            return status.lower()
    return ""


def _final_response_markdown(parts: Sequence[str], final_snapshot: Any) -> str:
    text = "".join(parts).strip()
    if not text and isinstance(final_snapshot, Mapping):
        text = _extract_text(
            final_snapshot,
            "final_response",
            "finalResponse",
            "summary",
            "result",
        )
    return (
        text or "Omnigent session completed without a textual final response."
    ).rstrip() + "\n"


def _summary_from_final_response(final_response: str) -> str:
    compact = " ".join(final_response.strip().split())
    return compact[:4096] or "Omnigent session completed."


def _detect_pr_urls(*texts: str) -> list[str]:
    urls: list[str] = []
    for text in texts:
        for url in _GITHUB_PR_URL_RE.findall(text):
            if url not in urls:
                urls.append(url)
    return urls


def _normalize_index_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, Sequence) and not isinstance(
        payload,
        (str, bytes, bytearray),
    ):
        return {"items": list(payload)}
    return {"items": []}


def _changed_file_paths(payload: Mapping[str, Any]) -> list[str]:
    raw_items = (
        payload.get("items") or payload.get("files") or payload.get("changes") or []
    )
    paths: list[str] = []
    if isinstance(raw_items, Sequence) and not isinstance(
        raw_items,
        (str, bytes, bytearray),
    ):
        for item in raw_items:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
            elif isinstance(item, Mapping):
                path = _extract_text(item, "path", "file", "filename")
                if path:
                    paths.append(path)
    return paths


def _session_file_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("items") or payload.get("files") or []
    if not isinstance(raw_items, Sequence) or isinstance(
        raw_items,
        (str, bytes, bytearray),
    ):
        return []
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _workspace_from_spec(workspace_spec: Mapping[str, Any]) -> str:
    for key in ("repository", "repo", "workspace", "url"):
        value = workspace_spec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _client_error_payload(exc: OmnigentClientError) -> dict[str, Any]:
    return {
        "message": str(exc),
        "statusCode": exc.status_code,
        "responseBody": _redact(exc.response_body),
    }


def _ensure_mapping(payload: Any, label: str) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    raise OmnigentClientError(f"Omnigent {label} was not a JSON object")


def _extract_text(source: Any, *keys: str) -> str:
    if not isinstance(source, Mapping):
        return ""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            redacted[key_text] = (
                "[REDACTED]" if _SECRET_KEY_RE.search(key_text) else _redact(child)
            )
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:4096]
    return value


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:4096]


def _safe_artifact_path(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._/-]+", "_", value.strip()).strip("/.")
    safe = safe.replace("..", "_")
    return safe or "unnamed"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_dumps(value: Any) -> str:
    return json.dumps(_redact(value), sort_keys=True, ensure_ascii=False)


def _jsonl(rows: Any) -> str:
    return "".join(_json_dumps(row) + "\n" for row in rows)


def _heartbeat(payload: Mapping[str, Any]) -> None:
    try:
        activity.heartbeat(dict(payload))
    except RuntimeError:
        return


__all__ = ["OmnigentHttpClient", "run_omnigent_execution"]
