"""Remediation context artifact builder.

This module keeps remediation evidence packaging at the service/activity boundary so
workflow history carries refs and compact metadata instead of raw logs or artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactService,
)

REMEDIATION_CONTEXT_LINK_TYPE = "remediation.context"
REMEDIATION_CONTEXT_ARTIFACT_NAME = "reports/remediation_context.json"
REMEDIATION_CONTEXT_SCHEMA_VERSION = "v1"
REMEDIATION_ARTIFACT_TYPES = frozenset(
    {
        "remediation.context",
        "remediation.plan",
        "remediation.decision_log",
        "remediation.action_request",
        "remediation.action_result",
        "remediation.verification",
        "remediation.summary",
    }
)
REMEDIATION_PHASES = frozenset(
    {
        "collecting_evidence",
        "diagnosing",
        "awaiting_approval",
        "acting",
        "verifying",
        "resolved",
        "escalated",
        "failed",
    }
)
REMEDIATION_RESOLUTIONS = frozenset(
    {
        "not_applicable",
        "diagnosis_only",
        "no_action_needed",
        "resolved_after_action",
        "escalated",
        "unsafe_to_act",
        "lock_conflict",
        "evidence_unavailable",
        "failed",
    }
)
MAX_REMEDIATION_CONTEXT_TAIL_LINES = 2000
MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS = 20
SECRET_LIKE_POLICY_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
SAFE_POLICY_KEYS = frozenset({"authorityMode"})


class RemediationContextError(RuntimeError):
    """Raised when a remediation context artifact cannot be generated."""


@dataclass(slots=True)
class RemediationContextBuildResult:
    """Result of one remediation context build."""

    artifact: db_models.TemporalArtifact
    link: db_models.TemporalExecutionRemediationLink
    payload: dict[str, Any]


class RemediationContextBuilder:
    """Build bounded remediation context artifacts from persisted remediation links."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        artifact_service: TemporalArtifactService,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service

    async def build_context(
        self,
        *,
        remediation_workflow_id: str,
        principal: str = "service:remediation-context",
    ) -> RemediationContextBuildResult:
        workflow_id = str(remediation_workflow_id or "").strip()
        if not workflow_id:
            raise RemediationContextError("remediation_workflow_id is required")

        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink, workflow_id
        )
        if link is None:
            raise RemediationContextError(f"No remediation link found for {workflow_id}")

        remediation_record = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            link.remediation_workflow_id,
        )
        if remediation_record is None:
            raise RemediationContextError(
                f"Remediation execution not found: {link.remediation_workflow_id}"
            )

        target_record = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            link.target_workflow_id,
        )
        if target_record is None:
            raise RemediationContextError(
                f"Target execution not found: {link.target_workflow_id}"
            )

        payload = self._build_payload(
            link=link,
            remediation_record=remediation_record,
            target_record=target_record,
        )
        payload_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        artifact, _upload = await self._artifact_service.create(
            principal=principal,
            content_type="application/json",
            size_bytes=len(payload_bytes),
            link=ExecutionRef(
                namespace=remediation_record.namespace,
                workflow_id=remediation_record.workflow_id,
                run_id=remediation_record.run_id,
                link_type=REMEDIATION_CONTEXT_LINK_TYPE,
                label=REMEDIATION_CONTEXT_ARTIFACT_NAME,
                created_by_activity_type="remediation.context.build",
            ),
            metadata_json={
                "artifact_type": REMEDIATION_CONTEXT_LINK_TYPE,
                "name": REMEDIATION_CONTEXT_ARTIFACT_NAME,
                "schemaVersion": REMEDIATION_CONTEXT_SCHEMA_VERSION,
                "targetWorkflowId": target_record.workflow_id,
                "targetRunId": link.target_run_id,
            },
            redaction_level=db_models.TemporalArtifactRedactionLevel.RESTRICTED,
        )
        artifact = await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload_bytes,
            content_type="application/json",
        )

        link.context_artifact_ref = artifact.artifact_id
        refs = list(remediation_record.artifact_refs or [])
        if artifact.artifact_id not in refs:
            refs.append(artifact.artifact_id)
            remediation_record.artifact_refs = refs
        await self._session.commit()
        await self._session.refresh(link)
        await self._session.refresh(artifact)

        return RemediationContextBuildResult(
            artifact=artifact,
            link=link,
            payload=payload,
        )

    def _build_payload(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        remediation_record: db_models.TemporalExecutionCanonicalRecord,
        target_record: db_models.TemporalExecutionCanonicalRecord,
    ) -> dict[str, Any]:
        remediation = self._remediation_payload(remediation_record)
        target = remediation.get("target") if isinstance(remediation, Mapping) else {}
        target_mapping = target if isinstance(target, Mapping) else {}
        task_run_ids = self._normalize_task_run_ids(target_mapping.get("taskRunIds"))
        evidence_policy = self._normalize_evidence_policy(
            remediation.get("evidencePolicy")
            if isinstance(remediation, Mapping)
            else None
        )

        return {
            "schemaVersion": REMEDIATION_CONTEXT_SCHEMA_VERSION,
            "remediationWorkflowId": remediation_record.workflow_id,
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "target": self._target_payload(target_record, link=link),
            "selectedSteps": self._normalize_step_selectors(
                target_mapping.get("stepSelectors")
            ),
            "evidence": {
                "targetArtifactRefs": self._target_artifact_refs(target_record),
                "taskRuns": [{"taskRunId": item} for item in task_run_ids],
            },
            "liveFollow": {
                "mode": link.mode,
                "supported": False,
                "taskRunId": task_run_ids[0] if task_run_ids else None,
                "resumeCursor": None,
            },
            "policies": {
                "authorityMode": link.authority_mode,
                "actionPolicyRef": self._string_or_none(
                    remediation.get("actionPolicyRef")
                    if isinstance(remediation, Mapping)
                    else None
                ),
                "evidencePolicy": evidence_policy,
                "approvalPolicy": self._mapping_or_none(
                    remediation.get("approvalPolicy")
                    if isinstance(remediation, Mapping)
                    else None
                ),
                "lockPolicy": self._mapping_or_none(
                    remediation.get("lockPolicy")
                    if isinstance(remediation, Mapping)
                    else None
                ),
            },
            "boundedness": {
                "maxTailLines": MAX_REMEDIATION_CONTEXT_TAIL_LINES,
                "maxTaskRunIds": MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS,
                "rawLogBodiesIncluded": False,
                "artifactContentsIncluded": False,
            },
        }

    @staticmethod
    def _remediation_payload(
        record: db_models.TemporalExecutionCanonicalRecord,
    ) -> Mapping[str, Any]:
        parameters = record.parameters if isinstance(record.parameters, Mapping) else {}
        task = parameters.get("task") if isinstance(parameters, Mapping) else {}
        task_mapping = task if isinstance(task, Mapping) else {}
        remediation = task_mapping.get("remediation")
        return remediation if isinstance(remediation, Mapping) else {}

    @staticmethod
    def _target_payload(
        record: db_models.TemporalExecutionCanonicalRecord,
        *,
        link: db_models.TemporalExecutionRemediationLink,
    ) -> dict[str, Any]:
        memo = record.memo if isinstance(record.memo, Mapping) else {}
        return {
            "workflowId": record.workflow_id,
            "runId": link.target_run_id,
            "title": _string_or_none(memo.get("title")),
            "summary": _string_or_none(memo.get("summary")),
            "state": _enum_value(record.state),
            "closeStatus": _enum_value(record.close_status),
        }

    @staticmethod
    def _normalize_step_selectors(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        selectors: list[dict[str, Any]] = []
        for item in value[:MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS]:
            if not isinstance(item, Mapping):
                continue
            selector: dict[str, Any] = {}
            logical_step_id = _string_or_none(
                item.get("logicalStepId") or item.get("logical_step_id")
            )
            if logical_step_id:
                selector["logicalStepId"] = logical_step_id
            attempt = _positive_int_or_none(item.get("attempt"))
            if attempt is not None:
                selector["attempt"] = attempt
            task_run_id = _string_or_none(item.get("taskRunId") or item.get("task_run_id"))
            if task_run_id:
                selector["taskRunId"] = task_run_id
            if selector:
                selectors.append(selector)
        return selectors

    @staticmethod
    def _normalize_task_run_ids(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        task_run_ids: list[str] = []
        seen: set[str] = set()
        for item in value:
            task_run_id = _string_or_none(item)
            if not task_run_id or task_run_id in seen:
                continue
            seen.add(task_run_id)
            task_run_ids.append(task_run_id)
            if len(task_run_ids) >= MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS:
                break
        return task_run_ids

    @staticmethod
    def _normalize_evidence_policy(value: Any) -> dict[str, Any]:
        policy = _safe_policy_mapping(value) or {}
        tail_lines = _positive_int_or_none(policy.get("tailLines"))
        if tail_lines is None:
            tail_lines = MAX_REMEDIATION_CONTEXT_TAIL_LINES
        policy["tailLines"] = min(tail_lines, MAX_REMEDIATION_CONTEXT_TAIL_LINES)
        return policy

    @staticmethod
    def _target_artifact_refs(
        record: db_models.TemporalExecutionCanonicalRecord,
    ) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        seen: set[tuple[str, str | None]] = set()

        for raw_ref in record.artifact_refs or []:
            ref = _artifact_ref_payload(raw_ref, kind=None)
            if ref is not None:
                key = (ref.get("artifact_id") or ref.get("ref") or "", ref.get("kind"))
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)

        for kind, raw_ref in (
            ("input", record.input_ref),
            ("plan", record.plan_ref),
            ("manifest", record.manifest_ref),
        ):
            ref = _artifact_ref_payload(raw_ref, kind=kind)
            if ref is not None:
                key = (ref.get("artifact_id") or ref.get("ref") or "", ref.get("kind"))
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)

        return refs

    @staticmethod
    def _mapping_or_none(value: Any) -> dict[str, Any] | None:
        return _safe_policy_mapping(value)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return _string_or_none(value)


