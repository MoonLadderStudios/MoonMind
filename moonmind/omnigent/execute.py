"""Run one Omnigent streaming-gateway execution for MM-1059."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx
from temporalio import activity

from moonmind.omnigent.bridge_artifacts import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
    OmnigentArtifactGateway,
    OmnigentContractError,
    _build_capture_bundle,
    _compact_summary,
    build_omnigent_result,
    build_omnigent_terminal_refs,
)
from moonmind.omnigent.bridge_events import (
    build_omnigent_bridge_event,
    normalize_omnigent_observation,
)
from moonmind.omnigent.bridge_security import (
    BridgeSessionBinding,
    OmnigentAuthorizationError,
    assert_bridge_session_binding,
    authorize_bridge_access,
    redact_raw_events,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_ITEM_FRONTIER_KEY,
    FIRST_MESSAGE_NOT_PREPARED,
    FIRST_MESSAGE_POSTED,
    FIRST_MESSAGE_POSTING,
    FIRST_MESSAGE_TERMINAL,
    OmnigentBridgeSessionStore,
    OmnigentDigestMismatchError,
)
from moonmind.omnigent.control_plane import spans as control_plane_spans
from moonmind.omnigent.failure_classification import (
    OmnigentFailureReason,
    classify_omnigent_failure,
    failure_class_for_terminal_status,
)
from moonmind.omnigent.settings import (
    OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR,
    OMNIGENT_DISABLED_MESSAGE,
    build_omnigent_gate,
    resolved_api_token,
    resolved_default_agent_name,
    resolved_proxy_forward_headers,
    resolved_server_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentAgentSelection,
    OmnigentAdapterError,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
    resolve_omnigent_target,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)
from moonmind.workflows.skills.run_projection import (
    prepend_skill_activation_summary,
)

_NON_TERMINAL_STATUSES = {
    "created",
    "launching",
    "provisioning",
    "running",
    "waiting",
    "idle",
}
_TERMINAL_STATUSES = {"completed", "failed", "canceled", "timed_out"}
_logger = logging.getLogger(__name__)

_ACTIVITY_HEARTBEAT_INTERVAL_SECONDS = 30.0
_MARKED_TURN_QUIET_PERIOD_SECONDS = 60.0
_MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS = 300.0
_TERMINAL_RECONCILIATION_INTERVAL_SECONDS = 30.0
_ACTIVITY_HEARTBEAT_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "omnigent_activity_heartbeat_state",
    default=None,
)


class OmnigentSessionStillRunningError(OmnigentClientError):
    """Raised when the stream ends while the provider session is still active."""

    code = "OMNIGENT_CURRENT_TURN_TERMINAL_AMBIGUOUS"


def _session_id(payload: dict[str, Any]) -> str:
    raw = payload.get("id") or payload.get("session_id") or payload.get("sessionId")
    session_id = str(raw or "").strip()
    if not session_id:
        raise OmnigentContractError("Omnigent session creation response missing session id")
    return session_id


def _session_authority_observation(
    snapshot: Mapping[str, Any] | None,
) -> tuple[dict[str, bool] | None, str | None]:
    """Return only provider authority fields actually present in a snapshot."""

    if not isinstance(snapshot, Mapping):
        return None, None
    raw_capabilities = snapshot.get("interventionCapabilities")
    if not isinstance(raw_capabilities, Mapping):
        raw_capabilities = snapshot.get("capabilities")
    capabilities = (
        {
            str(key): value
            for key, value in raw_capabilities.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
        if isinstance(raw_capabilities, Mapping)
        else None
    )
    raw_status = snapshot.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status.strip() else None
    return capabilities, status


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

    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = parameters.get("omnigent")
    profile_authorization = (
        omnigent.get("_moonmindProfileAuthorization")
        if isinstance(omnigent, Mapping)
        else None
    )
    if request.resolved_skillset_ref and isinstance(
        profile_authorization, Mapping
    ):
        # The profile-bound host preflight has already verified and mounted the
        # exact resolved snapshot at this fixed runner-visible path. Put the
        # canonical activation block in the actual first message so explicit
        # prompt text and artifact-backed prompts cannot bypass Skill delivery.
        text = prepend_skill_activation_summary(
            text,
            parameters=parameters,
            materialization_metadata={
                "visiblePath": OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR,
                "canonicalAliasAvailable": False,
            },
            skills_on_demand_enabled=False,
        )
    metadata = parameters.get("metadata")
    moonmind = metadata.get("moonmind") if isinstance(metadata, dict) else {}
    if not isinstance(moonmind, dict):
        moonmind = {}
    continuation_authority_instruction = str(
        moonmind.get("terminalContinuationAuthorityInstruction") or ""
    ).strip()
    if (
        continuation_authority_instruction
        and continuation_authority_instruction not in text
    ):
        text = f"{text}\n\n{continuation_authority_instruction}"

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


def _first_message_marker(*, request: AgentExecutionRequest) -> str:
    return "\n".join(
        [
            "MoonMind-Omnigent-Run:",
            f"  correlationId: {request.correlation_id}",
            f"  idempotencyKey: {request.idempotency_key}",
        ]
    )


def _retrieval_evidence(request: AgentExecutionRequest) -> dict[str, Any]:
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    metadata = parameters.get("metadata")
    moonmind = metadata.get("moonmind") if isinstance(metadata, dict) else {}
    if not isinstance(moonmind, dict):
        moonmind = {}
    ref = str(moonmind.get("latestContextPackRef") or "").strip() or None
    mode = str(moonmind.get("retrievalMode") or "disabled").strip()
    failure_class = str(moonmind.get("retrievalFailureClass") or "").strip()
    if mode.startswith("degraded"):
        state = "degraded"
    elif ref:
        state = "completed"
    elif failure_class == "denied":
        state = "denied"
    elif failure_class == "unavailable":
        state = "unavailable"
    elif failure_class or mode not in {"disabled", ""}:
        state = "failed"
    else:
        state = "disabled"
    return {
        "state": state,
        "contextPackRef": ref,
        "contextPackDigest": moonmind.get("retrievedContextDigest"),
        "queryDigest": moonmind.get("retrievalQueryDigest"),
        "queryPreview": moonmind.get("retrievalQueryPreview"),
        "transport": moonmind.get("retrievedContextTransport"),
        "resultCount": int(moonmind.get("retrievedContextItemCount") or 0),
        "sources": list(moonmind.get("retrievedContextSources") or [])[:20],
        "collections": list(moonmind.get("retrievalCollections") or [])[:10],
        "scope": dict(moonmind.get("retrievalScope") or {}),
        "budgets": dict(moonmind.get("retrievalBudgets") or {}),
        "usage": dict(moonmind.get("retrievalUsage") or {}),
        "overlay": dict(moonmind.get("retrievalOverlay") or {}),
        "embeddingConfigRef": moonmind.get("retrievalEmbeddingConfigRef"),
        "durationMs": moonmind.get("retrievalDurationMs"),
        "failureClass": failure_class or None,
        "truncated": bool(moonmind.get("retrievalContextTruncated", False)),
        "mode": mode,
        "reason": (
            moonmind.get("retrievalDegradedReason")
            or moonmind.get("retrievalDisabledReason")
        ),
        "initiationMode": moonmind.get("retrievalInitiationMode", "automatic"),
        "durabilityAuthority": moonmind.get("retrievalDurabilityAuthority"),
    }


async def _resolve_initial_context_message(
    *,
    request: AgentExecutionRequest,
    first_message: dict[str, Any],
    artifact_gateway: OmnigentArtifactGateway,
    run_store: OmnigentBridgeSessionStore | None,
    durable_row: Any,
    workspace: str | None,
    include_idempotency_marker: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve or restore the exact context-bearing first message."""

    existing = dict(
        ((getattr(durable_row, "metadata_", None) or {}).get("initialRetrieval") or {})
    )
    prepared_ref = str(existing.get("preparedMessageRef") or "").strip()
    if prepared_ref:
        restored = json.loads(await artifact_gateway.read_text(prepared_ref))
        if not isinstance(restored, dict) or not _first_message_text(restored):
            raise OmnigentContractError("persisted first-message artifact is invalid")
        restored_digest = hashlib.sha256(
            json.dumps(restored, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        expected_digest = str(existing.get("preparedMessageDigest") or "").removeprefix(
            "sha256:"
        )
        if expected_digest and restored_digest != expected_digest:
            raise OmnigentContractError("persisted first-message artifact digest mismatch")
        context_ref = str(existing.get("contextPackRef") or "").strip()
        if context_ref:
            moonmind = (
                request.parameters.setdefault("metadata", {}).setdefault("moonmind", {})
            )
            moonmind["latestContextPackRef"] = context_ref
        return restored, existing

    # Cut over rows prepared by the pre-retrieval bridge without attempting a
    # new retrieval. Reconstruct and verify the already committed message, then
    # add the portable artifact ref used by subsequent retries. The durable row
    # remains the authority for the original digest.
    durable_state = str(getattr(durable_row, "first_message_state", "") or "")
    if durable_state and durable_state != FIRST_MESSAGE_NOT_PREPARED:
        first_message.setdefault("metadata", {})[
            "moonmindIdempotencyKey"
        ] = request.idempotency_key
        if include_idempotency_marker:
            original_text = _first_message_text(first_message)
            first_message["data"]["content"][0]["text"] = (
                f"{original_text}\n\n{_first_message_marker(request=request)}".strip()
            )
        message_bytes = json.dumps(
            first_message, sort_keys=True, separators=(",", ":")
        ).encode()
        reconstructed_digest = hashlib.sha256(message_bytes).hexdigest()
        return first_message, {
            "state": "disabled",
            "mode": "legacy_prepared_message",
            "reason": "pre_context_retrieval_cutover",
            # mark_prepared remains the durable digest authority and classifies
            # any reconstruction mismatch through the established user-error
            # path. Do not attempt fresh retrieval for this cutover row.
            "preparedMessageDigest": reconstructed_digest,
            "preparedMessageRef": await artifact_gateway.write_json(
                request=request,
                name="input.omnigent.first_message.prepared.json",
                payload=first_message,
                link_type="input.omnigent.first_message.prepared",
            ),
            "firstMessageConsumedContextRef": False,
        }

    text = _first_message_text(first_message)
    if text:
        from moonmind.rag.context_injection import ContextInjectionService

        retrieval_request = request.model_copy(deep=True)
        retrieval_request.instruction_ref = text
        resolution = await ContextInjectionService().inject_context(
            request=retrieval_request,
            # The provider session workspace may be a URL or a path visible only
            # inside the Omnigent host. Retrieval and staging run on this worker.
            workspace_path=Path.cwd().resolve(),
        )
        first_message["data"]["content"][0]["text"] = resolution.instruction
        # Copy only the compact RAG metadata back to the canonical request so it
        # is projected into terminal Step Execution evidence.
        request.parameters = retrieval_request.parameters
        moonmind = (
            request.parameters.get("metadata", {}).get("moonmind", {})
            if isinstance(request.parameters, dict)
            else {}
        )
        if resolution.artifact_path is not None:
            try:
                context_ref = await artifact_gateway.write_text(
                    request=request,
                    name="input.context-pack.json",
                    payload=resolution.artifact_path.read_text(encoding="utf-8"),
                    link_type="input.context-pack",
                    content_type="application/json",
                )
            except Exception as exc:
                authored_rag = request.parameters.get("rag")
                required = bool(
                    isinstance(authored_rag, dict) and authored_rag.get("required")
                )
                if required:
                    raise OmnigentContractError(
                        "required initial context artifact publication failed"
                    ) from exc
                first_message["data"]["content"][0]["text"] = text
                if isinstance(moonmind, dict):
                    moonmind.pop("latestContextPackRef", None)
                    moonmind.pop("retrievedContextArtifactPath", None)
                    moonmind["retrievalMode"] = "degraded_without_context"
                    moonmind["retrievalFailureClass"] = "artifact_publication_failed"
                    moonmind["retrievalDegradedReason"] = (
                        "context_artifact_publication_failed"
                    )
            else:
                if isinstance(moonmind, dict):
                    moonmind["latestContextPackRef"] = context_ref
                    moonmind["retrievedContextArtifactPath"] = context_ref
                    moonmind["retrievalDurabilityAuthority"] = "artifact_gateway"

    evidence = _retrieval_evidence(request)
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    authored_rag = parameters.get("rag")
    retrieval_required = bool(
        isinstance(authored_rag, dict) and authored_rag.get("required")
    )
    evidence["required"] = retrieval_required
    if retrieval_required and evidence["state"] not in {"completed", "degraded"}:
        record_context = getattr(run_store, "record_initial_context", None)
        if callable(record_context):
            await record_context(
                request.idempotency_key,
                evidence=evidence,
            )
        raise OmnigentContractError(
            "required initial context retrieval is unavailable: "
            f"{evidence.get('reason') or 'retrieval_disabled'}"
        )
    first_message.setdefault("metadata", {})[
        "moonmindIdempotencyKey"
    ] = request.idempotency_key
    marker = _first_message_marker(request=request)
    if include_idempotency_marker:
        first_message_text = _first_message_text(first_message)
        first_message["data"]["content"][0]["text"] = (
            f"{first_message_text}\n\n{marker}".strip()
        )
    message_bytes = json.dumps(first_message, sort_keys=True, separators=(",", ":")).encode()
    evidence["preparedMessageDigest"] = hashlib.sha256(message_bytes).hexdigest()
    evidence["preparedMessageRef"] = await artifact_gateway.write_json(
        request=request,
        name="input.omnigent.first_message.prepared.json",
        payload=first_message,
        link_type="input.omnigent.first_message.prepared",
    )
    evidence["firstMessageConsumedContextRef"] = bool(
        evidence.get("contextPackRef") and resolution.items_count > 0
    ) if text else False
    record_context = getattr(run_store, "record_initial_context", None)
    if callable(record_context):
        durable_row = await record_context(
            request.idempotency_key,
            evidence=evidence,
        )
    return first_message, evidence


def _new_external_state_evidence(
    *,
    endpoint_ref: object,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "endpointRef": str(endpoint_ref or "default"),
        "retry": {
            "idempotencyKey": idempotency_key,
            "sessionResolution": "pending",
            "attached": False,
            "attachSource": None,
            "firstMessageOutcome": "pending",
        },
        "firstMessage": {},
    }


def _profile_authorization_evidence(
    request: AgentExecutionRequest,
) -> dict[str, Any]:
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = parameters.get("omnigent")
    if not isinstance(omnigent, dict):
        return {}
    payload = omnigent.get("_moonmindProfileAuthorization")
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "providerProfileId",
        "credentialGeneration",
        "providerLeaseRef",
        "hostBindingRef",
        "hostLeaseRef",
        "endpointRef",
        "omnigentHostId",
        "bridgeSessionId",
    }
    return {key: payload[key] for key in allowed if payload.get(key) is not None}


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


def _nested_value_contains_text(value: Any, *, needle: str) -> bool:
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str) and needle in current:
            return True
    return False


