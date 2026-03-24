"""Bounded manifest-ingest runtime helpers.

This module keeps manifest-specific update and query behavior in one place so the
execution service can expose a deterministic manifest control plane without
embedding large payloads in API responses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from temporalio import workflow
from yaml import YAMLError, safe_load

from api_service.db.models import MoonMindWorkflowState, TemporalExecutionRecord
from moonmind.schemas.manifest_ingest_models import (
    CompiledManifestPlanModel,
    ManifestExecutionPolicyModel,
    ManifestIngestSummaryModel,
    ManifestNodeCountsModel,
    ManifestNodeModel,
    ManifestNodeMutationRequestModel,
    ManifestNodePageModel,
    ManifestPlanNodeModel,
    ManifestRunIndexEntryModel,
    ManifestRunIndexModel,
    ManifestStatusSnapshotModel,
    ManifestUpdateManifestRequestModel,
    RequestedByModel,
    manifest_node_counts_from_nodes,
)
from moonmind.workflows.tasks.manifest_contract import (
    ManifestContractError,
    normalize_manifest_job_payload,
)

DEFAULT_MANIFEST_FAILURE_POLICY = "fail_fast"
DEFAULT_MANIFEST_MAX_CONCURRENCY = 50
MAX_MANIFEST_NODE_PAGE_SIZE = 200
MANIFEST_UPDATE_NAMES: set[str] = {
    "UpdateManifest",
    "SetConcurrency",
    "Pause",
    "Resume",
    "CancelNodes",
    "RetryNodes",
}
_MUTABLE_NODE_STATES = {"pending", "ready", "running"}


class ManifestIngestValidationError(ValueError):
    """Raised when manifest-specific update or query input is invalid."""


def ensure_manifest_execution(record: TemporalExecutionRecord) -> None:
    """Fail closed unless the provided record is a manifest ingest execution."""

    if record.workflow_type.value != "MoonMind.ManifestIngest":
        raise ManifestIngestValidationError(
            "Manifest-specific operations require workflowType MoonMind.ManifestIngest"
        )


def initialize_manifest_projection(record: TemporalExecutionRecord) -> None:
    """Ensure the execution record carries bounded manifest projection metadata."""

    ensure_manifest_execution(record)
    parameters = dict(record.parameters or {})
    memo = dict(record.memo or {})
    requested_by = _requested_by_from_record(record, parameters)
    execution_policy = _execution_policy_from_parameters(parameters)
    node_items = _node_items(parameters)
    counts = manifest_node_counts_from_nodes(node_items)

    parameters["requestedBy"] = requested_by.model_dump(by_alias=True)
    parameters["executionPolicy"] = execution_policy.model_dump(by_alias=True)
    parameters["manifestNodes"] = [
        item.model_dump(by_alias=True, mode="json") for item in node_items
    ]

    memo["manifest_phase"] = _default_phase(record)
    memo["manifest_counts"] = counts.model_dump(by_alias=True)
    memo.setdefault("summary_artifact_ref", _artifact_ref(memo, "summary_artifact_ref"))
    memo.setdefault(
        "run_index_artifact_ref",
        _artifact_ref(memo, "run_index_artifact_ref"),
    )
    memo.setdefault(
        "checkpoint_artifact_ref",
        _artifact_ref(memo, "checkpoint_artifact_ref"),
    )

    record.parameters = parameters
    record.memo = memo


def build_manifest_status_snapshot(
    record: TemporalExecutionRecord,
) -> ManifestStatusSnapshotModel:
    """Return the bounded manifest status snapshot exposed by API queries."""

    ensure_manifest_execution(record)
    parameters = dict(record.parameters or {})
    memo = dict(record.memo or {})
    requested_by = _requested_by_from_record(record, parameters)
    execution_policy = _execution_policy_from_parameters(parameters)
    node_items = _node_items(parameters)
    counts = _counts_from_record(parameters, memo, node_items)

    return ManifestStatusSnapshotModel(
        workflowId=record.workflow_id,
        runId=record.run_id,
        state=_manifest_lifecycle_state(record),
        phase=str(memo.get("manifest_phase") or _default_phase(record)),
        paused=bool(record.paused),
        maxConcurrency=execution_policy.max_concurrency,
        failurePolicy=execution_policy.failure_policy,
        counts=counts,
        requestedBy=requested_by,
        executionPolicy=execution_policy,
        manifestArtifactRef=record.manifest_ref,
        planArtifactRef=record.plan_ref,
        summaryArtifactRef=_artifact_ref(memo, "summary_artifact_ref"),
        runIndexArtifactRef=_artifact_ref(memo, "run_index_artifact_ref"),
        checkpointArtifactRef=_artifact_ref(memo, "checkpoint_artifact_ref"),
        updatedAt=record.updated_at,
    )


def list_manifest_nodes(
    record: TemporalExecutionRecord,
    *,
    state: str | None,
    cursor: str | None,
    limit: int,
) -> ManifestNodePageModel:
    """Return a bounded manifest node page from the execution projection payload."""

    ensure_manifest_execution(record)
    requested_limit = max(1, min(limit, MAX_MANIFEST_NODE_PAGE_SIZE))
    offset = _decode_cursor(cursor)
    requested_by = _requested_by_from_record(record, record.parameters or {})
    nodes = _node_items(record.parameters or {}, requested_by=requested_by)

    filtered: list[ManifestNodeModel] = []
    state_filter = str(state or "").strip().lower()
    if state_filter:
        for node in nodes:
            if node.state == state_filter:
                filtered.append(node)
    else:
        filtered = nodes

    items = filtered[offset : offset + requested_limit]
    next_cursor = (
        _encode_cursor(offset + requested_limit)
        if offset + requested_limit < len(filtered)
        else None
    )
    return ManifestNodePageModel(
        items=items,
        nextCursor=next_cursor,
        count=len(filtered),
    )


def apply_manifest_update(
    record: TemporalExecutionRecord,
    *,
    update_name: str,
    new_manifest_artifact_ref: str | None,
    mode: str | None,
    max_concurrency: int | None,
    node_ids: Sequence[str] | None,
) -> dict[str, Any]:
    """Apply one manifest-specific update to the execution projection record."""

    ensure_manifest_execution(record)
    initialize_manifest_projection(record)
    parameters = dict(record.parameters or {})
    memo = dict(record.memo or {})
    nodes = _node_items(parameters)
    now = datetime.now(UTC)

    if update_name == "UpdateManifest":
        update_request = ManifestUpdateManifestRequestModel(
            newManifestArtifactRef=new_manifest_artifact_ref,
            mode=mode,
        )
        record.manifest_ref = update_request.new_manifest_artifact_ref
        refs = list(record.artifact_refs or [])
        if update_request.new_manifest_artifact_ref not in refs:
            refs.append(update_request.new_manifest_artifact_ref)
            record.artifact_refs = refs
        parameters["manifestUpdate"] = update_request.model_dump(by_alias=True)
        memo["manifest_phase"] = "awaiting_manifest_update"
        record.parameters = parameters
        record.memo = memo
        return {
            "accepted": True,
            "applied": "next_safe_point",
            "message": (
                "Manifest update accepted and will be applied at the next safe point."
            ),
        }

    if update_name == "SetConcurrency":
        resolved = _execution_policy_from_parameters(parameters)
        if max_concurrency is None:
            raise ManifestIngestValidationError(
                "maxConcurrency is required when updateName is SetConcurrency"
            )
        if not 1 <= int(max_concurrency) <= 500:
            raise ManifestIngestValidationError(
                "maxConcurrency must be between 1 and 500 when provided"
            )
        resolved.max_concurrency = int(max_concurrency)
        resolved.concurrency_defaulted = False
        parameters["executionPolicy"] = resolved.model_dump(by_alias=True)
        record.parameters = parameters
        return {
            "accepted": True,
            "applied": "immediate",
            "message": f"Concurrency updated to {resolved.max_concurrency}.",
        }

    if update_name == "Pause":
        record.paused = True
        memo["manifest_phase"] = "paused"
        record.memo = memo
        return {
            "accepted": True,
            "applied": "immediate",
            "message": "Manifest ingest paused.",
        }

    if update_name == "Resume":
        record.paused = False
        memo["manifest_phase"] = _default_phase(record)
        record.memo = memo
        return {
            "accepted": True,
            "applied": "immediate",
            "message": "Manifest ingest resumed.",
        }

    mutation = ManifestNodeMutationRequestModel(nodeIds=list(node_ids or ()))
    accepted_node_ids: list[str] = []
    rejected_node_ids: list[str] = []
    by_id = {node.node_id: node for node in nodes}

    if update_name == "CancelNodes":
        for node_id in mutation.node_ids:
            node = by_id.get(node_id)
            if node is None or node.state not in _MUTABLE_NODE_STATES:
                rejected_node_ids.append(node_id)
                continue
            node.state = "canceled"
            node.completed_at = now
            accepted_node_ids.append(node_id)
        if not accepted_node_ids:
            raise ManifestIngestValidationError(
                "CancelNodes requires at least one pending, ready, or running node"
            )
        _persist_node_items(record, nodes)
        return {
            "accepted": True,
            "applied": "immediate",
            "message": f"Canceled {len(accepted_node_ids)} node(s).",
            "result": {
                "acceptedNodeIds": accepted_node_ids,
                "rejectedNodeIds": rejected_node_ids,
            },
        }

    if update_name == "RetryNodes":
        for node_id in mutation.node_ids:
            node = by_id.get(node_id)
            if node is None or node.state not in {"failed", "canceled"}:
                rejected_node_ids.append(node_id)
                continue
            node.state = "ready"
            node.completed_at = None
            node.result_artifact_ref = None
            node.child_run_id = None
            accepted_node_ids.append(node_id)
        if not accepted_node_ids:
            raise ManifestIngestValidationError(
                "RetryNodes requires at least one failed or canceled node"
            )
        _persist_node_items(record, nodes)
        return {
            "accepted": True,
            "applied": "immediate",
            "message": f"Queued {len(accepted_node_ids)} node(s) for retry.",
            "result": {
                "acceptedNodeIds": accepted_node_ids,
                "rejectedNodeIds": rejected_node_ids,
            },
        }

    raise ManifestIngestValidationError(
        f"Unsupported manifest update name: {update_name}"
    )


def compile_manifest_plan(
    *,
    manifest_ref: str,
    manifest_payload: bytes | str,
    action: str,
    options: Mapping[str, Any] | None,
    requested_by: RequestedByModel | Mapping[str, Any],
    execution_policy: ManifestExecutionPolicyModel | Mapping[str, Any],
) -> CompiledManifestPlanModel:
    """Validate manifest YAML and produce a stable compiled plan payload."""

    requested_by_model = RequestedByModel.model_validate(requested_by)
    execution_policy_model = ManifestExecutionPolicyModel.model_validate(
        execution_policy
    )
    manifest_text = (
        manifest_payload.decode("utf-8")
        if isinstance(manifest_payload, bytes)
        else str(manifest_payload)
    )
    normalized_text = manifest_text.strip()
    if not normalized_text:
        raise ManifestIngestValidationError("Manifest artifact payload is empty")
    try:
        manifest_obj = safe_load(normalized_text)
    except YAMLError as exc:
        raise ManifestIngestValidationError(
            "Manifest artifact is not valid YAML"
        ) from exc
    if not isinstance(manifest_obj, Mapping):
        raise ManifestIngestValidationError("Manifest YAML must decode to an object")

    metadata = manifest_obj.get("metadata")
    manifest_name = (
        str(metadata.get("name")).strip()
        if isinstance(metadata, Mapping) and metadata.get("name")
        else "manifest"
    )
    try:
        normalized = normalize_manifest_job_payload(
            {
                "manifest": {
                    "name": manifest_name,
                    "action": action,
                    "source": {
                        "kind": "inline",
                        "content": normalized_text,
                    },
                    "options": dict(options or {}),
                }
            }
        )
    except ManifestContractError as exc:
        raise ManifestIngestValidationError(str(exc)) from exc

    data_sources = manifest_obj.get("dataSources")
    if not isinstance(data_sources, list) or not data_sources:
        raise ManifestIngestValidationError(
            "Manifest YAML must declare at least one dataSources entry"
        )

    nodes: list[ManifestPlanNodeModel] = []
    for raw in data_sources:
        if not isinstance(raw, Mapping):
            raise ManifestIngestValidationError(
                "Each manifest dataSources entry must be an object"
            )
        nodes.append(
            ManifestPlanNodeModel(
                nodeId=_stable_node_id(raw),
                title=str(raw.get("id") or raw.get("type") or "manifest-node"),
                sourceType=str(raw.get("type") or "unknown"),
                sourceId=str(raw.get("id") or _stable_node_id(raw)),
                requiredCapabilities=list(normalized.get("requiredCapabilities", [])),
                runtimeHints={
                    "taskQueueClass": "activity_routed",
                    "requiredCapabilities": list(
                        normalized.get("requiredCapabilities", [])
                    ),
                },
                dependencies=[],
            )
        )

    return CompiledManifestPlanModel(
        manifestRef=manifest_ref,
        manifestDigest=str(normalized.get("manifestHash")),
        action=str(normalized["manifest"]["action"]),
        requestedBy=requested_by_model,
        executionPolicy=execution_policy_model,
        nodes=nodes,
        edges=[],
        requiredCapabilities=list(normalized.get("requiredCapabilities", [])),
        options=dict(normalized["manifest"].get("options") or {}),
    )


def plan_nodes_to_runtime_nodes(
    plan: (
        CompiledManifestPlanModel | Sequence[ManifestPlanNodeModel | Mapping[str, Any]]
    ),
    *,
    requested_by: RequestedByModel | Mapping[str, Any],
) -> list[ManifestNodeModel]:
    """Materialize compiled plan nodes into bounded runtime node records."""

    requested_by_model = RequestedByModel.model_validate(requested_by)
    raw_nodes = (
        plan.nodes if isinstance(plan, CompiledManifestPlanModel) else list(plan)
    )
    plan_nodes = [
        (
            node
            if isinstance(node, ManifestPlanNodeModel)
            else ManifestPlanNodeModel.model_validate(node)
        )
        for node in raw_nodes
    ]
    return [
        ManifestNodeModel(
            nodeId=node.node_id,
            state="ready",
            title=node.title,
            requestedBy=requested_by_model,
        )
        for node in plan_nodes
    ]


def build_manifest_run_index(
    *,
    workflow_id: str,
    manifest_ref: str,
    nodes: Sequence[ManifestNodeModel | Mapping[str, Any]],
) -> ManifestRunIndexModel:
    """Build the canonical run-index payload from runtime node state."""

    normalized_nodes = _coerce_runtime_nodes(nodes)
    items = [
        ManifestRunIndexEntryModel(
            nodeId=node.node_id,
            state=node.state,
            childWorkflowId=node.child_workflow_id,
            childRunId=node.child_run_id,
            workflowType=node.workflow_type,
            resultArtifactRef=node.result_artifact_ref,
        )
        for node in normalized_nodes
    ]
    return ManifestRunIndexModel(
        workflowId=workflow_id,
        manifestRef=manifest_ref,
        counts=manifest_node_counts_from_nodes(list(normalized_nodes)),
        items=items,
    )


def build_manifest_summary(
    *,
    workflow_id: str,
    state: str,
    phase: str,
    manifest_ref: str,
    plan_ref: str | None,
    nodes: Sequence[ManifestNodeModel | Mapping[str, Any]],
) -> ManifestIngestSummaryModel:
    """Build the bounded ingest summary payload."""

    normalized_nodes = _coerce_runtime_nodes(nodes)
    failed_node_ids = [
        node.node_id for node in normalized_nodes if node.state == "failed"
    ]
    return ManifestIngestSummaryModel(
        workflowId=workflow_id,
        state=state,
        phase=phase,
        manifestRef=manifest_ref,
        planRef=plan_ref,
        counts=manifest_node_counts_from_nodes(list(normalized_nodes)),
        failedNodeIds=failed_node_ids,
    )


def _persist_node_items(
    record: TemporalExecutionRecord,
    nodes: Sequence[ManifestNodeModel],
) -> None:
    parameters = dict(record.parameters or {})
    memo = dict(record.memo or {})
    parameters["manifestNodes"] = [
        item.model_dump(by_alias=True, mode="json") for item in nodes
    ]
    memo["manifest_counts"] = manifest_node_counts_from_nodes(list(nodes)).model_dump(
        by_alias=True
    )
    if not record.paused:
        memo["manifest_phase"] = _default_phase(record)
    record.parameters = parameters
    record.memo = memo


def _counts_from_record(
    parameters: Mapping[str, Any],
    memo: Mapping[str, Any],
    nodes: Sequence[ManifestNodeModel],
) -> ManifestNodeCountsModel:
    payload = memo.get("manifest_counts")
    if isinstance(payload, Mapping):
        try:
            return ManifestNodeCountsModel.model_validate(payload)
        except Exception:
            pass
    return manifest_node_counts_from_nodes(list(nodes))


def _node_items(
    parameters: Mapping[str, Any],
    *,
    requested_by: RequestedByModel | None = None,
) -> list[ManifestNodeModel]:
    raw_items = parameters.get("manifestNodes")
    if not isinstance(raw_items, list):
        return []
    default_requested_by = requested_by
    items: list[ManifestNodeModel] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            continue
        payload = dict(raw)
        if default_requested_by is not None and "requestedBy" not in payload:
            payload["requestedBy"] = default_requested_by.model_dump(by_alias=True)
        try:
            items.append(ManifestNodeModel.model_validate(payload))
        except Exception:
            continue
    return items


def _execution_policy_from_parameters(
    parameters: Mapping[str, Any],
) -> ManifestExecutionPolicyModel:
    payload = parameters.get("executionPolicy")
    if isinstance(payload, Mapping):
        data = dict(payload)
    else:
        data = {}
    if "failurePolicy" not in data:
        data["failurePolicy"] = (
            str(parameters.get("failurePolicy") or DEFAULT_MANIFEST_FAILURE_POLICY)
            .strip()
            .lower()
        )
    if "maxConcurrency" not in data:
        data["maxConcurrency"] = DEFAULT_MANIFEST_MAX_CONCURRENCY
        data["concurrencyDefaulted"] = True
    return ManifestExecutionPolicyModel.model_validate(data)


def _requested_by_from_record(
    record: TemporalExecutionRecord,
    parameters: Mapping[str, Any],
) -> RequestedByModel:
    payload = parameters.get("requestedBy")
    if isinstance(payload, Mapping):
        return RequestedByModel.model_validate(payload)
    return RequestedByModel(
        type="user" if record.owner_id else "system",
        id=record.owner_id or "system",
    )


def _manifest_lifecycle_state(record: TemporalExecutionRecord) -> str:
    state = record.state.value
    if state == MoonMindWorkflowState.AWAITING_EXTERNAL.value:
        return MoonMindWorkflowState.EXECUTING.value
    if state == MoonMindWorkflowState.PLANNING.value:
        return MoonMindWorkflowState.INITIALIZING.value
    return state


def _default_phase(record: TemporalExecutionRecord) -> str:
    state = _manifest_lifecycle_state(record)
    if record.paused:
        return "paused"
    if state == MoonMindWorkflowState.INITIALIZING.value:
        return "initializing"
    if state == MoonMindWorkflowState.FINALIZING.value:
        return "finalizing"
    if state in {
        MoonMindWorkflowState.COMPLETED.value,
        MoonMindWorkflowState.FAILED.value,
        MoonMindWorkflowState.CANCELED.value,
    }:
        return "terminal"
    return "executing"


def _artifact_ref(memo: Mapping[str, Any], key: str) -> str | None:
    snake = memo.get(key)
    if isinstance(snake, str) and snake.strip():
        return snake.strip()
    camel_key = "".join(
        [
            key.split("_")[0],
            *[part.capitalize() for part in key.split("_")[1:]],
        ]
    )
    camel = memo.get(camel_key)
    if isinstance(camel, str) and camel.strip():
        return camel.strip()
    return None


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        offset = int(payload.get("offset", 0))
        if offset < 0:
            raise ValueError("offset must be non-negative")
        return offset
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ManifestIngestValidationError("Invalid cursor") from exc


def _encode_cursor(offset: int) -> str:
    payload = {"offset": offset}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _stable_node_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"node-{digest[:12]}"


def _coerce_runtime_nodes(
    nodes: Sequence[ManifestNodeModel | Mapping[str, Any]],
) -> list[ManifestNodeModel]:
    return [
        (
            node
            if isinstance(node, ManifestNodeModel)
            else ManifestNodeModel.model_validate(node)
        )
        for node in nodes
    ]


def _workflow_owner_id(info: Any) -> str | None:
    search_attributes = getattr(info, "search_attributes", None)
    raw_value: Any = None
    if isinstance(search_attributes, Mapping):
        raw_value = search_attributes.get("mm_owner_id")
    elif hasattr(search_attributes, "get"):
        try:
            raw_value = search_attributes.get("mm_owner_id")
        except TypeError:
            raw_value = None

    if isinstance(raw_value, Sequence) and not isinstance(
        raw_value, (str, bytes, bytearray)
    ):
        raw_value = next((item for item in raw_value if str(item).strip()), None)

    normalized = str(raw_value or "").strip()
    return normalized or None


def _resolve_workflow_requested_by(
    parameters: Mapping[str, Any],
    *,
    owner_id: str | None,
) -> RequestedByModel:
    payload = parameters.get("requestedBy")
    provided = None
    if payload is not None:
        provided = RequestedByModel.model_validate(payload)

    normalized_owner = str(owner_id or "").strip()
    if not normalized_owner or normalized_owner.lower() == "system":
        expected = RequestedByModel(type="system", id="system")
    else:
        expected = RequestedByModel(type="user", id=normalized_owner)

    if provided is not None and provided != expected:
        raise ManifestIngestValidationError(
            "requestedBy must match the immutable workflow owner metadata"
        )

    if not normalized_owner and provided is not None and provided.type != "system":
        raise ManifestIngestValidationError(
            "User-scoped requestedBy requires workflow owner metadata"
        )

    return provided or expected


def _requested_by_principal(requested_by: RequestedByModel) -> str:
    if requested_by.type == "user":
        return requested_by.id
    return "system"


def _runtime_manifest_nodes(
    compiled_nodes: Sequence[Mapping[str, Any]],
    *,
    requested_by: RequestedByModel,
) -> list[dict[str, Any]]:
    runtime_nodes = plan_nodes_to_runtime_nodes(
        compiled_nodes,
        requested_by=requested_by,
    )
    runtime_by_id = {node.node_id: node for node in runtime_nodes}
    payloads: list[dict[str, Any]] = []
    for raw in compiled_nodes:
        node_id = str(raw.get("nodeId") or "").strip()
        runtime_node = runtime_by_id.get(node_id)
        if runtime_node is None:
            continue
        payload = runtime_node.model_dump(by_alias=True, mode="json")
        payload["dependencies"] = list(raw.get("dependencies") or [])
        payload["runtimeHints"] = dict(raw.get("runtimeHints") or {})
        payload["requiredCapabilities"] = list(raw.get("requiredCapabilities") or [])
        payload["sourceType"] = raw.get("sourceType")
        payload["sourceId"] = raw.get("sourceId")
        payloads.append(payload)
    return payloads


def _apply_manifest_node_update(
    existing_nodes: Mapping[str, dict[str, Any]],
    *,
    updated_nodes: Sequence[dict[str, Any]],
    mode: str,
) -> dict[str, dict[str, Any]]:
    next_nodes = {node_id: dict(node) for node_id, node in existing_nodes.items()}
    if mode == "APPEND":
        duplicate_ids = [
            str(node.get("nodeId") or "").strip()
            for node in updated_nodes
            if str(node.get("nodeId") or "").strip() in next_nodes
        ]
        if duplicate_ids:
            duplicate_list = ", ".join(sorted(dict.fromkeys(duplicate_ids)))
            raise ManifestIngestValidationError(
                f"APPEND updates cannot redefine existing node IDs: {duplicate_list}"
            )
        for node in updated_nodes:
            next_nodes[str(node["nodeId"])] = dict(node)
        return next_nodes

    if mode != "REPLACE_FUTURE":
        raise ManifestIngestValidationError(f"Unsupported manifest update mode: {mode}")

    replaced_nodes = {
        node_id: dict(node)
        for node_id, node in next_nodes.items()
        if str(node.get("state") or "").strip().lower() not in {"pending", "ready"}
    }
    for node in updated_nodes:
        node_id = str(node["nodeId"])
        if node_id in replaced_nodes:
            continue
        replaced_nodes[node_id] = dict(node)
    return replaced_nodes


import asyncio
from datetime import timedelta

from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    pass


@workflow.defn(name="MoonMind.ManifestIngest")
class ManifestIngestWorkflow:
    """Real Temporal workflow for manifest ingest."""

    def __init__(self) -> None:
        info = workflow.info()
        self._manifest_ref: str | None = None
        self._concurrency: int = 50
        self._paused: bool = False
        self._nodes: dict[str, dict[str, Any]] = {}
        self._status: str = "initializing"
        self._plan_ref: str | None = None
        self._summary_ref: str | None = None
        self._run_index_ref: str | None = None
        self._execution_policy: dict[str, Any] = {}
        self._requested_by: dict[str, Any] = {}
        self._owner_id: str | None = _workflow_owner_id(info)
        self._activity_principal: str = "system"
        self._action: str = "run"
        self._pending_manifest_update: dict[str, str] | None = None
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._workflow_id: str = info.workflow_id
        self._run_id: str = info.run_id

    async def _compile_manifest(
        self,
        *,
        manifest_ref: str,
    ) -> dict[str, Any]:
        manifest_payload = await workflow.execute_activity(
            "manifest_read",
            args=[
                {
                    "principal": self._activity_principal,
                    "manifest_ref": manifest_ref,
                }
            ],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return await workflow.execute_activity(
            "manifest_compile",
            args=[
                {
                    "principal": self._activity_principal,
                    "manifest_ref": manifest_ref,
                    "manifest_payload": manifest_payload,
                    "action": self._action,
                    "options": None,
                    "requested_by": self._requested_by,
                    "execution_policy": self._execution_policy,
                }
            ],
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _apply_pending_manifest_update(self) -> None:
        if self._pending_manifest_update is None:
            return

        update_request = ManifestUpdateManifestRequestModel.model_validate(
            self._pending_manifest_update
        )
        compile_result = await self._compile_manifest(
            manifest_ref=update_request.new_manifest_artifact_ref
        )
        compiled_nodes = _runtime_manifest_nodes(
            compile_result.get("nodes", []),
            requested_by=RequestedByModel.model_validate(self._requested_by),
        )
        self._nodes = _apply_manifest_node_update(
            self._nodes,
            updated_nodes=compiled_nodes,
            mode=update_request.mode,
        )
        self._manifest_ref = update_request.new_manifest_artifact_ref
        self._plan_ref = compile_result.get("plan_ref") or self._plan_ref
        self._pending_manifest_update = None

    @workflow.run
    async def run(self, parameters: dict[str, Any]) -> dict[str, Any]:
        self._manifest_ref = parameters.get("manifestArtifactRef")
        if not self._manifest_ref:
            raise ValueError("manifestArtifactRef is required")

        requested_by = _resolve_workflow_requested_by(
            parameters,
            owner_id=self._owner_id,
        )
        execution_policy = _execution_policy_from_parameters(parameters)
        self._requested_by = requested_by.model_dump(by_alias=True)
        self._execution_policy = execution_policy.model_dump(by_alias=True)
        self._activity_principal = _requested_by_principal(requested_by)
        self._action = str(parameters.get("action") or "run")
        self._concurrency = execution_policy.max_concurrency
        self._status = "executing"

        self._plan_ref = parameters.get("planArtifactRef")
        nodes_input = parameters.get("manifestNodes", [])

        if self._plan_ref and nodes_input:
            for n in nodes_input:
                node_id = n.get("nodeId") or n.get("node_id")
                if node_id:
                    self._nodes[node_id] = dict(n)
        else:
            # 1. Compile Plan
            compile_result = await self._compile_manifest(
                manifest_ref=self._manifest_ref
            )
            self._plan_ref = compile_result.get("plan_ref")
            for n in _runtime_manifest_nodes(
                compile_result.get("nodes", []),
                requested_by=requested_by,
            ):
                node_id = n.get("nodeId") or n.get("node_id")
                if node_id:
                    self._nodes[node_id] = dict(n)

        # 2. Execute nodes logic
        failure_policy = self._execution_policy.get("failurePolicy", "fail_fast")

        self._running_tasks = {}

        def can_start(node_id: str) -> bool:
            node = self._nodes[node_id]
            if node["state"] not in {"pending", "ready"}:
                return False
            for d in set(node.get("dependencies", [])):
                dep_node = self._nodes.get(d)
                if not dep_node or dep_node["state"] != "completed":
                    return False
            return True

        async def run_node(node_id: str):
            node = self._nodes[node_id]
            node["state"] = "running"
            try:
                # Execute MoonMind.Run as a child workflow
                run_params = {
                    "manifestIngestWorkflowId": self._workflow_id,
                    "manifestIngestRunId": self._run_id,
                    "manifestArtifactRef": self._manifest_ref,
                    "nodeId": node_id,
                    "requestedBy": self._requested_by,
                    "runtimeHints": {
                        "manifestNodeState": "running",
                        "workflowType": "MoonMind.Run",
                    },
                    "parentClosePolicy": "REQUEST_CANCEL",
                }

                child_id = f"{self._workflow_id}:{self._run_id}:{node_id}"
                node["child_workflow_id"] = child_id

                child_result = await workflow.execute_child_workflow(
                    "MoonMind.Run",
                    args=[
                        {
                            "workflow_type": "MoonMind.Run",
                            "owner_id": (
                                requested_by.id if requested_by.type == "user" else None
                            ),
                            "title": node.get("title", f"Manifest node {node_id}"),
                            "input_artifact_ref": self._manifest_ref,
                            "plan_artifact_ref": self._plan_ref,
                            "manifest_artifact_ref": None,
                            "initial_parameters": run_params,
                        }
                    ],
                    id=child_id,
                    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                )
                node["state"] = "completed"
                node["result_artifact_ref"] = child_result.get("output_artifact_ref")
            except asyncio.CancelledError as exc:
                node["state"] = "canceled"
                node["error"] = str(exc) or "cancelled"
                raise
            except Exception as e:
                node["state"] = "failed"
                node["error"] = str(e)

        while True:
            await workflow.wait_condition(lambda: not self._paused)

            if self._pending_manifest_update is not None and not self._running_tasks:
                await self._apply_pending_manifest_update()

            # Check for terminal failure in fail_fast mode
            any_failed = any(n["state"] == "failed" for n in self._nodes.values())
            if any_failed and failure_policy == "fail_fast":
                for n in self._nodes.values():
                    if n["state"] in {"pending", "ready"}:
                        n["state"] = "canceled"
                for task in self._running_tasks.values():
                    task.cancel()

            # Find ready nodes
            ready_nodes = [n_id for n_id in self._nodes if can_start(n_id)]

            # Start up to concurrency limit
            available_slots = self._concurrency - len(self._running_tasks)
            for n_id in ready_nodes[:available_slots]:
                task = asyncio.create_task(run_node(n_id))
                self._running_tasks[n_id] = task

            if not self._running_tasks:
                # No running tasks and no ready nodes means we're done
                break

            # Wait for at least one task to complete
            done, _ = await asyncio.wait(
                list(self._running_tasks.values()), return_when=asyncio.FIRST_COMPLETED
            )

            for d in done:
                # Remove completed tasks from tracking
                for n_id, t in list(self._running_tasks.items()):
                    if t == d:
                        del self._running_tasks[n_id]
                        break
                if d.cancelled():
                    continue
                exc = d.exception()
                if exc is not None:
                    if failure_policy == "fail_fast":
                        raise exc
                    # best_effort / continue_and_report: absorb failure, continue

        self._status = "finalizing"

        # 3. Create summary and index artifacts
        nodes_list = list(self._nodes.values())
        summary_result = await workflow.execute_activity(
            "manifest_write_summary",
            args=[
                {
                    "principal": self._activity_principal,
                    "workflow_id": self._workflow_id,
                    "state": (
                        "completed"
                        if all(n["state"] == "completed" for n in nodes_list)
                        else "failed"
                    ),
                    "phase": "completed",
                    "manifest_ref": self._manifest_ref,
                    "plan_ref": self._plan_ref,
                    "nodes": nodes_list,
                }
            ],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        if summary_result and len(summary_result) == 2:
            self._summary_ref = summary_result[0]
            self._run_index_ref = summary_result[1]

        final_status = "completed"
        if any(n["state"] == "failed" for n in nodes_list):
            final_status = "failed"

        return {
            "status": final_status,
            "summaryRef": self._summary_ref,
            "runIndexRef": self._run_index_ref,
        }

    @workflow.update(name="UpdateManifest")
    async def update_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = ManifestUpdateManifestRequestModel.model_validate(payload or {})
        self._pending_manifest_update = request.model_dump(by_alias=True)
        return {
            "accepted": True,
            "applied": "next_safe_point",
            "message": (
                "Manifest update accepted and will be applied at the next safe point."
            ),
        }

    @workflow.update(name="SetConcurrency")
    async def set_concurrency(self, payload: dict[str, Any]) -> dict[str, Any]:
        max_concurrency = payload.get("maxConcurrency")
        if max_concurrency is None:
            return {
                "accepted": False,
                "message": "maxConcurrency is required",
            }
        try:
            value = int(max_concurrency)
        except (TypeError, ValueError):
            return {
                "accepted": False,
                "message": "maxConcurrency must be an integer",
            }
        if not 1 <= value <= 500:
            return {
                "accepted": False,
                "message": "maxConcurrency must be between 1 and 500",
            }
        self._concurrency = value
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="Pause")
    async def pause(self, payload: dict[str, Any] = None) -> dict[str, Any]:
        self._paused = True
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="Resume")
    async def resume(self, payload: dict[str, Any] = None) -> dict[str, Any]:
        self._paused = False
        return {"accepted": True, "applied": "immediate"}

    @workflow.update(name="CancelNodes")
    async def cancel_nodes(self, payload: dict[str, Any]) -> dict[str, Any]:
        mutation = ManifestNodeMutationRequestModel.model_validate(payload or {})
        accepted_node_ids: list[str] = []
        rejected_node_ids: list[str] = []
        node_ids = mutation.node_ids
        for nid in node_ids:
            node = self._nodes.get(nid)
            if node is None:
                rejected_node_ids.append(nid)
                continue
            if node["state"] in {"pending", "ready"}:
                node["state"] = "canceled"
                accepted_node_ids.append(nid)
                continue
            if node["state"] == "running":
                task = self._running_tasks.get(nid)
                if task is None:
                    rejected_node_ids.append(nid)
                    continue
                task.cancel()
                accepted_node_ids.append(nid)
                continue
            rejected_node_ids.append(nid)
        return {
            "accepted": True,
            "applied": "immediate",
            "result": {
                "acceptedNodeIds": accepted_node_ids,
                "rejectedNodeIds": rejected_node_ids,
            },
        }

    @workflow.update(name="RetryNodes")
    async def retry_nodes(self, payload: dict[str, Any]) -> dict[str, Any]:
        node_ids = payload.get("nodeIds", [])
        for nid in node_ids:
            if nid in self._nodes and self._nodes[nid]["state"] in [
                "failed",
                "canceled",
            ]:
                self._nodes[nid]["state"] = "pending"
        return {"accepted": True, "applied": "immediate"}