class RemediationLifecyclePublisher:
    """Publish bounded remediation lifecycle artifacts for one remediation run."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        artifact_service: TemporalArtifactService,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service

    async def publish_json_artifact(
        self,
        *,
        remediation_workflow_id: str,
        artifact_type: str,
        name: str,
        payload: Mapping[str, Any],
        target_workflow_id: str | None = None,
        target_run_id: str | None = None,
        principal: str = "service:remediation-lifecycle",
    ) -> db_models.TemporalArtifact:
        workflow_id = _required_string(
            remediation_workflow_id, "remediation_workflow_id"
        )
        artifact_type = _required_string(artifact_type, "artifact_type")
        if artifact_type not in REMEDIATION_ARTIFACT_TYPES:
            raise RemediationContextError(
                f"Unsupported remediation artifact type: {artifact_type}"
            )
        name = _required_string(name, "name")
        if not isinstance(payload, Mapping):
            raise RemediationContextError("payload must be a mapping")

        remediation_record = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            workflow_id,
        )
        if remediation_record is None:
            raise RemediationContextError(
                f"Remediation execution not found: {workflow_id}"
            )

        safe_payload = _safe_lifecycle_payload(payload)
        payload_bytes = json.dumps(
            safe_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        metadata_json = {
            "artifact_type": artifact_type,
            "name": name,
            "schemaVersion": REMEDIATION_CONTEXT_SCHEMA_VERSION,
        }
        if target_workflow_id := _string_or_none(target_workflow_id):
            metadata_json["targetWorkflowId"] = target_workflow_id
        if target_run_id := _string_or_none(target_run_id):
            metadata_json["targetRunId"] = target_run_id

        artifact, _upload = await self._artifact_service.create(
            principal=principal,
            content_type="application/json",
            size_bytes=len(payload_bytes),
            link=ExecutionRef(
                namespace=remediation_record.namespace,
                workflow_id=remediation_record.workflow_id,
                run_id=remediation_record.run_id,
                link_type=artifact_type,
                label=name,
                created_by_activity_type="remediation.lifecycle.publish",
            ),
            metadata_json=metadata_json,
            redaction_level=db_models.TemporalArtifactRedactionLevel.RESTRICTED,
        )
        artifact = await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload_bytes,
            content_type="application/json",
        )

        refs = list(remediation_record.artifact_refs or [])
        if artifact.artifact_id not in refs:
            refs.append(artifact.artifact_id)
            remediation_record.artifact_refs = refs
        await self._session.commit()
        await self._session.refresh(artifact)
        return artifact


def normalize_remediation_phase(value: Any) -> str:
    """Return a bounded remediation phase value."""

    normalized = str(value or "").strip()
    return normalized if normalized in REMEDIATION_PHASES else "failed"


def normalize_remediation_resolution(value: Any) -> str:
    """Return a bounded remediation resolution value."""

    normalized = str(value or "").strip()
    return normalized if normalized in REMEDIATION_RESOLUTIONS else "failed"


def build_remediation_summary_block(
    *,
    target_workflow_id: str,
    target_run_id: str,
    phase: str,
    mode: str,
    authority_mode: str,
    actions_attempted: Sequence[Mapping[str, Any]] = (),
    resolution: str = "not_applicable",
    lock_conflicts: int = 0,
    approval_count: int = 0,
    evidence_degraded: bool = False,
    escalated: bool = False,
    unavailable_evidence_classes: Sequence[Any] = (),
    fallbacks_used: Sequence[Any] = (),
    resulting_target_run_id: str | None = None,
) -> dict[str, Any]:
    """Build the compact remediation block for run summaries."""

    summary = {
        "targetWorkflowId": _required_string(target_workflow_id, "target_workflow_id"),
        "targetRunId": _required_string(target_run_id, "target_run_id"),
        "phase": normalize_remediation_phase(phase),
        "mode": _required_string(mode, "mode"),
        "authorityMode": _required_string(authority_mode, "authority_mode"),
        "actionsAttempted": _bounded_action_summaries(actions_attempted),
        "resolution": normalize_remediation_resolution(resolution),
        "lockConflicts": max(_positive_int_or_none(lock_conflicts) or 0, 0),
        "approvalCount": max(_positive_int_or_none(approval_count) or 0, 0),
        "evidenceDegraded": bool(evidence_degraded),
        "escalated": bool(escalated),
        "unavailableEvidenceClasses": _safe_string_list(
            unavailable_evidence_classes
        ),
        "fallbacksUsed": _safe_string_list(fallbacks_used),
    }
    if resulting_target_run_id := _string_or_none(resulting_target_run_id):
        summary["resultingTargetRunId"] = resulting_target_run_id
    return summary


def build_remediation_audit_event(
    *,
    event_id: str,
    event_type: str,
    actor_user: str | None,
    execution_principal: str | None,
    remediation_workflow_id: str,
    remediation_run_id: str,
    target_workflow_id: str,
    target_run_id: str,
    action_kind: str | None,
    risk_tier: str | None,
    approval_decision: str | None,
    timestamp: datetime | str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one compact queryable remediation audit event."""

    return {
        "eventId": _required_string(event_id, "event_id"),
        "eventType": _required_string(event_type, "event_type"),
        "actorUser": _string_or_none(actor_user),
        "executionPrincipal": _string_or_none(execution_principal),
        "remediationWorkflowId": _required_string(
            remediation_workflow_id, "remediation_workflow_id"
        ),
        "remediationRunId": _required_string(
            remediation_run_id, "remediation_run_id"
        ),
        "targetWorkflowId": _required_string(target_workflow_id, "target_workflow_id"),
        "targetRunId": _required_string(target_run_id, "target_run_id"),
        "actionKind": _string_or_none(action_kind),
        "riskTier": _string_or_none(risk_tier),
        "approvalDecision": _string_or_none(approval_decision),
        "timestamp": _timestamp_string(timestamp),
        "metadata": _safe_policy_mapping(metadata) or {},
    }