def _snapshot_contains_current_turn_progress(
    snapshot: Mapping[str, Any],
    *,
    marker: str,
    baseline_item_ids: frozenset[str] | None = None,
) -> bool:
    """Prove provider work occurred after this invocation's marked message.

    Omnigent can replay the previous turn's terminal SSE event when a stream is
    opened before posting the next message. Prefer the per-message marker; when
    the bounded stock snapshot has evicted that marker, item identities captured
    immediately before dispatch preserve the same ordering boundary.
    """

    return bool(
        _marked_turn_item_state(
            snapshot,
            marker=marker,
            baseline_item_ids=baseline_item_ids,
        )["progress"]
    )


def _snapshot_item_ids(snapshot: Mapping[str, Any] | None) -> frozenset[str] | None:
    """Capture the bounded item identity frontier before dispatching a turn."""

    if not isinstance(snapshot, Mapping):
        return None
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return None
    return frozenset(
        item_id
        for raw_item in raw_items
        if isinstance(raw_item, Mapping)
        if (item_id := str(raw_item.get("id") or "").strip())
    )


def _validated_pre_dispatch_item_ids(raw_item_ids: Any) -> frozenset[str]:
    """Validate one bounded persisted or heartbeat item frontier."""

    if not isinstance(raw_item_ids, list):
        raise OmnigentContractError("Persisted pre-dispatch item frontier is invalid")
    item_ids = frozenset(
        str(item_id).strip() for item_id in raw_item_ids if str(item_id).strip()
    )
    if len(item_ids) != len(raw_item_ids):
        raise OmnigentContractError("Persisted pre-dispatch item frontier is invalid")
    return item_ids


def _persisted_pre_dispatch_item_ids(durable_row: Any) -> frozenset[str] | None:
    """Restore the exact item frontier recorded before the message side effect."""

    metadata = getattr(durable_row, "metadata_", None)
    if (
        not isinstance(metadata, Mapping)
        or FIRST_MESSAGE_ITEM_FRONTIER_KEY not in metadata
    ):
        return None
    return _validated_pre_dispatch_item_ids(
        metadata[FIRST_MESSAGE_ITEM_FRONTIER_KEY]
    )


