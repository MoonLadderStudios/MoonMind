"""Run one Omnigent streaming-gateway execution for MM-1059."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from re import sub
from typing import Any

import httpx
from temporalio import activity

from moonmind.omnigent.store import (
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentDigestMismatchError,
    OmnigentRunStore,
)
from moonmind.omnigent.settings import (
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAdapterError,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
    resolve_omnigent_target,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "timed_out",
    "timeout",
}
_NON_TERMINAL_STATUSES = {
    "created",
    "launching",
    "provisioning",
    "running",
    "waiting",
    "idle",
}
_MAX_OMNIGENT_HARVEST_ITEMS = 100

_logger = logging.getLogger(__name__)


class OmnigentContractError(RuntimeError):
    """Raised when Omnigent emits an unsupported adapter contract value."""


class OmnigentSessionStillRunningError(OmnigentClientError):
    """Raised when the stream ends while the provider session is still active."""


class OmnigentArtifactError(RuntimeError):
    """Raised when Omnigent artifact evidence cannot be read or written."""


@dataclass(slots=True)
class OmnigentCaptureBundle:
    """MoonMind artifact refs captured for one Omnigent session."""

    output_refs: list[str] = field(default_factory=list)
    diagnostics_ref: str = ""
    capture_manifest_ref: str = ""
    external_state_ref: str = ""
    metadata_refs: dict[str, str] = field(default_factory=dict)


class OmnigentArtifactGateway:
    """Minimal artifact boundary needed by the Omnigent activity."""

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        raise NotImplementedError

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        raise NotImplementedError

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        raise NotImplementedError

    async def read_text(self, artifact_ref: str) -> str:
        raise NotImplementedError


class LocalOmnigentArtifactGateway(OmnigentArtifactGateway):
    """Local MoonMind artifact gateway for Omnigent evidence capture."""

    def __init__(
        self,
        *,
        root: str | Path = "var/artifacts/omnigent",
        readable_refs: dict[str, str] | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._readable_refs = dict(readable_refs or {})

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        data = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return await self.write_text(
            request=request,
            name=name,
            payload=f"{data}\n",
            link_type=link_type,
            content_type="application/json",
        )

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        return await self.write_bytes(
            request=request,
            name=name,
            payload=payload.encode("utf-8"),
            link_type=link_type,
            content_type=content_type,
        )

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe_correlation = _safe_artifact_segment(request.correlation_id)
        safe_name = _safe_artifact_name(name)
        path = (self._root / safe_correlation / safe_name).resolve()
        if not path.is_relative_to(self._root):
            raise OmnigentArtifactError("Omnigent artifact path escapes artifact root")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        metadata_path = path.with_suffix(f"{path.suffix}.metadata.json")
        metadata_path.write_text(
            json.dumps(
                {
                    "contentType": content_type,
                    "linkType": link_type,
                    "sha256": digest,
                    "sizeBytes": len(payload),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return f"artifact://omnigent/{safe_correlation}/{safe_name}"

    async def read_text(self, artifact_ref: str) -> str:
        if artifact_ref in self._readable_refs:
            return self._readable_refs[artifact_ref]
        prefix = "artifact://omnigent/"
        if artifact_ref.startswith(prefix):
            relative = artifact_ref[len(prefix) :]
            path = (self._root / relative).resolve()
            if not path.is_relative_to(self._root):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if path.is_file():
                return path.read_text(encoding="utf-8")
        raise OmnigentArtifactError(f"Unable to dereference artifact ref: {artifact_ref}")


def _safe_artifact_segment(value: object) -> str:
    text = sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    if text in {".", ".."}:
        return "segment"
    return text[:120] or "run"


def _safe_artifact_name(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    parts = [_safe_artifact_segment(part) for part in text.split("/") if part.strip()]
    return "/".join(parts) or "artifact"


async def _capture_artifact_json(
    gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    refs: dict[str, str],
    *,
    key: str,
    name: str,
    payload: Any,
    link_type: str,
) -> str:
    ref = await gateway.write_json(
        request=request,
        name=name,
        payload=payload,
        link_type=link_type,
    )
    refs[key] = ref
    return ref


def _compact_summary(value: object | None, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:4096]


def _session_id(payload: dict[str, Any]) -> str:
    raw = payload.get("id") or payload.get("session_id") or payload.get("sessionId")
    session_id = str(raw or "").strip()
    if not session_id:
        raise OmnigentContractError("Omnigent session creation response missing session id")
    return session_id


def _agent_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items") or payload.get("agents") or payload.get("data") or []
    if isinstance(items, dict):
        items = list(items.values())
    elif not isinstance(items, list):
        items = []
    return [item for item in items if isinstance(item, dict)]


def _resolve_agent_id(*, agents_payload: dict[str, Any], requested_name: str | None) -> str:
    items = _agent_items(agents_payload)
    if not items:
        raise OmnigentContractError("Omnigent agent target could not be resolved")
    if requested_name:
        for item in items:
            if str(item.get("name") or "").strip() == requested_name:
                raw = item.get("id") or item.get("agent_id") or item.get("agentId")
                if raw:
                    return str(raw)
        raise OmnigentContractError(
            f"Requested Omnigent agent name '{requested_name}' could not be resolved"
        )
    raw = items[0].get("id") or items[0].get("agent_id") or items[0].get("agentId")
    if not raw:
        raise OmnigentContractError("Omnigent agent target is missing an id")
    return str(raw)


def _has_active_elicitation(payload: dict[str, Any]) -> bool:
    pending = payload.get("pending_inputs") or payload.get("pendingInputs") or []
    if isinstance(pending, list) and pending:
        return True
    return bool(payload.get("elicitation") or payload.get("elicitation_request"))


def normalize_omnigent_observation(payload: dict[str, Any]) -> str | None:
    """Normalize Omnigent observations; unknown values are contract errors."""

    event_type = str(payload.get("type") or "").strip()
    if event_type in {"stream.done"}:
        return None
    if event_type in {"response.completed", "completed"}:
        return "completed"
    if event_type in {"response.failed", "failed"}:
        return "failed"
    if event_type in {"response.elicitation_request", "elicitation_request"}:
        return "awaiting_approval"
    if event_type.startswith("response.") or event_type.startswith("session."):
        known_prefixes = (
            "response.output",
            "response.delta",
            "session.child",
            "session.input",
            "session.item",
        )
        if not event_type.startswith(known_prefixes):
            raise OmnigentContractError(f"Unsupported Omnigent event type: {event_type}")

    status = payload.get("status")
    session = payload.get("session")
    if isinstance(session, dict) and status is None:
        status = session.get("status")
    response = payload.get("response")
    if isinstance(response, dict) and status is None:
        status = response.get("status")
    data = payload.get("data")
    if isinstance(data, dict) and status is None:
        data_response = data.get("response")
        if isinstance(data_response, dict):
            status = data_response.get("status")
    if status is None:
        return None

    raw = str(status).strip().lower()
    if raw in {"cancelled", "timeout"}:
        return {"cancelled": "canceled", "timeout": "timed_out"}[raw]
    if raw in _TERMINAL_STATUSES:
        return raw
    if raw == "waiting":
        return (
            "awaiting_approval"
            if _has_active_elicitation(payload)
            else "intervention_requested"
        )
    if raw in _NON_TERMINAL_STATUSES:
        return raw
    raise OmnigentContractError(f"Unsupported Omnigent status: {raw}")


def _failure_class_for(status: str) -> str | None:
    if status == "completed":
        return None
    if status == "failed":
        return "execution_error"
    if status in {"canceled", "timed_out"}:
        return "system_error"
    return "integration_error"


def _session_options(omni: dict[str, Any]) -> dict[str, Any]:
    session = omni.get("session")
    return session if isinstance(session, dict) else {}


async def _build_omnigent_first_message(
    *,
    request: AgentExecutionRequest,
    prompt: dict[str, Any],
    artifact_gateway: OmnigentArtifactGateway,
) -> dict[str, Any]:
    text = str(prompt.get("text") or "").strip()
    explicit_instruction_ref = str(prompt.get("instructionRef") or "").strip()
    inline_instruction = str(request.instruction_ref or "").strip()
    instruction_ref = explicit_instruction_ref or inline_instruction
    if not text and instruction_ref:
        if explicit_instruction_ref:
            text = (await artifact_gateway.read_text(instruction_ref)).strip()
        else:
            text = inline_instruction
    if not text:
        text = str((request.parameters or {}).get("description") or "").strip()
    if not text:
        title = str((request.parameters or {}).get("title") or "MoonMind Agent Task").strip()
        workspace_blob = json.dumps(request.workspace_spec or {}, indent=2, default=str)
        parts = [
            f"Task title: {title}",
            f"Correlation ID: {request.correlation_id}",
            f"Workspace spec (JSON):\n{workspace_blob}",
        ]
        if request.input_refs:
            parts.append("Input refs: " + ", ".join(request.input_refs))
        text = "\n\n".join(parts)

    return {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _first_message_text(first_message: dict[str, Any]) -> str:
    data = first_message.get("data")
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _first_message_marker(*, request: AgentExecutionRequest, digest: str) -> str:
    return "\n".join(
        [
            "MoonMind-Omnigent-Run:",
            f"  correlationId: {request.correlation_id}",
            f"  idempotencyKey: {request.idempotency_key}",
            f"  firstMessageDigest: {digest}",
        ]
    )


def _snapshot_contains_first_message_marker(
    snapshot: dict[str, Any],
    *,
    digest: str,
    marker: str,
) -> bool:
    needle_values = {digest, marker}
    stack: list[Any] = [snapshot]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str) and any(needle in value for needle in needle_values):
            return True
    return False


async def _unsupported_bundle_upload(bundle_ref: str) -> dict[str, Any]:
    raise OmnigentContractError(
        f"Omnigent bundleRef cannot be resolved by this activity: {bundle_ref}"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_heartbeat(details: dict[str, Any]) -> None:
    try:
        activity.heartbeat(details)
    except RuntimeError as exc:
        _logger.debug("Skipping Omnigent heartbeat outside activity context: %s", exc)


def _heartbeat_details() -> tuple[Any, ...]:
    try:
        raw = getattr(activity.info(), "heartbeat_details", ())
    except RuntimeError:
        return ()
    if raw is None:
        return ()
    if isinstance(raw, tuple):
        return raw
    if isinstance(raw, list):
        return tuple(raw)
    return (raw,)


def _heartbeat_state() -> dict[str, Any]:
    for detail in reversed(_heartbeat_details()):
        if isinstance(detail, dict):
            return detail
    return {}


def _heartbeat_session_id(state: dict[str, Any]) -> str:
    return str(
        state.get("omnigentSessionId")
        or state.get("session_id")
        or state.get("sessionId")
        or ""
    ).strip()


async def _periodic_stream_heartbeat(
    *,
    session_id: str,
    event_count: dict[str, int],
    status: dict[str, str],
    interval_seconds: float = 30.0,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        _safe_heartbeat(
            {
                "omnigentSessionId": session_id,
                "normalizedStatus": status.get("value", "running"),
                "eventsCaptured": event_count.get("value", 0),
                "alive": True,
            }
        )


async def _enqueue_stream_events(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    queue: asyncio.Queue[dict[str, Any] | BaseException | None],
) -> None:
    try:
        async for event in client.stream_events(session_id):
            await queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await queue.put(exc)
    finally:
        await queue.put(None)


async def _queued_stream_events(
    *,
    queue: asyncio.Queue[dict[str, Any] | BaseException | None],
    stream_task: asyncio.Task[None],
) -> AsyncIterator[dict[str, Any]]:
    while True:
        event = await queue.get()
        if event is None:
            break
        if isinstance(event, BaseException):
            raise event
        yield event
    if stream_task.done() and not stream_task.cancelled():
        stream_task.result()


def build_omnigent_result(
    *,
    request: AgentExecutionRequest,
    terminal_status: str,
    session_id: str,
    agent_id: str | None,
    final_snapshot: dict[str, Any],
    event_count: int,
    capture_bundle: OmnigentCaptureBundle,
    failure_summary: str | None = None,
    provider_error_code: str | None = None,
) -> AgentRunResult:
    """Build compact terminal canonical result for Omnigent."""

    output_refs = list(capture_bundle.output_refs)
    diagnostics_ref = capture_bundle.diagnostics_ref
    if not output_refs:
        raise OmnigentContractError("Omnigent result requires MoonMind output artifact refs")
    if not diagnostics_ref:
        raise OmnigentContractError("Omnigent result requires a MoonMind diagnostics artifact ref")
    _assert_no_provider_native_refs(
        [*output_refs, diagnostics_ref, *capture_bundle.metadata_refs.values()]
    )
    summary = final_snapshot.get("summary") or failure_summary
    if not summary:
        summary = (
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        )

    metadata = {
        "providerName": "omnigent",
        "normalizedStatus": terminal_status,
        "omnigentSessionId": session_id,
        "sseEventsCaptured": event_count,
        "correlationId": request.correlation_id,
    }
    if agent_id:
        metadata["omnigentAgentId"] = agent_id
    if capture_bundle.capture_manifest_ref:
        metadata["captureManifestRef"] = capture_bundle.capture_manifest_ref
    if capture_bundle.external_state_ref:
        metadata["externalStateRef"] = capture_bundle.external_state_ref
    metadata.update(capture_bundle.metadata_refs)
    snapshot_metadata_keys = {
        "omnigentAgentName": "omnigent_agent_name",
        "hostType": "host_type",
        "workspace": "workspace",
        "githubPrUrl": "github_pr_url",
    }
    for metadata_key, snake_key in snapshot_metadata_keys.items():
        value = final_snapshot.get(metadata_key) or final_snapshot.get(snake_key)
        if value:
            metadata[metadata_key] = str(value)

    return AgentRunResult(
        outputRefs=output_refs,
        summary=_compact_summary(
            summary,
            fallback="Omnigent session reached a terminal status",
        ),
        diagnosticsRef=str(diagnostics_ref),
        failureClass=_failure_class_for(terminal_status),
        providerErrorCode=provider_error_code,
        metadata=metadata,
    )


def _assert_no_provider_native_refs(refs: list[str]) -> None:
    bad = [ref for ref in refs if str(ref).startswith("omnigent://")]
    if bad:
        raise OmnigentContractError(
            "Omnigent terminal result cannot expose provider-native refs"
        )


async def _cancel_omnigent_session(
    client: OmnigentHttpClient,
    session_id: str,
) -> None:
    with suppress(Exception):
        await client.interrupt(session_id)
    with suppress(Exception):
        snapshot = await client.get_session(session_id)
        normalized = normalize_omnigent_observation(snapshot)
        if normalized in {
            "created",
            "launching",
            "provisioning",
            "running",
            "waiting",
            "idle",
            "awaiting_approval",
            "intervention_requested",
        }:
            await client.stop_session(session_id)


async def _capture_cancelled_omnigent_session(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    initial_snapshot: dict[str, Any] | None,
    first_message_request: dict[str, Any] | None,
    first_message_response: dict[str, Any] | None,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    capture_policy: dict[str, Any] | None,
) -> None:
    with suppress(Exception):
        final_snapshot = await client.get_session(session_id)
        await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot or {"status": "canceled"},
            first_message_request=first_message_request,
            first_message_response=first_message_response,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="canceled",
            diagnostics={
                "cancelled": True,
                "failureClass": "system_error",
            },
            harvest_resources=True,
            capture_policy=capture_policy,
        )


async def _harvest_changed_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> list[dict[str, Any]]:
    try:
        changed = await client.list_changed_files(session_id)
    except Exception as exc:
        manifest["changedFilesUnavailable"] = _compact_summary(
            exc,
            fallback="changed files unavailable",
        )
        return []
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="changedFilesIndexRef",
        name="output.omnigent.changed_files.index.json",
        payload=changed,
        link_type="output.omnigent.changed_files.index",
    )
    manifest["changedFilesIndexRef"] = index_ref
    file_items = _resource_items(changed)[:_MAX_OMNIGENT_HARVEST_ITEMS]
    harvested: list[dict[str, Any]] = []
    for item in file_items:
        path = str(
            item.get("path")
            or item.get("file_path")
            or item.get("filePath")
            or item.get("name")
            or ""
        ).strip()
        if not path:
            continue
        try:
            content = await client.get_workspace_file(session_id, path)
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="changed file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.changed_files/{path}",
            payload=content,
            link_type="output.omnigent.changed_file",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["changedFiles"] = harvested
    manifest.setdefault("patchUnavailable", True)
    return file_items


async def _harvest_workspace_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    try:
        files = await client.list_workspace_files(session_id)
    except Exception as exc:
        manifest["workspaceFilesUnavailable"] = _compact_summary(
            exc,
            fallback="workspace files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="workspaceFilesIndexRef",
        name="output.omnigent.workspace_files.index.json",
        payload=files,
        link_type="output.omnigent.workspace_files.index",
    )
    manifest["workspaceFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        path = _resource_path(item)
        if not path:
            continue
        if str(item.get("type") or item.get("kind") or "").strip().lower() in {
            "dir",
            "directory",
            "folder",
        }:
            harvested.append({"path": path, "skipped": "directory"})
            continue
        try:
            content = await client.get_workspace_file(session_id, path)
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="workspace file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_files/{path}",
            payload=content,
            link_type="output.omnigent.workspace_file",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["workspaceFiles"] = harvested


async def _harvest_workspace_diffs(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    changed_items: list[dict[str, Any]],
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    paths = [
        path
        for path in (_resource_path(item) for item in changed_items)
        if path
    ][:_MAX_OMNIGENT_HARVEST_ITEMS]
    if not paths:
        manifest["workspaceDiffs"] = []
        manifest["patchUnavailable"] = True
        return
    harvested: list[dict[str, Any]] = []
    for path in paths:
        try:
            diff = await client.get_workspace_diff(session_id, path)
        except Exception as exc:
            manifest["workspaceDiffsUnavailable"] = _compact_summary(
                exc,
                fallback="workspace diff capability unavailable",
            )
            manifest["patchUnavailable"] = True
            return
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_diffs/{path}.diff",
            payload=diff,
            link_type="output.omnigent.workspace_diff",
            content_type="text/x-diff",
        )
        harvested.append({"path": path, "artifactRef": ref})
    manifest["workspaceDiffs"] = harvested
    manifest["patchUnavailable"] = not bool(harvested)


async def _harvest_session_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
) -> None:
    try:
        files = await client.list_session_files(session_id)
    except Exception as exc:
        manifest["sessionFilesUnavailable"] = _compact_summary(
            exc,
            fallback="session files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="sessionFilesIndexRef",
        name="output.omnigent.session_files.index.json",
        payload=files,
        link_type="output.omnigent.session_files.index",
    )
    manifest["sessionFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        file_id = str(item.get("id") or item.get("file_id") or item.get("fileId") or "").strip()
        filename = str(item.get("filename") or item.get("name") or file_id).strip()
        if not file_id:
            continue
        try:
            content = await client.get_session_file_content(session_id, file_id)
        except Exception as exc:
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="session file content unavailable",
                    ),
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.session_files/{file_id}/{filename}",
            payload=content,
            link_type="output.omnigent.session_file",
        )
        metadata_ref = await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key=f"sessionFileMetadataRef:{file_id}",
            name=f"output.omnigent.session_files/{file_id}/metadata.json",
            payload=item,
            link_type="output.omnigent.session_file.metadata",
        )
        harvested.append(
            {
                "fileId": file_id,
                "filename": filename,
                "artifactRef": ref,
                "metadataRef": metadata_ref,
            }
        )
    manifest["sessionFiles"] = harvested


def _resource_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "files", "changes", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _resource_items(value)
            if nested:
                return nested
    return []


def _resource_path(item: dict[str, Any]) -> str:
    return str(
        item.get("path")
        or item.get("file_path")
        or item.get("filePath")
        or item.get("relativePath")
        or item.get("name")
        or ""
    ).strip().strip("/")


def _child_session_ids(
    events: list[dict[str, Any]],
    *,
    parent_session_id: str,
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        event_type = str(event.get("type") or event.get("eventType") or "").lower()
        if "child" not in event_type:
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized_key = key.replace("_", "").lower()
                    if normalized_key in {"sessionid", "childsessionid"}:
                        candidate = str(nested or "").strip()
                        if (
                            candidate
                            and candidate != parent_session_id
                            and candidate not in seen
                        ):
                            ids.append(candidate)
                            seen.add(candidate)
                    else:
                        stack.append(nested)
            elif isinstance(value, list):
                stack.extend(value)
    return ids


async def _build_capture_bundle(
    *,
    client: OmnigentHttpClient | None,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    initial_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
    first_message_request: dict[str, Any] | None,
    first_message_response: dict[str, Any] | None,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    terminal_status: str,
    diagnostics: dict[str, Any],
    harvest_resources: bool,
    capture_policy: dict[str, Any] | None = None,
    endpoint_ref: str | None = None,
    retry_evidence: dict[str, Any] | None = None,
) -> OmnigentCaptureBundle:
    refs: dict[str, str] = {}
    if first_message_request is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageRequestRef",
            name="input.omnigent.first_message.request.json",
            payload=first_message_request,
            link_type="input.omnigent.first_message.request",
        )
    if first_message_response is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageResponseRef",
            name="input.omnigent.first_message.response.json",
            payload=first_message_response,
            link_type="input.omnigent.first_message.response",
        )
    if initial_snapshot is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="initialSnapshotRef",
            name="runtime.omnigent.snapshot.initial.json",
            payload=initial_snapshot,
            link_type="runtime.omnigent.snapshot.initial",
        )
    raw_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.raw.jsonl",
        payload=_jsonl(raw_events),
        link_type="runtime.omnigent.sse.raw",
        content_type="application/x-ndjson",
    )
    refs["rawSseStreamRef"] = raw_ref
    normalized_ref = await artifact_gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.normalized.jsonl",
        payload=_jsonl(normalized_events),
        link_type="runtime.omnigent.sse.normalized",
        content_type="application/x-ndjson",
    )
    refs["normalizedEventStreamRef"] = normalized_ref
    final_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="finalSnapshotRef",
        name="output.omnigent.snapshot.final.json",
        payload=final_snapshot,
        link_type="output.omnigent.snapshot.final",
    )
    manifest: dict[str, Any] = {
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "omnigentAgentId": agent_id,
        "terminalStatus": terminal_status,
        "artifactRefs": refs,
        "patchUnavailable": True,
    }
    child_session_ids = _child_session_ids(raw_events, parent_session_id=session_id)
    manifest["childSessions"] = len(child_session_ids)
    if child_session_ids:
        child_ref = await artifact_gateway.write_text(
            request=request,
            name="runtime.omnigent.child_sessions.jsonl",
            payload=_jsonl(
                [
                    {"childSessionId": child_session_id}
                    for child_session_id in child_session_ids
                ]
            ),
            link_type="runtime.omnigent.child_sessions",
            content_type="application/x-ndjson",
        )
        refs["childSessionsRef"] = child_ref
        manifest["childSessionsRef"] = child_ref
        child_snapshots: list[dict[str, str]] = []
        if client is not None:
            for child_session_id in child_session_ids:
                try:
                    child_snapshot = await client.get_session(child_session_id)
                except Exception as exc:
                    child_snapshots.append(
                        {
                            "childSessionId": child_session_id,
                            "unavailable": _compact_summary(
                                exc,
                                fallback="child session snapshot unavailable",
                            ),
                        }
                    )
                    continue
                child_snapshot_ref = await _capture_artifact_json(
                    artifact_gateway,
                    request,
                    refs,
                    key=f"childSessionSnapshotRef:{child_session_id}",
                    name=f"runtime.omnigent.child_sessions/{child_session_id}.json",
                    payload=child_snapshot,
                    link_type="runtime.omnigent.child_session.snapshot",
                )
                child_snapshots.append(
                    {
                        "childSessionId": child_session_id,
                        "snapshotRef": child_snapshot_ref,
                    }
                )
        manifest["childSessionEvidence"] = child_snapshots
    if harvest_resources and client is not None and session_id:
        changed_items: list[dict[str, Any]] = []
        if _capture_enabled(capture_policy, "changedFiles"):
            changed_items = await _harvest_changed_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
        if _capture_enabled(capture_policy, "workspaceFiles"):
            await _harvest_workspace_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
        await _harvest_workspace_diffs(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            changed_items=changed_items,
            manifest=manifest,
            refs=refs,
        )
        if _capture_enabled(capture_policy, "sessionFiles"):
            await _harvest_session_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
            )
    diagnostics_payload = {
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "terminalStatus": terminal_status,
        "diagnostics": diagnostics,
        "captureManifest": manifest,
    }
    diagnostics_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="diagnosticsRef",
        name="diagnostics.omnigent.json",
        payload=diagnostics_payload,
        link_type="diagnostics.omnigent",
    )
    external_state_payload = _build_external_state_payload(
        request=request,
        session_id=session_id,
        agent_id=agent_id,
        terminal_status=terminal_status,
        refs=refs,
        diagnostics_ref=diagnostics_ref,
        endpoint_ref=endpoint_ref,
        retry_evidence=retry_evidence,
    )
    external_state_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="externalStateRef",
        name="checkpoint.omnigent.external_state.json",
        payload=external_state_payload,
        link_type="checkpoint.omnigent.external_state",
    )
    manifest["externalStateRef"] = external_state_ref
    manifest_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="captureManifestRef",
        name="output.omnigent.capture_manifest.json",
        payload=manifest,
        link_type="output.omnigent.capture_manifest",
    )
    metadata_refs = {
        "captureManifestRef": manifest_ref,
        "externalStateRef": external_state_ref,
        "rawSseStreamRef": raw_ref,
        "normalizedEventStreamRef": normalized_ref,
        "finalSnapshotRef": final_ref,
    }
    for optional_key in (
        "firstMessageRequestRef",
        "firstMessageResponseRef",
        "initialSnapshotRef",
        "changedFilesIndexRef",
        "workspaceFilesIndexRef",
        "sessionFilesIndexRef",
        "childSessionsRef",
    ):
        if optional_key in refs:
            metadata_refs[optional_key] = refs[optional_key]
    output_refs = [final_ref, normalized_ref, manifest_ref]
    return OmnigentCaptureBundle(
        output_refs=output_refs,
        diagnostics_ref=diagnostics_ref,
        capture_manifest_ref=manifest_ref,
        external_state_ref=external_state_ref,
        metadata_refs=metadata_refs,
    )


def _build_external_state_payload(
    *,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    terminal_status: str,
    refs: dict[str, str],
    diagnostics_ref: str,
    endpoint_ref: str | None,
    retry_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = _compact_retry_evidence(retry_evidence)
    payload: dict[str, Any] = {
        "provider": "omnigent",
        "sourceIssue": "MM-1078",
        "sourceIssueRefs": ["MM-1078", "MM-1074"],
        "idempotencyKey": request.idempotency_key,
        "omnigentSessionId": session_id,
        "omnigentAgentId": agent_id,
        "terminalStatus": terminal_status,
        "diagnosticsRef": diagnostics_ref,
        "retryEvidence": evidence,
        "artifactRefs": {
            key: value
            for key, value in refs.items()
            if key
            in {
                "initialSnapshotRef",
                "finalSnapshotRef",
                "rawSseStreamRef",
                "normalizedEventStreamRef",
                "firstMessageRequestRef",
                "firstMessageResponseRef",
            }
        },
    }
    if endpoint_ref:
        payload["endpointRef"] = str(endpoint_ref)
    return payload


def _compact_retry_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {"outcome": "created", "reattached": False, "reconciled": False}
    allowed = {
        "outcome",
        "source",
        "reattached",
        "reconciled",
        "mismatch",
        "firstMessageState",
        "firstMessageDigest",
        "idempotencyMarkerPresent",
        "diagnosticsCode",
    }
    compact = {
        key: value
        for key, value in evidence.items()
        if key in allowed and value is not None
    }
    compact.setdefault("reattached", compact.get("outcome") in {"attached", "reconciled"})
    compact.setdefault("reconciled", compact.get("outcome") == "reconciled")
    compact.setdefault("mismatch", compact.get("outcome") == "unrecoverable_mismatch")
    return compact


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(event, sort_keys=True, default=str, separators=(",", ":")) + "\n"
        for event in events
    )


def _capture_enabled(capture_policy: dict[str, Any] | None, key: str) -> bool:
    if capture_policy is None:
        return True
    return bool(capture_policy.get(key, True))


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Expected after requesting cancellation of a helper task.
        pass


async def run_omnigent_execution(
    request: AgentExecutionRequest,
    *,
    artifact_gateway: OmnigentArtifactGateway | None = None,
    run_store: OmnigentRunStore | None = None,
) -> AgentRunResult:
    """Execute one Omnigent session and return only terminal AgentRunResult."""

    gate = build_omnigent_gate()
    if not gate.enabled:
        raise RuntimeError(
            f"{OMNIGENT_DISABLED_MESSAGE} (missing: {', '.join(gate.missing)})"
        )

    client: OmnigentHttpClient | None = None
    session_id = ""
    stream_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    artifact_gateway = artifact_gateway or LocalOmnigentArtifactGateway()
    first_message: dict[str, Any] | None = None
    first_message_response: dict[str, Any] | None = None
    initial_snapshot: dict[str, Any] | None = None
    raw_events: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    target_agent_id: str | None = None
    delete_after_harvest = False
    capture_policy: dict[str, Any] | None = None
    endpoint_ref: str | None = None
    retry_evidence: dict[str, Any] = {
        "outcome": "created",
        "reattached": False,
        "reconciled": False,
    }
    try:
        selection = build_omnigent_selection(request)
        capture_policy = selection.capture
        endpoint_ref = str(selection.endpoint_ref or "default")
        delete_after_harvest = bool(
            selection.capture.get("deleteOmnigentSessionAfterHarvest", False)
        )
        async with httpx.AsyncClient() as httpx_client:
            client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=httpx_client,
            )

            async def list_agents() -> list[dict[str, Any]]:
                raw = await _maybe_await(client.list_agents())
                if isinstance(raw, list):
                    return [item for item in raw if isinstance(item, dict)]
                if isinstance(raw, dict):
                    return _agent_items(raw)
                return []

            target = await resolve_omnigent_target(
                selection,
                list_agents=list_agents,
                upload_agent_bundle=_unsupported_bundle_upload,
                default_agent_name=resolved_default_agent_name(),
            )
            target_agent_id = target.agent_id
            session_payload = build_omnigent_session_create_payload(
                request=request,
                selection=selection,
                target=target,
            )
            session_payload["idempotency_key"] = request.idempotency_key
            labels = session_payload.setdefault("labels", {})
            if isinstance(labels, dict):
                labels.setdefault("moonmind.issue", "MM-1059")

            durable_row = None
            if run_store is not None:
                durable_row = await run_store.get_or_create(
                    request=request,
                    endpoint_ref=endpoint_ref,
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    target_metadata={
                        "hostType": selection.session.host_type,
                        "workspace": selection.session.workspace,
                    },
                )

            retry_state = _heartbeat_state()
            durable_session_id = str(
                getattr(durable_row, "omnigent_session_id", None) or ""
            ).strip()
            heartbeat_session_id = _heartbeat_session_id(retry_state)
            session_id = durable_session_id or heartbeat_session_id
            first_message_posted = bool(retry_state.get("firstMessagePosted"))
            first_message_reconcile_required = False
            if durable_row is not None:
                first_message_reconcile_required = (
                    durable_row.first_message_state == FIRST_MESSAGE_POSTING
                )
                first_message_posted = (
                    durable_row.first_message_state
                    in {FIRST_MESSAGE_POSTED, FIRST_MESSAGE_TERMINAL}
                )
            if session_id:
                retry_evidence = {
                    "outcome": "attached",
                    "source": "durable_mapping" if durable_session_id else "heartbeat",
                    "reattached": True,
                    "reconciled": False,
                    "firstMessageState": getattr(
                        durable_row,
                        "first_message_state",
                        "posted" if first_message_posted else "unknown",
                    ),
                }
            if not session_id:
                create_response = await client.create_session(session_payload)
                session_id = _session_id(create_response)
                if run_store is not None:
                    await run_store.attach_session(
                        request.idempotency_key,
                        session_id,
                    )
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "normalizedStatus": "running",
                        "eventsCaptured": 0,
                        "firstMessagePosted": False,
                    }
                )
            with suppress(Exception):
                initial_snapshot = await client.get_session(session_id)

            first_message = await _build_omnigent_first_message(
                request=request,
                prompt=selection.prompt,
                artifact_gateway=artifact_gateway,
            )
            digest = hashlib.sha256(
                json.dumps(first_message, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            marker = _first_message_marker(request=request, digest=digest)
            first_message.setdefault("metadata", {})[
                "moonmindFirstMessageDigest"
            ] = digest
            first_message["metadata"]["moonmindIdempotencyKey"] = request.idempotency_key
            if selection.prompt.get("includeIdempotencyMarker", True):
                first_message_text = _first_message_text(first_message)
                first_message["data"]["content"][0]["text"] = (
                    f"{first_message_text}\n\n{marker}".strip()
                )
            if run_store is not None:
                try:
                    durable_row = await run_store.mark_prepared(
                        request.idempotency_key,
                        digest=digest,
                        marker=marker,
                    )
                    first_message_reconcile_required = (
                        durable_row.first_message_state == FIRST_MESSAGE_POSTING
                    )
                    retry_evidence["firstMessageState"] = durable_row.first_message_state
                    retry_evidence["firstMessageDigest"] = digest
                    retry_evidence["idempotencyMarkerPresent"] = True
                except OmnigentDigestMismatchError as exc:
                    retry_evidence.update(
                        {
                            "outcome": "unrecoverable_mismatch",
                            "mismatch": True,
                            "firstMessageDigest": digest,
                            "idempotencyMarkerPresent": True,
                            "diagnosticsCode": "omnigent_first_message_digest_mismatch",
                        }
                    )
                    bundle = await _build_capture_bundle(
                        client=client,
                        artifact_gateway=artifact_gateway,
                        request=request,
                        session_id=session_id,
                        agent_id=target.agent_id,
                        initial_snapshot=initial_snapshot,
                        final_snapshot=initial_snapshot or {"status": "failed"},
                        first_message_request=first_message,
                        first_message_response=None,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": str(exc),
                            "nonRetryable": True,
                            "failureClass": "integration_error",
                        },
                        harvest_resources=False,
                        endpoint_ref=endpoint_ref,
                        retry_evidence=retry_evidence,
                    )
                    return build_omnigent_result(
                        request=request,
                        terminal_status="failed",
                        session_id=session_id,
                        agent_id=target.agent_id,
                        final_snapshot={
                            "status": "failed",
                            "summary": "First-message digest mismatch",
                        },
                        event_count=0,
                        capture_bundle=bundle,
                        failure_summary="First-message digest mismatch",
                        provider_error_code="omnigent_first_message_digest_mismatch",
                    )
            stream_queue: asyncio.Queue[dict[str, Any] | BaseException | None] | None = None
            if first_message_reconcile_required:
                reconciliation_snapshot = await client.get_session(session_id)
                if not _snapshot_contains_first_message_marker(
                    reconciliation_snapshot,
                    digest=digest,
                    marker=marker,
                ):
                    retry_evidence.update(
                        {
                            "outcome": "unrecoverable_mismatch",
                            "mismatch": True,
                            "firstMessageState": FIRST_MESSAGE_POSTING,
                            "firstMessageDigest": digest,
                            "idempotencyMarkerPresent": False,
                            "diagnosticsCode": "omnigent_first_message_reconcile_failed",
                        }
                    )
                    bundle = await _build_capture_bundle(
                        client=client,
                        artifact_gateway=artifact_gateway,
                        request=request,
                        session_id=session_id,
                        agent_id=target.agent_id,
                        initial_snapshot=initial_snapshot,
                        final_snapshot=reconciliation_snapshot,
                        first_message_request=first_message,
                        first_message_response=None,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": "Unable to reconcile first-message posting state",
                            "failureClass": "integration_error",
                        },
                        harvest_resources=False,
                        endpoint_ref=endpoint_ref,
                        retry_evidence=retry_evidence,
                    )
                    return build_omnigent_result(
                        request=request,
                        terminal_status="failed",
                        session_id=session_id,
                        agent_id=target.agent_id,
                        final_snapshot={
                            "status": "failed",
                            "summary": "Unable to reconcile first-message posting state",
                        },
                        event_count=0,
                        capture_bundle=bundle,
                        failure_summary="Unable to reconcile first-message posting state",
                        provider_error_code="omnigent_first_message_reconcile_failed",
                    )
                if run_store is not None:
                    await run_store.mark_posted(request.idempotency_key)
                first_message_posted = True
                retry_evidence.update(
                    {
                        "outcome": "reconciled",
                        "reattached": True,
                        "reconciled": True,
                        "firstMessageState": FIRST_MESSAGE_POSTED,
                        "firstMessageDigest": digest,
                        "idempotencyMarkerPresent": True,
                    }
                )
            if not first_message_posted:
                stream_queue = asyncio.Queue()
                stream_task = asyncio.create_task(
                    _enqueue_stream_events(
                        client=client,
                        session_id=session_id,
                        queue=stream_queue,
                    )
                )
                await asyncio.sleep(0)
                if run_store is not None:
                    await run_store.mark_posting(request.idempotency_key)
                first_message_response = await client.post_event(session_id, first_message)
                if run_store is not None:
                    await run_store.mark_posted(
                        request.idempotency_key,
                        response=first_message_response,
                    )
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "normalizedStatus": "running",
                        "eventsCaptured": 0,
                        "firstMessagePosted": True,
                        "firstMessageDigest": digest,
                    }
                )
                retry_evidence.update(
                    {
                        "outcome": (
                            "created"
                            if retry_evidence.get("outcome") == "created"
                            else "posted"
                        ),
                        "firstMessageState": FIRST_MESSAGE_POSTED,
                        "firstMessageDigest": digest,
                        "idempotencyMarkerPresent": True,
                    }
                )

            event_count = {"value": 0}
            heartbeat_status = {"value": "running"}
            heartbeat_task = asyncio.create_task(
                _periodic_stream_heartbeat(
                    session_id=session_id,
                    event_count=event_count,
                    status=heartbeat_status,
                )
            )
            terminal_status: str | None = None
            try:
                stream_events = (
                    _queued_stream_events(
                        queue=stream_queue,
                        stream_task=stream_task,
                    )
                    if stream_queue is not None and stream_task is not None
                    else client.stream_events(session_id)
                )
                async for event in stream_events:
                    event_count["value"] += 1
                    raw_events.append(dict(event))
                    normalized = normalize_omnigent_observation(event)
                    normalized_events.append(
                        {
                            "eventType": str(event.get("type") or "").strip(),
                            "normalizedStatus": normalized or "running",
                            "sequence": event_count["value"],
                        }
                    )
                    _safe_heartbeat(
                        {
                            "omnigentSessionId": session_id,
                            "normalizedStatus": normalized or "running",
                            "eventsCaptured": event_count["value"],
                            "firstMessagePosted": True,
                            "eventType": str(event.get("type") or "").strip(),
                        }
                    )
                    if normalized in {"awaiting_approval", "intervention_requested"}:
                        heartbeat_status["value"] = normalized
                        _safe_heartbeat(
                            {
                                "normalizedStatus": normalized,
                                "omnigentSessionId": session_id,
                                "eventsCaptured": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
                        continue
                    if normalized in {"completed", "failed", "canceled", "timed_out"}:
                        terminal_status = normalized
                        heartbeat_status["value"] = normalized
                        break
                    if event_count["value"] % 8 == 0:
                        _safe_heartbeat(
                            {
                                "omnigentSessionId": session_id,
                                "normalizedStatus": normalized or "running",
                                "eventsCaptured": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
            finally:
                await _cancel_task(heartbeat_task)
                await _cancel_task(stream_task)

            final_snapshot = await client.get_session(session_id)
            if terminal_status is None:
                normalized_snapshot = normalize_omnigent_observation(final_snapshot)
                if normalized_snapshot in {
                    "completed",
                    "failed",
                    "canceled",
                    "timed_out",
                }:
                    terminal_status = normalized_snapshot
                elif normalized_snapshot in _NON_TERMINAL_STATUSES:
                    raise OmnigentSessionStillRunningError(
                        "Omnigent stream ended while the provider session is still running"
                    )
            if terminal_status is None:
                raise OmnigentContractError(
                    "Omnigent stream ended before a terminal session outcome"
                )
            bundle = await _build_capture_bundle(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                agent_id=target.agent_id,
                initial_snapshot=initial_snapshot,
                final_snapshot=final_snapshot,
                first_message_request=first_message,
                first_message_response=first_message_response,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status=terminal_status,
                diagnostics={"failureClass": _failure_class_for(terminal_status)},
                harvest_resources=True,
                capture_policy=capture_policy,
                endpoint_ref=endpoint_ref,
                retry_evidence=retry_evidence,
            )
            if run_store is not None:
                await run_store.mark_terminal(
                    request.idempotency_key,
                    status=terminal_status,
                    terminal_refs={
                        "outputRefs": bundle.output_refs,
                        "diagnosticsRef": bundle.diagnostics_ref,
                        "metadataRefs": bundle.metadata_refs,
                    },
                )
            return build_omnigent_result(
                request=request,
                terminal_status=terminal_status,
                session_id=session_id,
                agent_id=target.agent_id,
                final_snapshot=final_snapshot,
                event_count=event_count["value"],
                capture_bundle=bundle,
            )
    except asyncio.CancelledError:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if client is not None and session_id:
            async with httpx.AsyncClient() as cleanup_httpx_client:
                cleanup_client = OmnigentHttpClient(
                    base_url=resolved_server_url(),
                    api_token=resolved_api_token(),
                    client=cleanup_httpx_client,
                )
                await _cancel_omnigent_session(cleanup_client, session_id)
                await _capture_cancelled_omnigent_session(
                    client=cleanup_client,
                    artifact_gateway=artifact_gateway,
                    request=request,
                    session_id=session_id,
                    agent_id=target_agent_id,
                    initial_snapshot=initial_snapshot,
                    first_message_request=first_message,
                    first_message_response=first_message_response,
                    raw_events=raw_events,
                    normalized_events=normalized_events,
                    capture_policy=capture_policy,
                )
                if delete_after_harvest:
                    with suppress(Exception):
                        await cleanup_client.delete_session(session_id)
        raise
    except (
        OmnigentArtifactError,
        OmnigentContractError,
        OmnigentAdapterError,
        ValueError,
    ) as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        final_snapshot = {"status": "failed", "summary": str(exc)}
        bundle = await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=target_agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot,
            first_message_request=first_message,
            first_message_response=first_message_response,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics={
                "error": str(exc),
                "failureClass": "integration_error",
            },
            harvest_resources=bool(client and session_id),
            endpoint_ref=endpoint_ref,
            retry_evidence=retry_evidence,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent contract error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass="integration_error",
            providerErrorCode="omnigent_contract_error",
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )
    except OmnigentSessionStillRunningError:
        raise
    except (OmnigentClientError, httpx.HTTPError) as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        status_code = exc.status_code if isinstance(exc, OmnigentClientError) else None
        final_snapshot = {"status": "failed", "summary": str(exc)}
        diagnostics = (
            exc.diagnostics()
            if isinstance(exc, OmnigentClientError)
            else {"error": str(exc), "failureClass": "integration_error"}
        )
        bundle = await _build_capture_bundle(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            agent_id=target_agent_id,
            initial_snapshot=initial_snapshot,
            final_snapshot=final_snapshot,
            first_message_request=first_message,
            first_message_response=first_message_response,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics=diagnostics,
            harvest_resources=bool(client and session_id),
            endpoint_ref=endpoint_ref,
            retry_evidence=retry_evidence,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent integration error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass="integration_error",
            providerErrorCode=str(status_code or "omnigent_http_error"),
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )


__all__ = [
    "LocalOmnigentArtifactGateway",
    "OmnigentArtifactGateway",
    "OmnigentCaptureBundle",
    "OmnigentContractError",
    "OmnigentSessionStillRunningError",
    "build_omnigent_result",
    "normalize_omnigent_observation",
    "run_omnigent_execution",
]