def build_remediation_continue_as_new_state(
    *,
    target_workflow_id: str,
    target_run_id: str,
    context_artifact_ref: str | None,
    lock_identity: str | None,
    action_ledger_ref: str | None,
    approval_state: str | None,
    retry_budget_state: Mapping[str, Any] | None = None,
    live_follow_cursor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact state that is safe to carry across Continue-As-New."""

    return {
        "targetWorkflowId": _required_string(target_workflow_id, "target_workflow_id"),
        "targetRunId": _required_string(target_run_id, "target_run_id"),
        "contextArtifactRef": _string_or_none(context_artifact_ref),
        "lockIdentity": _string_or_none(lock_identity),
        "actionLedgerRef": _string_or_none(action_ledger_ref),
        "approvalState": _string_or_none(approval_state),
        "retryBudgetState": _safe_policy_mapping(retry_budget_state) or {},
        "liveFollowCursor": _safe_policy_mapping(live_follow_cursor) or {},
    }


def build_target_remediation_linkage_summary(
    *,
    target_workflow_id: str,
    active_remediation_count: int = 0,
    latest_remediation_title: str | None = None,
    latest_remediation_status: str | None = None,
    latest_action_kind: str | None = None,
    latest_outcome: str | None = None,
    active_lock_scope: str | None = None,
    active_lock_holder: str | None = None,
    last_updated_at: datetime | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact inbound remediation metadata for target read models."""

    summary = {
        "targetWorkflowId": _required_string(target_workflow_id, "target_workflow_id"),
        "activeRemediationCount": max(
            _positive_int_or_none(active_remediation_count) or 0,
            0,
        ),
        "latestRemediationTitle": _safe_optional_string(latest_remediation_title),
        "latestRemediationStatus": _safe_optional_string(latest_remediation_status),
        "latestActionKind": _safe_optional_string(latest_action_kind),
        "latestOutcome": _safe_optional_string(latest_outcome),
        "activeLockScope": _safe_optional_string(active_lock_scope),
        "activeLockHolder": _safe_optional_string(active_lock_holder),
        "metadata": _safe_policy_mapping(metadata) or {},
    }
    if last_updated_at is not None:
        summary["lastUpdatedAt"] = _timestamp_string(last_updated_at)
    return summary


def _artifact_ref_payload(raw_ref: Any, *, kind: str | None) -> dict[str, str] | None:
    ref = _string_or_none(raw_ref)
    if not ref or not ref.startswith("art_"):
        return None
    payload: dict[str, str] = {"artifact_id": ref}
    if kind:
        payload["kind"] = kind
    return payload


def _safe_policy_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    sanitized: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        key = _string_or_none(raw_key)
        if not key or _is_secret_like_key(key):
            continue
        safe_item = _safe_policy_value(raw_item)
        if safe_item is not None:
            sanitized[key] = safe_item
    return sanitized


def _safe_policy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        mapping = _safe_policy_mapping(value)
        return mapping if mapping else None
    if isinstance(value, list):
        return [
            safe_item
            for item in value
            if (safe_item := _safe_policy_value(item)) is not None
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and _is_unsafe_context_string(value):
            return None
        return value
    return None


def _safe_lifecycle_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_policy_mapping(value) or {}


def _bounded_action_summaries(
    actions_attempted: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for item in actions_attempted[:50]:
        if not isinstance(item, Mapping):
            continue
        summary: dict[str, str] = {}
        for key in ("kind", "status"):
            if value := _string_or_none(item.get(key)):
                if not _is_unsafe_context_string(value):
                    summary[key] = value
        if summary:
            summaries.append(summary)
    return summaries


def _safe_string_list(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values[:50]:
        item = _string_or_none(value)
        if not item or item in seen or _is_unsafe_context_string(item):
            continue
        seen.add(item)
        result.append(item)
    return result


def _required_string(value: Any, field_name: str) -> str:
    normalized = _string_or_none(value)
    if not normalized:
        raise RemediationContextError(f"{field_name} is required")
    if not _is_identifier_field(field_name) and _is_unsafe_context_string(normalized):
        raise RemediationContextError(f"{field_name} is unsafe")
    return normalized


def _safe_optional_string(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if not normalized or _is_unsafe_context_string(normalized):
        return None
    return normalized


def _timestamp_string(value: datetime | str) -> str:
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    normalized = _required_string(value, "timestamp")
    try:
        timestamp = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemediationContextError("timestamp must be ISO8601") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_secret_like_key(key: str) -> bool:
    if key in SAFE_POLICY_KEYS:
        return False
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in SECRET_LIKE_POLICY_KEY_PARTS)


def _is_identifier_field(field_name: str) -> bool:
    normalized = field_name.strip()
    return normalized.endswith("_id") or normalized.endswith("Id")


def _is_unsafe_context_string(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("/")
        or normalized.startswith("file:")
        or normalized.startswith("http://")
        or normalized.startswith("https://")
        or "presigned" in normalized
        or "storage_key" in normalized
        or "token=" in normalized
        or "password=" in normalized
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