def _marked_turn_item_state(
    snapshot: Mapping[str, Any],
    *,
    marker: str,
    baseline_item_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Summarize ordered completion evidence after one marked user item.

    Native Codex can accept a new message as a steer while the preceding turn
    is still producing tools. Those items share the same session response id,
    so the completion boundary must come from item ordering: the marker when it
    remains projected, otherwise item IDs absent from the pre-dispatch snapshot.
    A textual assistant after the last tool is terminal, while an unmatched tool
    call is still active regardless of the stock server's stale ``idle`` state.
    """

    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return {
            "markerIndex": -1,
            "boundarySource": None,
            "progress": False,
            "terminalAssistantAfterWork": False,
            "unfinishedToolCall": False,
            "signature": None,
        }

    marked_message_index = -1
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            continue
        if str(raw_item.get("type") or "").strip() != "message":
            continue
        data = raw_item.get("data")
        item_data = data if isinstance(data, Mapping) else {}
        if str(item_data.get("role") or "").strip().lower() != "user":
            continue
        if _nested_value_contains_text(raw_item, needle=marker):
            marked_message_index = index

    progress_start_index: int | None = (
        marked_message_index + 1 if marked_message_index >= 0 else None
    )
    boundary_source = "marker" if progress_start_index is not None else None
    if progress_start_index is None and baseline_item_ids is not None:
        progress_start_index = next(
            (
                index
                for index, raw_item in enumerate(raw_items)
                if isinstance(raw_item, Mapping)
                and (item_id := str(raw_item.get("id") or "").strip())
                and item_id not in baseline_item_ids
            ),
            None,
        )
        if progress_start_index is not None:
            boundary_source = "pre_dispatch_item_ids"

    if progress_start_index is None:
        return {
            "markerIndex": -1,
            "boundarySource": None,
            "progress": False,
            "terminalAssistantAfterWork": False,
            "unfinishedToolCall": False,
            "signature": None,
        }

    last_tool_index = -1
    last_text_assistant_index = -1
    progress = False
    pending_call_ids: set[str] = set()
    instrumentation_call_ids: set[str] = set()
    anonymous_pending_calls = 0
    for index, raw_item in enumerate(
        raw_items[progress_start_index:],
        start=progress_start_index,
    ):
        if not isinstance(raw_item, Mapping):
            continue
        item_type = str(raw_item.get("type") or "").strip()
        data = raw_item.get("data")
        item_data = data if isinstance(data, Mapping) else {}
        if item_type == "function_call":
            progress = True
            # Native Codex appends this evidence-only instrumentation after its
            # final assistant text. It is not agent work and must not turn a
            # completed response back into an active tool boundary.
            if str(item_data.get("name") or "").strip() == "turn_diff":
                call_id = str(item_data.get("call_id") or "").strip()
                if call_id:
                    instrumentation_call_ids.add(call_id)
                continue
            last_tool_index = index
            call_id = str(item_data.get("call_id") or "").strip()
            if call_id:
                pending_call_ids.add(call_id)
            else:
                anonymous_pending_calls += 1
        elif item_type == "function_call_output":
            progress = True
            call_id = str(item_data.get("call_id") or "").strip()
            if call_id and call_id in instrumentation_call_ids:
                continue
            last_tool_index = index
            if call_id:
                pending_call_ids.discard(call_id)
            elif anonymous_pending_calls:
                anonymous_pending_calls -= 1
        elif item_type == "message":
            role = str(item_data.get("role") or "").strip().lower()
            if role != "assistant":
                continue
            progress = True
            content = item_data.get("content")
            if isinstance(content, list) and any(
                isinstance(block, Mapping)
                and str(block.get("text") or "").strip()
                for block in content
            ):
                last_text_assistant_index = index
                # A later assistant message proves any earlier unmatched call
                # was omitted from the bounded projection (or failed without
                # an output item); calls that genuinely remain active are
                # ordered after that message and are added on later iterations.
                pending_call_ids.clear()
                anonymous_pending_calls = 0

    last_item = next(
        (item for item in reversed(raw_items) if isinstance(item, Mapping)),
        {},
    )
    last_data = last_item.get("data")
    last_item_data = last_data if isinstance(last_data, Mapping) else {}
    signature = (
        len(raw_items),
        str(last_item.get("id") or ""),
        str(last_item.get("type") or ""),
        str(last_item.get("status") or ""),
        str(last_item_data.get("role") or ""),
        str(last_item_data.get("call_id") or ""),
    )
    return {
        "markerIndex": marked_message_index,
        "boundarySource": boundary_source,
        "progress": progress,
        "terminalAssistantAfterWork": (
            last_text_assistant_index
            > max(progress_start_index - 1, last_tool_index)
        ),
        "unfinishedToolCall": bool(pending_call_ids or anonymous_pending_calls),
        "signature": signature,
    }


def _snapshot_projects_inactive_turn(snapshot: Mapping[str, Any]) -> bool:
    normalized = normalize_omnigent_observation(dict(snapshot))
    if normalized in {"completed", "failed", "canceled", "timed_out", "idle"}:
        return True
    active_response_is_projected = (
        "active_response_id" in snapshot or "activeResponseId" in snapshot
    )
    active_response_id = str(
        snapshot.get("active_response_id")
        or snapshot.get("activeResponseId")
        or ""
    ).strip()
    return (
        normalized in _NON_TERMINAL_STATUSES
        and active_response_is_projected
        and not active_response_id
    )


def _snapshot_confirms_current_turn_terminal(
    snapshot: Mapping[str, Any],
    *,
    marker: str,
    baseline_item_ids: frozenset[str] | None = None,
) -> bool:
    """Identify a structurally terminal assistant candidate for a marked turn.

    Stock Omnigent can emit ``response.completed`` for an intermediate response
    while the Codex turn remains active. A stale inactive projection is therefore
    corroborative only. Even this structural candidate must pass the bounded
    transcript-quiescence gate before it owns a terminal decision because an
    assistant preamble can be followed by a tool that has not appeared yet.
    """

    state = _marked_turn_item_state(
        snapshot,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    return bool(
        state["progress"]
        and state["terminalAssistantAfterWork"]
        and not state["unfinishedToolCall"]
        and _snapshot_projects_inactive_turn(snapshot)
    )


async def _unsupported_bundle_upload(bundle_ref: str) -> dict[str, Any]:
    raise OmnigentContractError(
        f"Omnigent bundleRef cannot be resolved by this activity: {bundle_ref}"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_heartbeat(details: dict[str, Any]) -> None:
    state = _ACTIVITY_HEARTBEAT_STATE.get()
    payload = dict(details)
    if state is not None:
        # All lifecycle and streaming tasks spawned by one Activity share this
        # mutable accumulator. A substrate-level liveness heartbeat therefore
        # preserves the latest session/cursor evidence instead of replacing it.
        state.update(details)
        payload = dict(state)
    try:
        activity.heartbeat(payload)
    except RuntimeError as exc:
        _logger.debug("Skipping Omnigent heartbeat outside activity context: %s", exc)


@asynccontextmanager
async def omnigent_activity_heartbeat(
    *, interval_seconds: float | None = None
) -> AsyncIterator[None]:
    """Heartbeat the complete Activity, including host/workspace preparation."""

    state: dict[str, Any] = {}
    token = _ACTIVITY_HEARTBEAT_STATE.set(state)
    interval = max(
        0.01,
        float(
            _ACTIVITY_HEARTBEAT_INTERVAL_SECONDS
            if interval_seconds is None
            else interval_seconds
        ),
    )

    async def heartbeat() -> None:
        while True:
            _safe_heartbeat({"activityAlive": True})
            await asyncio.sleep(interval)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        _ACTIVITY_HEARTBEAT_STATE.reset(token)


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


async def _await_marked_turn_terminal(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    marker: str,
    baseline_item_ids: frozenset[str] | None = None,
    event_count: int,
    terminal_status: str,
    timeout_seconds: float = 1800.0,
    interval_seconds: float = 2.0,
    quiet_period_seconds: float = _MARKED_TURN_QUIET_PERIOD_SECONDS,
    tool_only_quiet_period_seconds: float = (
        _MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS
    ),
) -> tuple[str, dict[str, Any]]:
    """Wait until a terminal event is stably projected into the marked turn.

    Omnigent sessions are interactive and return to ``idle`` after a Codex
    turn, so the session snapshot itself does not become terminal. Stock native
    sessions can replay the prior SSE terminal frame and can briefly project an
    assistant preamble as their last item before its next tool appears. Marked
    item ordering plus a bounded stable period therefore owns the terminal
    decision; neither a terminal frame nor a transient last assistant does.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, timeout_seconds)
    quiet_signature: tuple[Any, ...] | None = None
    quiet_since: float | None = None
    while loop.time() < deadline:
        observation_started_at = loop.time()
        snapshot = await client.get_session(session_id)
        observation_completed_at = loop.time()
        observation_latency_seconds = (
            observation_completed_at - observation_started_at
        )
        # A session read can block behind the provider while a new tool call is
        # being projected. Never let that unobserved interval satisfy the quiet
        # window: the returned snapshot may describe the state at request start,
        # before work that completed while the HTTP request was in flight.
        observation_was_slow = observation_latency_seconds > max(
            0.01,
            interval_seconds * 4,
        )
        normalized = normalize_omnigent_observation(snapshot)
        turn_state = _marked_turn_item_state(
            snapshot,
            marker=marker,
            baseline_item_ids=baseline_item_ids,
        )
        progress = bool(turn_state["progress"])
        if not isinstance(snapshot.get("items"), list) and normalized in {
            "completed",
            "failed",
            "canceled",
            "timed_out",
        }:
            # Older/alternate Omnigent adapters do not project ordered items.
            # For those adapters, a terminal snapshot corroborates the live
            # post-dispatch terminal event without inventing item evidence.
            return (
                normalized
                if normalized in {"failed", "canceled", "timed_out"}
                else terminal_status
            ), snapshot
        if (
            normalized in {"failed", "canceled", "timed_out"}
            and turn_state["boundarySource"] is not None
            and progress
        ):
            # A marked provider failure is stronger than a replayed successful
            # SSE frame. Preserve the provider's current terminal authority so
            # cleanup or downstream terminal-contract checks cannot be the
            # first place that discovers the failed turn.
            return normalized, snapshot
        inactive = _snapshot_projects_inactive_turn(snapshot)
        stable_candidate = bool(
            progress and inactive and not turn_state["unfinishedToolCall"]
        )
        signature = turn_state["signature"]
        if stable_candidate and isinstance(signature, tuple):
            if observation_was_slow or quiet_signature != signature:
                quiet_signature = signature
                quiet_since = observation_completed_at
            required_quiet_seconds = max(
                0.0,
                (
                    quiet_period_seconds
                    if turn_state["terminalAssistantAfterWork"]
                    else tool_only_quiet_period_seconds
                ),
            )
            if quiet_since is not None and (
                loop.time() - quiet_since >= required_quiet_seconds
            ):
                # A preamble assistant can launch another tool after the stale
                # idle/completed projection, and some turns end immediately
                # after a tool result without a final assistant. Accept either
                # shape only after the ordered transcript is inactive and
                # unchanged for a bounded quiet period.
                return (
                    normalized
                    if normalized in {"failed", "canceled", "timed_out"}
                    else terminal_status
                ), snapshot
        else:
            quiet_signature = None
            quiet_since = None
        _safe_heartbeat(
            {
                "omnigentSessionId": session_id,
                "normalizedStatus": (
                    normalized if normalized in _NON_TERMINAL_STATUSES else "running"
                ),
                "eventsCaptured": event_count,
                "firstMessagePosted": True,
                "terminalSnapshotPolling": True,
                "currentTurnProgress": progress,
                "currentTurnBoundarySource": turn_state["boundarySource"],
                "terminalAssistantAfterWork": bool(
                    turn_state["terminalAssistantAfterWork"]
                ),
                "unfinishedToolCall": bool(turn_state["unfinishedToolCall"]),
                "turnQuietSeconds": (
                    round(loop.time() - quiet_since, 3)
                    if quiet_since is not None
                    else 0.0
                ),
                "turnQuietTargetSeconds": (
                    max(
                        0.0,
                        (
                            quiet_period_seconds
                            if turn_state["terminalAssistantAfterWork"]
                            else tool_only_quiet_period_seconds
                        ),
                    )
                    if stable_candidate
                    else None
                ),
                "snapshotLatencySeconds": round(observation_latency_seconds, 3),
                "snapshotLatencyResetQuietWindow": observation_was_slow,
            }
        )
        await asyncio.sleep(max(0.1, interval_seconds))
    raise OmnigentSessionStillRunningError(
        "Omnigent current marked turn did not reach terminal state before timeout"
    )


def _inactive_marked_turn_terminal_status(
    snapshot: dict[str, Any],
    *,
    marker: str,
    baseline_item_ids: frozenset[str] | None,
) -> str | None:
    """Return terminal authority for a structurally complete inactive turn."""

    turn_state = _marked_turn_item_state(
        snapshot,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    if (
        not _snapshot_projects_inactive_turn(snapshot)
        or not turn_state["progress"]
        or turn_state["unfinishedToolCall"]
    ):
        return None
    normalized = normalize_omnigent_observation(snapshot)
    return (
        normalized
        if normalized in {"failed", "canceled", "timed_out"}
        else "completed"
    )


async def _reconcile_inactive_marked_turn(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    marker: str,
    baseline_item_ids: frozenset[str] | None,
    event_count: int,
    snapshot: dict[str, Any] | None = None,
    timeout_seconds: float = 1800.0,
    interval_seconds: float = 2.0,
    quiet_period_seconds: float = _MARKED_TURN_QUIET_PERIOD_SECONDS,
    tool_only_quiet_period_seconds: float = (
        _MARKED_TOOL_ONLY_QUIET_PERIOD_SECONDS
    ),
) -> tuple[str, dict[str, Any]] | None:
    """Recover a terminal turn whose SSE completion edge was not observed.

    Activity retry and stream reconnect can happen after Omnigent has already
    returned an interactive session to idle. The new stream then contains only
    liveness heartbeats, so waiting exclusively for another terminal frame can
    consume the Activity's entire ScheduleToClose budget. Reconcile only when
    the provider snapshot is inactive and the marked turn has completed tool
    structure; the existing bounded quiet-period poll remains the terminal
    authority and prevents stale idle projections from ending active work.
    """

    candidate = snapshot or await client.get_session(session_id)
    terminal_status = _inactive_marked_turn_terminal_status(
        candidate,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    if terminal_status is None:
        return None
    return await _await_marked_turn_terminal(
        client=client,
        session_id=session_id,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
        event_count=event_count,
        terminal_status=terminal_status,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        quiet_period_seconds=quiet_period_seconds,
        tool_only_quiet_period_seconds=tool_only_quiet_period_seconds,
    )


async def _enqueue_stream_events(
    *,
    client: OmnigentHttpClient,
    session_id: str,
    queue: asyncio.Queue[tuple[dict[str, Any], bool] | BaseException | None],
    message_posted: asyncio.Event,
) -> None:
    try:
        async for event in client.stream_events(session_id):
            await queue.put((event, message_posted.is_set()))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await queue.put(exc)
    finally:
        await queue.put(None)


async def _queued_stream_events(
    *,
    queue: asyncio.Queue[tuple[dict[str, Any], bool] | BaseException | None],
    stream_task: asyncio.Task[None],
) -> AsyncIterator[tuple[dict[str, Any], bool]]:
    while True:
        event = await queue.get()
        if event is None:
            break
        if isinstance(event, BaseException):
            raise event
        yield event
    if stream_task.done() and not stream_task.cancelled():
        stream_task.result()


async def _optional_stream_events(
    stream: AsyncIterator[Any] | None,
) -> AsyncIterator[Any]:
    """Yield a selected provider stream, or no items after prior reconciliation."""

    if stream is None:
        return
    async for item in stream:
        yield item


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
    first_message_posted: bool,
    first_message_response_identifiers: dict[str, str],
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    capture_policy: dict[str, Any] | None,
    external_state: dict[str, Any] | None = None,
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
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="canceled",
            diagnostics={
                "cancelled": True,
                "failureClass": "system_error",
            },
            harvest_resources=True,
            capture_policy=capture_policy,
            external_state=external_state,
        )


def _first_message_response_identifiers(
    response: dict[str, Any] | None = None,
    *,
    pending_id: object | None = None,
    item_id: object | None = None,
) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    if isinstance(response, dict):
        pending_id = response.get("pending_id", pending_id)
        item_id = response.get("item_id", item_id)
    for key, value in (("pendingId", pending_id), ("itemId", item_id)):
        text = str(value).strip() if value is not None else ""
        if text:
            identifiers[key] = text
    return identifiers


async def _cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Expected after requesting cancellation of a helper task.
        pass


def _activity_attempt() -> int:
    """Return the current Temporal attempt without requiring Activity context."""

    try:
        return max(1, int(activity.info().attempt))
    except RuntimeError:
        return 1


async def _publish_active_journals(
    *,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
) -> tuple[str, str]:
    """Finalize the current crash-safe journal prefix before its DB commit."""

    def jsonl(items: list[dict[str, Any]]) -> str:
        return "".join(
            f"{json.dumps(item, sort_keys=True, default=str)}\n" for item in items
        )

    prefix = f"{len(normalized_events):08d}"
    raw_ref = await artifact_gateway.write_text(
        request=request,
        name=f"runtime.omnigent.sse.raw.{prefix}.jsonl",
        payload=jsonl(redact_raw_events(raw_events)),
        link_type="runtime.omnigent.sse.raw",
        content_type="application/x-ndjson",
    )
    normalized_ref = await artifact_gateway.write_text(
        request=request,
        name=f"runtime.omnigent.sse.normalized.{prefix}.jsonl",
        payload=jsonl(normalized_events),
        link_type="runtime.omnigent.sse.normalized",
        content_type="application/x-ndjson",
    )
    return raw_ref, normalized_ref


async def _append_reconciled_terminal_snapshot(
    *,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    run_store: OmnigentBridgeSessionStore | None,
    bridge_session_id: str | None,
    session_id: str,
    terminal_status: str,
    snapshot: dict[str, Any],
    source: str,
    event_count: dict[str, int],
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
) -> None:
    """Persist the synthetic terminal edge recovered from provider state."""

    terminal_snapshot = dict(snapshot)
    terminal_snapshot["status"] = terminal_status
    normalized_bridge_event = build_omnigent_bridge_event(
        payload={
            "type": "session.final_snapshot",
            "session": terminal_snapshot,
            "metadata": {"terminalReconciliationSource": source},
        },
        sequence=event_count["value"] + 1,
        request=request,
        omnigent_session_id=session_id,
        bridge_session_id=bridge_session_id,
    )
    marker_digest = hashlib.sha256(
        f"{session_id}\n{_first_message_marker(request=request)}".encode()
    ).hexdigest()
    normalized_bridge_event.event["deduplicationKey"] = (
        f"terminal-reconciliation:{marker_digest}"
    )
    moonmind_metadata = normalized_bridge_event.event["metadata"]["moonmind"]
    moonmind_metadata["source"] = "omnigent_terminal_reconciliation"
    moonmind_metadata["terminalReconciliationSource"] = source
    normalized_events.append(normalized_bridge_event.event)
    event_count["value"] += 1
    if run_store is None or not bridge_session_id:
        return
    raw_ref, normalized_ref = await _publish_active_journals(
        artifact_gateway=artifact_gateway,
        request=request,
        raw_events=raw_events,
        normalized_events=normalized_events,
    )
    normalized_bridge_event.event["artifactRef"] = normalized_ref
    await run_store.attach_active_journal_refs(
        bridge_session_id,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
    )
    await run_store.append_events(
        bridge_session_id,
        [normalized_bridge_event.event],
    )


def _parse_jsonl(payload: str) -> list[dict[str, Any]]:
    """Parse a previously published journal without accepting non-object rows."""

    items: list[dict[str, Any]] = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _logger.warning("Skipping corrupted JSONL line in Omnigent journal")
            continue
        if isinstance(value, dict):
            items.append(value)
    return items


async def _restore_active_journals(
    *,
    artifact_gateway: OmnigentArtifactGateway,
    durable_row: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Restore the last committed journal prefix for an Activity retry.

    Journal refs are attached before the matching index commit, so a prefix may
    contain one harmless unindexed tail event. Reusing that prefix is safe: the
    event store deduplicates it when the retry reaches the same observation.
    """

    restored: list[list[dict[str, Any]]] = []
    for attribute in ("raw_events_ref", "normalized_events_ref"):
        ref = str(getattr(durable_row, attribute, None) or "").strip()
        if not ref:
            restored.append([])
            continue
        restored.append(_parse_jsonl(await artifact_gateway.read_text(ref)))
    return restored[0], restored[1]


async def run_omnigent_execution(
    request: AgentExecutionRequest,
    *,
    artifact_gateway: OmnigentArtifactGateway | None = None,
    run_store: OmnigentBridgeSessionStore | None = None,
    resume_session_id: str | None = None,
    first_message_text: str | None = None,
    defer_bridge_terminal: bool = False,
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
    first_message_posted = False
    first_message_response_identifiers: dict[str, str] = {}
    initial_snapshot: dict[str, Any] | None = None
    pre_dispatch_item_ids: frozenset[str] | None = None
    raw_events: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []
    event_diagnostics: list[dict[str, Any]] = []
    target_agent_id: str | None = None
    delete_after_harvest = False
    capture_policy: dict[str, Any] | None = None
    external_state: dict[str, Any] | None = None
    try:
        # §16 rule 1: authorize the MoonMind principal + workflow + AgentRun +
        # bridge session before any provider call. Fails closed on missing
        # identity through the non-retryable user-error result path below.
        authorization = authorize_bridge_access(request)
        # §16 rule 1: authorize the durable bridge session before any provider
        # call; refuse cross-owner reuse of an idempotency key.
        if run_store is not None:
            assert_bridge_session_binding(
                authorization,
                await run_store.get_binding(request.idempotency_key),
            )
        selection = build_omnigent_selection(request)
        capture_policy = selection.capture
        external_state = _new_external_state_evidence(
            endpoint_ref=selection.endpoint_ref or "default",
            idempotency_key=request.idempotency_key,
        )
        external_state.update(_profile_authorization_evidence(request))
        delete_after_harvest = bool(
            selection.capture.get("deleteOmnigentSessionAfterHarvest", False)
        )
        async with httpx.AsyncClient() as httpx_client:
            client = OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                client=httpx_client,
                upstream_header_allowlist=resolved_proxy_forward_headers(),
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
                default_agent=OmnigentAgentSelection(
                    agent_name=resolved_default_agent_name()
                ),
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
            durable_terminal_status: str | None = None
            bridge_session_id: str | None = None
            if run_store is not None:
                durable_row = await run_store.get_or_create(
                    request=request,
                    endpoint_ref=str(selection.endpoint_ref or "default"),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    target_metadata={
                        "hostType": selection.session.host_type,
                        "workspace": selection.session.workspace,
                    },
                )
                bridge_session_id = str(
                    getattr(durable_row, "bridge_session_id", "") or ""
                )
                restored_status = str(
                    getattr(durable_row, "status", "") or ""
                ).strip().lower()
                if (
                    restored_status in _TERMINAL_STATUSES
                    and getattr(durable_row, "first_message_posted_at", None)
                    is not None
                ):
                    durable_terminal_status = restored_status
                external_state["bridgeSessionId"] = bridge_session_id
                assert_bridge_session_binding(
                    authorization,
                    BridgeSessionBinding(
                        workflow_id=str(
                            getattr(
                                durable_row,
                                "moonmind_workflow_id",
                                authorization.workflow_id,
                            )
                        ),
                        agent_run_id=str(
                            getattr(
                                durable_row,
                                "moonmind_agent_run_id",
                                authorization.agent_run_id,
                            )
                        ),
                    ),
                )

            retry_state = _heartbeat_state()
            heartbeat_pre_dispatch_item_ids = (
                _validated_pre_dispatch_item_ids(retry_state["preDispatchItemIds"])
                if "preDispatchItemIds" in retry_state
                else None
            )
            durable_pre_dispatch_item_ids = _persisted_pre_dispatch_item_ids(
                durable_row
            )
            if (
                heartbeat_pre_dispatch_item_ids is not None
                and durable_pre_dispatch_item_ids is not None
                and heartbeat_pre_dispatch_item_ids != durable_pre_dispatch_item_ids
            ):
                raise OmnigentContractError(
                    "Omnigent retry item frontier conflicts with durable state"
                )
            pre_dispatch_item_ids = (
                durable_pre_dispatch_item_ids
                if durable_pre_dispatch_item_ids is not None
                else heartbeat_pre_dispatch_item_ids
            )
            durable_session_id = str(
                getattr(durable_row, "omnigent_session_id", None) or ""
            ).strip()
            heartbeat_session_id = _heartbeat_session_id(retry_state)
            requested_resume_session_id = str(resume_session_id or "").strip()
            resolved_existing_session_ids = {
                value
                for value in (
                    durable_session_id,
                    heartbeat_session_id,
                    requested_resume_session_id,
                )
                if value
            }
            if len(resolved_existing_session_ids) > 1:
                raise OmnigentContractError(
                    "Omnigent continuation session conflicts with durable retry state"
                )
            session_id = (
                durable_session_id
                or heartbeat_session_id
                or requested_resume_session_id
            )
            first_message_posted = bool(retry_state.get("firstMessagePosted"))
            first_message_reconcile_required = False
            if durable_row is not None:
                first_message_reconcile_required = (
                    durable_row.first_message_state == FIRST_MESSAGE_POSTING
                )
                first_message_posted = (
                    durable_row.first_message_state == FIRST_MESSAGE_POSTED
                    or (
                        durable_row.first_message_state == FIRST_MESSAGE_TERMINAL
                        and getattr(
                            durable_row, "first_message_posted_at", None
                        )
                        is not None
                    )
                )
                first_message_response_identifiers = _first_message_response_identifiers(
                    pending_id=getattr(durable_row, "first_message_pending_id", None),
                    item_id=getattr(durable_row, "first_message_item_id", None),
                )
                external_state["firstMessage"]["durableState"] = (
                    durable_row.first_message_state
                )
                if first_message_response_identifiers:
                    external_state["firstMessage"]["responseIdentifiers"] = dict(
                        first_message_response_identifiers
                    )
            if session_id:
                external_state["retry"].update(
                    {
                        "sessionResolution": "attached",
                        "attached": True,
                        "attachSource": (
                            "bridge_session_store"
                            if durable_session_id
                            else (
                                "coordinator_continuation"
                                if requested_resume_session_id
                                else "activity_heartbeat"
                            )
                        ),
                    }
                )
            if not session_id:
                with control_plane_spans.omnigent_span(
                    control_plane_spans.SESSION_ENSURE_PROVIDER_ATTACHMENT,
                    runtime="omnigent",
                    harness=str(session_payload.get("harness") or "unknown"),
                    attempt_ordinal=_activity_attempt(),
                ):
                    create_response = await client.create_session(session_payload)
                session_id = _session_id(create_response)
                external_state["retry"].update(
                    {
                        "sessionResolution": "created",
                        "attached": False,
                        "attachSource": None,
                    }
                )
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
            elif (
                run_store is not None
                and requested_resume_session_id
                and not durable_session_id
            ):
                await run_store.attach_session(
                    request.idempotency_key,
                    requested_resume_session_id,
                )
            with suppress(Exception):
                initial_snapshot = await client.get_session(session_id)
            record_session_created = (
                getattr(run_store, "record_session_created", None)
                if run_store is not None
                else None
            )
            if callable(record_session_created):
                snapshot_capabilities, snapshot_status = (
                    _session_authority_observation(initial_snapshot)
                )
                await record_session_created(
                    request.idempotency_key,
                    session_id=session_id,
                    agent_id=target.agent_id,
                    endpoint_ref=str(selection.endpoint_ref or "default"),
                    capabilities=snapshot_capabilities,
                    session_status=snapshot_status,
                )
            if (
                pre_dispatch_item_ids is None
                and not first_message_posted
                and not first_message_reconcile_required
            ):
                pre_dispatch_item_ids = _snapshot_item_ids(initial_snapshot)
                if pre_dispatch_item_ids is not None and run_store is not None:
                    record_frontier = getattr(
                        run_store, "record_first_message_item_frontier", None
                    )
                    if callable(record_frontier):
                        durable_row = await record_frontier(
                            request.idempotency_key,
                            item_ids=sorted(pre_dispatch_item_ids),
                        )
                        persisted_frontier = _persisted_pre_dispatch_item_ids(
                            durable_row
                        )
                        if persisted_frontier is not None:
                            pre_dispatch_item_ids = persisted_frontier
            if pre_dispatch_item_ids is not None:
                _safe_heartbeat(
                    {
                        "omnigentSessionId": session_id,
                        "firstMessagePosted": first_message_posted,
                        "preDispatchItemIds": sorted(pre_dispatch_item_ids),
                    }
                )

            if first_message_text is not None:
                first_message = {
                    "type": "message",
                    "data": {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": str(first_message_text).strip(),
                            }
                        ],
                    },
                }
                first_message["data"]["content"][0]["text"] = (
                    f"{_first_message_text(first_message)}\n\n"
                    f"{_first_message_marker(request=request)}"
                ).strip()
                prepared_digest = hashlib.sha256(
                    json.dumps(
                        first_message,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                retrieval_evidence = {
                    "state": "disabled",
                    "mode": "session_continuation",
                    "reason": "repository_publication_terminal_contract",
                    "preparedMessageDigest": prepared_digest,
                    "preparedMessageRef": await artifact_gateway.write_json(
                        request=request,
                        name="input.omnigent.first_message.prepared.json",
                        payload=first_message,
                        link_type="input.omnigent.first_message.prepared",
                    ),
                    "firstMessageConsumedContextRef": False,
                }
            else:
                first_message = await _build_omnigent_first_message(
                    request=request,
                    prompt=selection.prompt,
                    artifact_gateway=artifact_gateway,
                )
                first_message, retrieval_evidence = await _resolve_initial_context_message(
                    request=request,
                    first_message=first_message,
                    artifact_gateway=artifact_gateway,
                    run_store=run_store,
                    durable_row=durable_row,
                    workspace=selection.session.workspace,
                    include_idempotency_marker=selection.prompt.get(
                        "includeIdempotencyMarker", True
                    ),
                )
            digest = str(retrieval_evidence["preparedMessageDigest"])
            marker = _first_message_marker(request=request)
            external_state["firstMessage"].update(
                {
                    "digest": digest,
                    "idempotencyMarkerPresent": selection.prompt.get(
                        "includeIdempotencyMarker", True
                    ),
                    "postedBeforeRetry": first_message_posted,
                    "reconcileRequired": first_message_reconcile_required,
                    "state": "posted" if first_message_posted else "prepared",
                }
            )
            retrieval_evidence["firstMessageDigest"] = digest
            external_state["initialRetrieval"] = retrieval_evidence
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
                    external_state["firstMessage"]["durableState"] = (
                        durable_row.first_message_state
                    )
                    external_state["firstMessage"][
                        "reconcileRequired"
                    ] = first_message_reconcile_required
                except OmnigentDigestMismatchError as exc:
                    external_state["retry"].update(
                        {
                            "firstMessageOutcome": "unrecoverable_mismatch",
                            "mismatchReason": "digest_mismatch",
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
                        first_message_posted=first_message_posted,
                        first_message_response_identifiers=first_message_response_identifiers,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": str(exc),
                            "nonRetryable": True,
                            "failureClass": classify_omnigent_failure(
                                OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH
                            ),
                        },
                        harvest_resources=False,
                        external_state=external_state,
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
                        failure_reason=(
                            OmnigentFailureReason.FIRST_MESSAGE_DIGEST_MISMATCH
                        ),
                    )
            stream_queue: asyncio.Queue[
                tuple[dict[str, Any], bool] | BaseException | None
            ] | None = None
            message_posted_gate = asyncio.Event()
            if first_message_reconcile_required:
                reconciliation_snapshot = await client.get_session(session_id)
                if not _snapshot_contains_first_message_marker(
                    reconciliation_snapshot,
                    digest=digest,
                    marker=marker,
                ):
                    external_state["retry"].update(
                        {
                            "firstMessageOutcome": "unrecoverable_mismatch",
                            "mismatchReason": "reconcile_failed",
                            "reconciliationChecked": True,
                            "markerFound": False,
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
                        first_message_posted=first_message_posted,
                        first_message_response_identifiers=first_message_response_identifiers,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                        terminal_status="failed",
                        diagnostics={
                            "error": "Unable to reconcile first-message posting state",
                            "failureClass": classify_omnigent_failure(
                                OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION
                            ),
                        },
                        harvest_resources=False,
                        external_state=external_state,
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
                        failure_reason=(
                            OmnigentFailureReason.AMBIGUOUS_POSTING_RECONCILIATION
                        ),
                    )
                if run_store is not None:
                    await run_store.mark_posted(request.idempotency_key)
                first_message_posted = True
                external_state["retry"].update(
                    {
                        "firstMessageOutcome": "reconciled",
                        "reconciliationChecked": True,
                        "markerFound": True,
                    }
                )
                external_state["firstMessage"]["state"] = "posted"
            if not first_message_posted:
                stream_queue = asyncio.Queue()
                stream_task = asyncio.create_task(
                    _enqueue_stream_events(
                        client=client,
                        session_id=session_id,
                        queue=stream_queue,
                        message_posted=message_posted_gate,
                    )
                )
                await asyncio.sleep(0)
                if run_store is not None:
                    await run_store.mark_posting(request.idempotency_key)
                # The server may emit current-turn SSE events before the POST
                # response is returned. Open the current-turn gate immediately
                # before dispatch; anything already queued remains pre-post
                # replay, while events emitted during request handling are live.
                message_posted_gate.set()
                with control_plane_spans.omnigent_span(
                    control_plane_spans.TURN_SUBMIT,
                    runtime="omnigent",
                    attempt_ordinal=_activity_attempt(),
                ):
                    first_message_response = await client.post_event(
                        session_id, first_message
                    )
                first_message_posted = True
                first_message_response_identifiers = _first_message_response_identifiers(
                    first_message_response
                )
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
                external_state["retry"]["firstMessageOutcome"] = "posted"
                external_state["firstMessage"]["state"] = "posted"
            elif external_state["retry"]["firstMessageOutcome"] == "pending":
                external_state["retry"]["firstMessageOutcome"] = "already_posted"
                external_state["firstMessage"]["state"] = "posted"

            durable_cursor = 0
            if durable_row is not None and bridge_session_id:
                raw_events, normalized_events = await _restore_active_journals(
                    artifact_gateway=artifact_gateway,
                    durable_row=durable_row,
                )
                if normalized_events:
                    # Refs are switched before the matching index commit. Reconcile
                    # any durable artifact tail first so a retry cannot lose it.
                    await run_store.append_events(bridge_session_id, normalized_events)
                previous_rows = await run_store.list_events(bridge_session_id)
                durable_cursor = max(
                    (
                        int(
                            (
                                (row.metadata_ or {}).get("reconciliation") or {}
                            ).get("streamCursor")
                            or 0
                        )
                        for row in previous_rows
                    ),
                    default=0,
                )
                if durable_cursor:
                    # The current Omnigent stream endpoint has no cursor/resume
                    # parameter. Preserve that discontinuity explicitly and once
                    # instead of implying that the new connection is contiguous.
                    gap_event = build_omnigent_bridge_event(
                        payload={
                            "type": "stream.resume_gap",
                            "status": "running",
                            "metadata": {
                                "reason": "upstream_replay_unavailable",
                                "lastDurableCursor": durable_cursor,
                            },
                        },
                        sequence=durable_cursor + 1,
                        request=request,
                        omnigent_session_id=session_id,
                        bridge_session_id=bridge_session_id,
                    ).event
                    gap_event["deduplicationKey"] = f"resume-gap:{durable_cursor}"
                    normalized_events.append(gap_event)
                    raw_ref, normalized_ref = await _publish_active_journals(
                        artifact_gateway=artifact_gateway,
                        request=request,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                    )
                    gap_event["artifactRef"] = normalized_ref
                    await run_store.attach_active_journal_refs(
                        bridge_session_id, raw_ref=raw_ref, normalized_ref=normalized_ref
                    )
                    await run_store.append_events(bridge_session_id, [gap_event])
                    durable_cursor += 1

            event_count = {"value": durable_cursor}
            heartbeat_status = {"value": "running"}
            terminal_status = durable_terminal_status
            terminal_snapshot_override: dict[str, Any] | None = None
            if durable_terminal_status is not None:
                terminal_snapshot_override = dict(initial_snapshot or {})
                terminal_snapshot_override["status"] = durable_terminal_status
                durable_terminal_refs = dict(
                    getattr(durable_row, "terminal_refs", None) or {}
                )
                durable_summary = str(
                    durable_terminal_refs.get("summary") or ""
                ).strip()
                if durable_summary:
                    terminal_snapshot_override["summary"] = durable_summary
                external_state["terminalReconciliation"] = {
                    "source": "durable_bridge_terminal",
                    "status": durable_terminal_status,
                }
            if (
                terminal_status is None
                and first_message_posted
                and bool(external_state["retry"].get("attached"))
                and isinstance(initial_snapshot, dict)
            ):
                reattached_terminal = await _reconcile_inactive_marked_turn(
                    client=client,
                    session_id=session_id,
                    marker=marker,
                    baseline_item_ids=pre_dispatch_item_ids,
                    event_count=event_count["value"],
                    snapshot=initial_snapshot,
                )
                if reattached_terminal is not None:
                    (
                        terminal_status,
                        terminal_snapshot_override,
                    ) = reattached_terminal
                    external_state["terminalReconciliation"] = {
                        "source": "reattached_inactive_snapshot",
                        "status": terminal_status,
                    }
                    await _append_reconciled_terminal_snapshot(
                        artifact_gateway=artifact_gateway,
                        request=request,
                        run_store=run_store,
                        bridge_session_id=bridge_session_id,
                        session_id=session_id,
                        terminal_status=terminal_status,
                        snapshot=terminal_snapshot_override,
                        source="reattached_inactive_snapshot",
                        event_count=event_count,
                        raw_events=raw_events,
                        normalized_events=normalized_events,
                    )
            if terminal_status is None:
                heartbeat_task = asyncio.create_task(
                    _periodic_stream_heartbeat(
                        session_id=session_id,
                        event_count=event_count,
                        status=heartbeat_status,
                    )
                )
            next_terminal_reconciliation_at = 0.0
            try:
                stream_events = None
                if terminal_status is None:
                    stream_events = (
                        _queued_stream_events(
                            queue=stream_queue,
                            stream_task=stream_task,
                        )
                        if stream_queue is not None and stream_task is not None
                        else client.stream_events(session_id)
                    )
                async for stream_item in _optional_stream_events(stream_events):
                    if isinstance(stream_item, tuple):
                        event, arrived_after_message_post = stream_item
                    else:
                        event = stream_item
                        arrived_after_message_post = True
                    event_count["value"] += 1
                    raw_events.append(dict(event))
                    normalized_bridge_event = build_omnigent_bridge_event(
                        payload=event,
                        sequence=event_count["value"],
                        request=request,
                        omnigent_session_id=session_id,
                        bridge_session_id=bridge_session_id,
                    )
                    if normalized_bridge_event.diagnostic is not None:
                        event_diagnostics.append(normalized_bridge_event.diagnostic)
                    normalized_events.append(normalized_bridge_event.event)
                    if run_store is not None and bridge_session_id:
                        # Durability policy: publish the redacted journals first,
                        # then commit each normalized index row. A crash can leave
                        # an unreferenced artifact, never a DB row whose evidence
                        # does not exist. Per-event commits favor loss bounds over
                        # throughput for this interactive stream.
                        raw_ref, normalized_ref = await _publish_active_journals(
                            artifact_gateway=artifact_gateway,
                            request=request,
                            raw_events=raw_events,
                            normalized_events=normalized_events,
                        )
                        normalized_bridge_event.event["artifactRef"] = normalized_ref
                        await run_store.attach_active_journal_refs(
                            bridge_session_id,
                            raw_ref=raw_ref,
                            normalized_ref=normalized_ref,
                        )
                        await run_store.append_events(
                            bridge_session_id, [normalized_bridge_event.event]
                        )
                    normalized = normalized_bridge_event.event["normalizedStatus"]
                    _safe_heartbeat(
                        {
                            "omnigentSessionId": session_id,
                            "normalizedStatus": normalized,
                            "eventsCaptured": event_count["value"],
                            "firstMessagePosted": True,
                            "eventType": normalized_bridge_event.event["type"],
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
                    if normalized_bridge_event.event["type"] in {
                        "response.heartbeat",
                        "session.heartbeat",
                    }:
                        loop_time = asyncio.get_running_loop().time()
                        if loop_time >= next_terminal_reconciliation_at:
                            next_terminal_reconciliation_at = (
                                loop_time
                                + _TERMINAL_RECONCILIATION_INTERVAL_SECONDS
                            )
                            reconciled_terminal = (
                                await _reconcile_inactive_marked_turn(
                                    client=client,
                                    session_id=session_id,
                                    marker=marker,
                                    baseline_item_ids=pre_dispatch_item_ids,
                                    event_count=event_count["value"],
                                )
                            )
                            if reconciled_terminal is not None:
                                (
                                    terminal_status,
                                    terminal_snapshot_override,
                                ) = reconciled_terminal
                                heartbeat_reconciliation_source = (
                                    normalized_bridge_event.event["type"].replace(
                                        ".", "_"
                                    )
                                    + "_snapshot"
                                )
                                external_state["terminalReconciliation"] = {
                                    "source": heartbeat_reconciliation_source,
                                    "status": terminal_status,
                                }
                                await _append_reconciled_terminal_snapshot(
                                    artifact_gateway=artifact_gateway,
                                    request=request,
                                    run_store=run_store,
                                    bridge_session_id=bridge_session_id,
                                    session_id=session_id,
                                    terminal_status=terminal_status,
                                    snapshot=terminal_snapshot_override,
                                    source=heartbeat_reconciliation_source,
                                    event_count=event_count,
                                    raw_events=raw_events,
                                    normalized_events=normalized_events,
                                )
                                heartbeat_status["value"] = terminal_status
                                break
                    if normalized in {
                        "completed",
                        "failed",
                        "canceled",
                        "timed_out",
                        "idle",
                    }:
                        # Native Codex reports a successful turn by returning
                        # the interactive session to idle. Treat that edge as
                        # a completion candidate only; the marked-turn
                        # structural and quiescence checks below still own the
                        # terminal decision.
                        terminal_event_status = (
                            "completed" if normalized == "idle" else normalized
                        )
                        terminal_snapshot = await client.get_session(session_id)
                        current_turn_progress = (
                            _snapshot_confirms_current_turn_terminal(
                                terminal_snapshot,
                                marker=marker,
                                baseline_item_ids=pre_dispatch_item_ids,
                            )
                        )
                        if arrived_after_message_post and not current_turn_progress:
                            marker_visible = _snapshot_contains_first_message_marker(
                                terminal_snapshot,
                                digest=digest,
                                marker=marker,
                            )
                            if marker_visible:
                                for _ in range(20):
                                    await asyncio.sleep(0.25)
                                    terminal_snapshot = await client.get_session(
                                        session_id
                                    )
                                    current_turn_progress = (
                                        _snapshot_confirms_current_turn_terminal(
                                            terminal_snapshot,
                                            marker=marker,
                                            baseline_item_ids=pre_dispatch_item_ids,
                                        )
                                    )
                                    if current_turn_progress:
                                        break
                        if not current_turn_progress and not arrived_after_message_post:
                            # A terminal frame queued before the message was
                            # posted belongs to the preceding turn. Keep the
                            # stream open for the current turn's terminal event
                            # instead of accepting stale completion.
                            continue
                        (
                            terminal_status,
                            terminal_snapshot_override,
                        ) = await _await_marked_turn_terminal(
                            client=client,
                            session_id=session_id,
                            marker=marker,
                            baseline_item_ids=pre_dispatch_item_ids,
                            event_count=event_count["value"],
                            terminal_status=terminal_event_status,
                        )
                        if normalized == "idle" and terminal_status == "completed":
                            completed_snapshot = dict(
                                terminal_snapshot_override or terminal_snapshot
                            )
                            completed_snapshot["status"] = "completed"
                            normalized_bridge_event = build_omnigent_bridge_event(
                                payload={
                                    "type": "session.final_snapshot",
                                    "session": completed_snapshot,
                                },
                                sequence=event_count["value"] + 1,
                                request=request,
                                omnigent_session_id=session_id,
                                bridge_session_id=bridge_session_id,
                            )
                            normalized_events.append(normalized_bridge_event.event)
                            event_count["value"] += 1
                            if run_store is not None and bridge_session_id:
                                raw_ref, normalized_ref = (
                                    await _publish_active_journals(
                                        artifact_gateway=artifact_gateway,
                                        request=request,
                                        raw_events=raw_events,
                                        normalized_events=normalized_events,
                                    )
                                )
                                normalized_bridge_event.event["artifactRef"] = (
                                    normalized_ref
                                )
                                await run_store.attach_active_journal_refs(
                                    bridge_session_id,
                                    raw_ref=raw_ref,
                                    normalized_ref=normalized_ref,
                                )
                                await run_store.append_events(
                                    bridge_session_id,
                                    [normalized_bridge_event.event],
                                )
                        heartbeat_status["value"] = terminal_status
                        break
                    if event_count["value"] % 8 == 0:
                        _safe_heartbeat(
                            {
                                "omnigentSessionId": session_id,
                                "normalizedStatus": normalized,
                                "eventsCaptured": event_count["value"],
                                "firstMessagePosted": True,
                            }
                        )
            finally:
                await _cancel_task(heartbeat_task)
                await _cancel_task(stream_task)

            final_snapshot = terminal_snapshot_override or await client.get_session(
                session_id
            )
            if terminal_status is None:
                normalized_snapshot = normalize_omnigent_observation(final_snapshot)
                if normalized_snapshot in {
                    "completed",
                    "failed",
                    "canceled",
                    "timed_out",
                }:
                    if isinstance(final_snapshot.get("items"), list):
                        turn_state = _marked_turn_item_state(
                            final_snapshot,
                            marker=marker,
                            baseline_item_ids=pre_dispatch_item_ids,
                        )
                        if not turn_state["progress"]:
                            raise OmnigentSessionStillRunningError(
                                "Omnigent stream ended before the current marked turn "
                                "produced provider work"
                            )
                        terminal_status, final_snapshot = (
                            await _await_marked_turn_terminal(
                                client=client,
                                session_id=session_id,
                                marker=marker,
                                baseline_item_ids=pre_dispatch_item_ids,
                                event_count=event_count["value"],
                                terminal_status=normalized_snapshot,
                            )
                        )
                    else:
                        terminal_status = normalized_snapshot
                    # The stream ended without emitting a terminal event but the
                    # final snapshot is terminal. Append an indexed terminal event
                    # derived from the snapshot so the durable event index records
                    # how the run ended; otherwise diagnostics/Workflow Chat would
                    # see a terminal session row with no terminal event (§7.2).
                    normalized_bridge_event = build_omnigent_bridge_event(
                        payload={
                            "type": "session.final_snapshot",
                            "session": final_snapshot,
                        },
                        sequence=event_count["value"] + 1,
                        request=request,
                        omnigent_session_id=session_id,
                        bridge_session_id=bridge_session_id,
                    )
                    normalized_events.append(normalized_bridge_event.event)
                    event_count["value"] += 1
                    if run_store is not None and bridge_session_id:
                        raw_ref, normalized_ref = await _publish_active_journals(
                            artifact_gateway=artifact_gateway,
                            request=request,
                            raw_events=raw_events,
                            normalized_events=normalized_events,
                        )
                        normalized_bridge_event.event["artifactRef"] = normalized_ref
                        await run_store.attach_active_journal_refs(
                            bridge_session_id,
                            raw_ref=raw_ref,
                            normalized_ref=normalized_ref,
                        )
                        await run_store.append_events(
                            bridge_session_id, [normalized_bridge_event.event]
                        )
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
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status=terminal_status,
                diagnostics={
                    "failureClass": failure_class_for_terminal_status(terminal_status),
                    "eventDiagnostics": event_diagnostics,
                },
                harvest_resources=True,
                external_state=external_state,
                capture_policy=capture_policy,
            )
            terminal_refs = build_omnigent_terminal_refs(
                bundle,
                terminal_status=terminal_status,
                final_snapshot=final_snapshot,
            )
            if run_store is not None and not defer_bridge_terminal:
                await run_store.mark_terminal(
                    request.idempotency_key,
                    status=terminal_status,
                    terminal_refs=terminal_refs,
                    # Persist the full, non-lossy normalized status stream into
                    # the durable event index (OmnigentBridge §7.2).
                    events=normalized_events,
                )
            # §17: an optional resource-harvest failure resolves to
            # completed-with-diagnostics unless policy requires full evidence,
            # in which case the missing required evidence escalates.
            harvest_failure_reason: OmnigentFailureReason | None = None
            if (
                terminal_status == "completed"
                and bundle.resource_harvest_failure_class
            ):
                harvest_failure_reason = (
                    OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED
                )
            result = build_omnigent_result(
                request=request,
                terminal_status=terminal_status,
                session_id=session_id,
                agent_id=target.agent_id,
                final_snapshot=final_snapshot,
                event_count=event_count["value"],
                capture_bundle=bundle,
                failure_reason=harvest_failure_reason,
                require_full_evidence=harvest_failure_reason is not None,
                failure_summary=(
                    "Required Omnigent resource evidence was missing after "
                    "session completion"
                    if harvest_failure_reason is not None
                    else None
                ),
                provider_error_code=(
                    "omnigent_required_resource_evidence_missing"
                    if harvest_failure_reason is not None
                    else None
                ),
            )
            if defer_bridge_terminal:
                result_metadata = dict(result.metadata or {})
                result_metadata["deferredBridgeTerminal"] = {
                    "idempotencyKey": request.idempotency_key,
                    "status": terminal_status,
                    "terminalRefs": terminal_refs,
                }
                result = result.model_copy(update={"metadata": result_metadata})
            return result
    except asyncio.CancelledError:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if client is not None and session_id:
            async with httpx.AsyncClient() as cleanup_httpx_client:
                cleanup_client = OmnigentHttpClient(
                    base_url=resolved_server_url(),
                    api_token=resolved_api_token(),
                    client=cleanup_httpx_client,
                    upstream_header_allowlist=resolved_proxy_forward_headers(),
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
                    first_message_posted=first_message_posted,
                    first_message_response_identifiers=first_message_response_identifiers,
                    raw_events=raw_events,
                    normalized_events=normalized_events,
                    capture_policy=capture_policy,
                    external_state=external_state,
                )
                if delete_after_harvest:
                    with suppress(Exception):
                        await cleanup_client.delete_session(session_id)
        raise
    except OmnigentArtifactError as exc:
        # §17: required artifact-persistence failure -> system_error
        # (MoonMind artifact authority failed).
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if client is not None and session_id:
            with suppress(Exception):
                await _cancel_omnigent_session(client, session_id)
        if run_store is not None:
            with suppress(Exception):
                await run_store.mark_terminal(
                    request.idempotency_key,
                    status="failed",
                    events=normalized_events,
                )
        failure_class = classify_omnigent_failure(
            OmnigentFailureReason.REQUIRED_ARTIFACT_PERSISTENCE_FAILED
        )
        final_snapshot = {"status": "failed", "summary": str(exc)}
        try:
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
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status="failed",
                diagnostics={
                    "error": str(exc),
                    "failureClass": failure_class,
                    "artifactAuthorityFailed": True,
                },
                harvest_resources=False,
                external_state=external_state,
                capture_policy=capture_policy,
            )
        except OmnigentArtifactError:
            # Artifact authority is unavailable even for evidence capture;
            # still surface the system_error terminal outcome.
            return AgentRunResult(
                summary=_compact_summary(
                    exc, fallback="Omnigent artifact persistence failed"
                ),
                failureClass=failure_class,
                providerErrorCode="omnigent_artifact_persistence_failed",
                metadata={
                    "normalizedStatus": "failed",
                    "providerName": "omnigent",
                    "artifactAuthorityFailed": True,
                },
            )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(
                exc, fallback="Omnigent artifact persistence failed"
            ),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode="omnigent_artifact_persistence_failed",
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )
    except OmnigentAuthorizationError as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        failure_class = exc.failure_class
        final_snapshot = {"status": "failed", "summary": str(exc)}
        diagnostics = {
            "error": str(exc),
            "failureClass": failure_class,
            "authorizationDenied": True,
        }
        try:
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
                first_message_posted=first_message_posted,
                first_message_response_identifiers=first_message_response_identifiers,
                raw_events=raw_events,
                normalized_events=normalized_events,
                terminal_status="failed",
                diagnostics=diagnostics,
                harvest_resources=False,
                external_state=external_state,
                capture_policy=capture_policy,
            )
        except OmnigentArtifactError:
            return AgentRunResult(
                summary=_compact_summary(
                    exc, fallback="Omnigent bridge authorization denied"
                ),
                failureClass=failure_class,
                providerErrorCode="omnigent_authorization_denied",
                metadata={
                    "normalizedStatus": "failed",
                    "providerName": "omnigent",
                    "authorizationDenied": True,
                },
            )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(
                exc, fallback="Omnigent bridge authorization denied"
            ),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode="omnigent_authorization_denied",
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                "authorizationDenied": True,
                **bundle.metadata_refs,
            },
        )
    except (
        OmnigentContractError,
        OmnigentAdapterError,
        ValueError,
    ) as exc:
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        if isinstance(exc, OmnigentAdapterError) and exc.failure_class == classify_omnigent_failure(
            OmnigentFailureReason.INVALID_SESSION_PAYLOAD
        ):
            # §17: invalid session-create payload -> user_error.
            failure_reason = OmnigentFailureReason.INVALID_SESSION_PAYLOAD
            provider_error_code = "omnigent_invalid_session_payload"
        else:
            # Contract/adapter integration faults surface as the §17
            # integration rows (upstream/host register/connect).
            failure_reason = OmnigentFailureReason.HOST_REGISTER_CONNECT
            provider_error_code = "omnigent_contract_error"
        failure_class = classify_omnigent_failure(failure_reason)
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
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics={
                "error": str(exc),
                "failureClass": failure_class,
            },
            harvest_resources=bool(client and session_id),
            external_state=external_state,
            capture_policy=capture_policy,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent contract error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode=provider_error_code,
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )
    except OmnigentSessionStillRunningError:
        raise
    except (OmnigentClientError, httpx.HTTPError) as exc:
        # §17 transport rows: upstream unreachable / host register-connect /
        # auth failure map to integration_error, while 4xx client-input
        # failures (invalid session payload) map to user_error. The client
        # already classified this via the shared §17 classifier and preserves
        # redacted host/server diagnostics through OmnigentClientError.
        await _cancel_task(heartbeat_task)
        await _cancel_task(stream_task)
        status_code = exc.status_code if isinstance(exc, OmnigentClientError) else None
        transport_failure_class = classify_omnigent_failure(
            OmnigentFailureReason.HOST_REGISTER_CONNECT
        )
        failure_class = (
            exc.failure_class
            if isinstance(exc, OmnigentClientError)
            else transport_failure_class
        )
        final_snapshot = {"status": "failed", "summary": str(exc)}
        diagnostics = (
            exc.diagnostics()
            if isinstance(exc, OmnigentClientError)
            else {"error": str(exc), "failureClass": transport_failure_class}
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
            first_message_posted=first_message_posted,
            first_message_response_identifiers=first_message_response_identifiers,
            raw_events=raw_events,
            normalized_events=normalized_events,
            terminal_status="failed",
            diagnostics=diagnostics,
            harvest_resources=bool(client and session_id),
            external_state=external_state,
            capture_policy=capture_policy,
        )
        return AgentRunResult(
            outputRefs=bundle.output_refs,
            summary=_compact_summary(exc, fallback="Omnigent integration error"),
            diagnosticsRef=bundle.diagnostics_ref,
            failureClass=failure_class,
            providerErrorCode=str(status_code or "omnigent_http_error"),
            metadata={
                "normalizedStatus": "failed",
                "providerName": "omnigent",
                **bundle.metadata_refs,
            },
        )


__all__ = [
    "OmnigentContractError",
    "OmnigentSessionStillRunningError",
    "normalize_omnigent_observation",
    "run_omnigent_execution",
]
