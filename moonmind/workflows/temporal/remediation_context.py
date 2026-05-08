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
from moonmind.utils.logging import redact_sensitive_text

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
        "remediation.audit_event",
        "remediation.target_annotation",
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
REMEDIATION_REPAIR_DECISIONS = frozenset(
    {
        "attempted",
        "skipped",
        "denied",
        "unsafe",
        "approval_required",
        "escalated",
    }
)
REMEDIATION_REPAIR_OUTCOMES = frozenset(
    {
        "repaired",
        "still_failed",
        "not_attempted",
        "unsafe",
        "approval_required",
        "escalated",
    }
)
REMEDIATION_PREVENTION_STATUSES = frozenset(
    {
        "reviewable_change_created",
        "findings_reported",
        "no_reviewable_fix",
        "policy_blocked",
    }
)
REMEDIATION_LOCK_RELEASE_STATUSES = frozenset(
    {"attempted", "released", "not_held", "failed"}
)
MAX_REMEDIATION_CONTEXT_TAIL_LINES = 2000
MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS = 20
TARGET_EVIDENCE_CLASSES = (
    ("stdout", "stdoutRef"),
    ("stderr", "stderrRef"),
    ("merged_logs", "mergedLogsRef"),
    ("diagnostics", "diagnosticsRef"),
    ("provider_snapshot", "providerSnapshotRef"),
    ("continuity", "continuityRefs"),
)
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
        target_evidence = self._target_evidence_payload(target_record)
        if not task_run_ids:
            task_run_ids = self._task_run_ids_from_evidence(target_evidence)
        evidence_policy = self._normalize_evidence_policy(
            remediation.get("evidencePolicy")
            if isinstance(remediation, Mapping)
            else None
        )
        task_runs = self._normalize_task_run_evidence(
            task_run_ids=task_run_ids,
            target_evidence=target_evidence,
        )
        live_follow = self._live_follow_payload(
            link=link,
            target_record=target_record,
            task_run_ids=task_run_ids,
            task_runs=task_runs,
            target_evidence=target_evidence,
            evidence_policy=evidence_policy,
        )
        availability = self._evidence_availability(
            task_runs=task_runs,
            live_follow=live_follow,
        )
        unavailable_classes = [
            item["class"]
            for item in availability
            if item.get("status")
            in {"missing", "partial", "denied", "unavailable", "unsupported"}
        ]

        return {
            "schemaVersion": REMEDIATION_CONTEXT_SCHEMA_VERSION,
            "remediationWorkflowId": remediation_record.workflow_id,
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "target": self._target_payload(target_record, link=link),
            "selectedSteps": self._normalize_step_selectors(
                target_mapping.get("stepSelectors"),
                target_evidence=target_evidence,
            ),
            "evidence": {
                "targetArtifactRefs": self._target_artifact_refs(target_record),
                "taskRuns": task_runs,
                "availability": availability,
                "evidenceDegraded": bool(unavailable_classes),
                "unavailableEvidenceClasses": unavailable_classes,
                **self._diagnosis_hints_payload(target_evidence),
            },
            "liveFollow": live_follow,
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
    def _target_evidence_payload(
        record: db_models.TemporalExecutionCanonicalRecord,
    ) -> Mapping[str, Any]:
        for source in (record.memo, record.parameters, record.integration_state):
            if not isinstance(source, Mapping):
                continue
            evidence = source.get("remediationEvidence") or source.get(
                "remediation_evidence"
            )
            if isinstance(evidence, Mapping):
                return evidence
        return {}

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
    def _normalize_step_selectors(
        value: Any,
        *,
        target_evidence: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        evidence_steps = _mapping_list(target_evidence.get("selectedSteps"))
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
            evidence_step = _match_step_evidence(selector, evidence_steps)
            if evidence_step is not None:
                if status := _safe_optional_string(evidence_step.get("status")):
                    selector["status"] = status
                if summary := _safe_optional_string(evidence_step.get("summary")):
                    selector["summary"] = summary
                artifact_refs = _artifact_ref_list(evidence_step.get("artifactRefs"))
                if artifact_refs:
                    selector["artifactRefs"] = artifact_refs
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
    def _task_run_ids_from_evidence(target_evidence: Mapping[str, Any]) -> list[str]:
        task_run_ids: list[str] = []
        seen: set[str] = set()
        for item in _mapping_list(target_evidence.get("taskRuns")):
            task_run_id = _string_or_none(item.get("taskRunId"))
            if not task_run_id or task_run_id in seen:
                continue
            seen.add(task_run_id)
            task_run_ids.append(task_run_id)
            if len(task_run_ids) >= MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS:
                break
        return task_run_ids

    @staticmethod
    def _normalize_task_run_evidence(
        *,
        task_run_ids: Sequence[str],
        target_evidence: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        source_by_id = {
            task_run_id: item
            for item in _mapping_list(target_evidence.get("taskRuns"))
            if (task_run_id := _string_or_none(item.get("taskRunId")))
        }
        task_runs: list[dict[str, Any]] = []
        for task_run_id in task_run_ids[:MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS]:
            entry: dict[str, Any] = {"taskRunId": task_run_id}
            source = source_by_id.get(task_run_id, {})
            for field in (
                "observabilitySummaryRef",
                "stdoutRef",
                "stderrRef",
                "mergedLogsRef",
                "diagnosticsRef",
                "providerSnapshotRef",
            ):
                if ref := _artifact_ref_payload(source.get(field), kind=None):
                    entry[field] = ref
            continuity_refs = _artifact_ref_list(source.get("continuityRefs"))
            if continuity_refs:
                entry["continuityRefs"] = continuity_refs
            task_runs.append(entry)
        return task_runs

    @staticmethod
    def _evidence_availability(
        *,
        task_runs: Sequence[Mapping[str, Any]],
        live_follow: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        has_merged = any(isinstance(item.get("mergedLogsRef"), Mapping) for item in task_runs)
        availability: list[dict[str, str]] = []
        for class_name, field_name in TARGET_EVIDENCE_CLASSES:
            available = any(_has_task_run_evidence(item, field_name) for item in task_runs)
            if available:
                availability.append({"class": class_name, "status": "available"})
            else:
                record = {"class": class_name, "status": "missing"}
                if has_merged:
                    record["fallback"] = "merged_logs"
                availability.append(record)

        live_status = _string_or_none(live_follow.get("status")) or "unsupported"
        if live_status == "active":
            availability.append({"class": "live_follow", "status": "available"})
        else:
            status = "denied" if live_status == "policy_denied" else live_status
            record = {"class": "live_follow", "status": status}
            if reason := _string_or_none(live_follow.get("reason")):
                record["reason"] = reason
            fallbacks = live_follow.get("fallbacks")
            if isinstance(fallbacks, Sequence) and not isinstance(
                fallbacks, (str, bytes, bytearray)
            ):
                if fallback := _string_or_none(next(iter(fallbacks), None)):
                    record["fallback"] = fallback
            availability.append(record)
        return availability

    @staticmethod
    def _live_follow_payload(
        *,
        link: db_models.TemporalExecutionRemediationLink,
        target_record: db_models.TemporalExecutionCanonicalRecord,
        task_run_ids: Sequence[str],
        task_runs: Sequence[Mapping[str, Any]],
        target_evidence: Mapping[str, Any],
        evidence_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        mode = link.mode
        task_run_id = task_run_ids[0] if task_run_ids else None
        live_evidence = target_evidence.get("liveFollow")
        live_mapping = live_evidence if isinstance(live_evidence, Mapping) else {}
        resume_cursor = _safe_policy_mapping(live_mapping.get("resumeCursor"))
        fallbacks = _durable_fallback_classes(task_runs)

        if _live_follow_denied_by_policy(evidence_policy):
            return {
                "status": "policy_denied",
                "mode": mode,
                "supported": False,
                "taskRunId": task_run_id,
                "resumeCursor": resume_cursor,
                "reason": "policy denies live observation",
                "fallbacks": fallbacks,
            }
        if mode not in {"follow", "snapshot_then_follow"}:
            return {
                "status": "unsupported",
                "mode": mode,
                "supported": False,
                "taskRunId": task_run_id,
                "resumeCursor": resume_cursor,
                "reason": "remediation mode does not request live observation",
                "fallbacks": fallbacks,
            }
        if _enum_value(target_record.state) not in {
            "executing",
            "awaiting_external",
            "awaiting_slot",
            "finalizing",
        }:
            return {
                "status": "unavailable",
                "mode": mode,
                "supported": False,
                "taskRunId": task_run_id,
                "resumeCursor": resume_cursor,
                "reason": "target is terminal",
                "fallbacks": fallbacks,
            }
        if not _task_run_supports_live_follow(
            task_run_id=task_run_id,
            target_evidence=target_evidence,
        ):
            return {
                "status": "unsupported",
                "mode": mode,
                "supported": False,
                "taskRunId": task_run_id,
                "resumeCursor": resume_cursor,
                "reason": "task run does not support live follow",
                "fallbacks": fallbacks,
            }
        return {
            "status": "active",
            "mode": mode,
            "supported": True,
            "taskRunId": task_run_id,
            "resumeCursor": resume_cursor,
            "fallbacks": fallbacks,
        }

    @staticmethod
    def _diagnosis_hints_payload(target_evidence: Mapping[str, Any]) -> dict[str, Any]:
        hints = _safe_string_list(target_evidence.get("diagnosisHints") or [])
        return {"diagnosisHints": hints} if hints else {}

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

    async def publish_target_annotation(
        self,
        *,
        remediation_workflow_id: str,
        target_workflow_id: str,
        target_run_id: str,
        name: str,
        payload: Mapping[str, Any],
        principal: str = "service:remediation-lifecycle",
    ) -> db_models.TemporalArtifact:
        """Publish a supplemental remediation annotation linked to the target."""

        artifact = await self.publish_json_artifact(
            remediation_workflow_id=remediation_workflow_id,
            artifact_type="remediation.target_annotation",
            name=name,
            payload=payload,
            target_workflow_id=target_workflow_id,
            target_run_id=target_run_id,
            principal=principal,
        )
        target_record = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            _required_string(target_workflow_id, "target_workflow_id"),
        )
        if target_record is None:
            raise RemediationContextError(
                f"Target execution not found: {target_workflow_id}"
            )
        self._session.add(
            db_models.TemporalArtifactLink(
                artifact_id=artifact.artifact_id,
                namespace=target_record.namespace,
                workflow_id=target_record.workflow_id,
                run_id=_required_string(target_run_id, "target_run_id"),
                link_type="remediation.target_annotation",
                label=name,
                created_by_activity_type="remediation.target.annotation.publish",
            )
        )
        refs = list(target_record.artifact_refs or [])
        if artifact.artifact_id not in refs:
            refs.append(artifact.artifact_id)
            target_record.artifact_refs = refs
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

def build_non_applicable_remediation_artifact_reason(
    *,
    artifact_type: str,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe why an expected remediation artifact is not applicable."""

    normalized_artifact_type = _required_string(artifact_type, "artifact_type")
    if normalized_artifact_type not in REMEDIATION_ARTIFACT_TYPES:
        raise ValueError(
            f"artifact_type must be one of {sorted(REMEDIATION_ARTIFACT_TYPES)}"
        )
    payload: dict[str, Any] = {
        "artifactType": normalized_artifact_type,
        "reason": _required_redacted_text(reason, "reason"),
    }
    if safe_metadata := _safe_policy_mapping(metadata):
        payload["metadata"] = safe_metadata
    return payload

def build_remediation_evidence_set(
    *,
    remediation_workflow_id: str,
    remediation_run_id: str,
    target_workflow_id: str,
    target_run_id: str,
    artifacts: Mapping[str, Any],
    non_applicable_artifacts: Sequence[Mapping[str, Any]] = (),
    evidence_degraded: bool = False,
    degraded_reasons: Sequence[Any] = (),
) -> dict[str, Any]:
    """Build the queryable remediation evidence set for verification."""

    non_applicable: list[dict[str, Any]] = []
    for item in non_applicable_artifacts[:50]:
        if not isinstance(item, Mapping):
            continue
        reason = build_non_applicable_remediation_artifact_reason(
            artifact_type=item.get("artifactType") or item.get("artifact_type"),
            reason=item.get("reason"),
            metadata=item.get("metadata")
            if isinstance(item.get("metadata"), Mapping)
            else None,
        )
        non_applicable.append(reason)

    return {
        "schemaVersion": "v1",
        "remediationWorkflowId": _required_lifecycle_string(
            remediation_workflow_id, "remediation_workflow_id"
        ),
        "remediationRunId": _required_lifecycle_string(
            remediation_run_id, "remediation_run_id"
        ),
        "targetWorkflowId": _required_lifecycle_string(
            target_workflow_id, "target_workflow_id"
        ),
        "targetRunId": _required_lifecycle_string(target_run_id, "target_run_id"),
        "artifacts": _artifact_refs_mapping(artifacts),
        "nonApplicableArtifacts": non_applicable,
        "evidenceDegraded": bool(evidence_degraded),
        "degradedReasons": _safe_string_list(degraded_reasons),
    }

def build_remediation_target_annotation(
    *,
    target_workflow_id: str,
    target_run_id: str,
    remediation_workflow_id: str,
    remediation_run_id: str,
    action_kind: str,
    decision: str,
    artifact_refs: Mapping[str, Any],
    timestamp: datetime | str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded, supplemental annotation for the target execution."""

    payload: dict[str, Any] = {
        "schemaVersion": "v1",
        "kind": "remediation.target_annotation",
        "targetWorkflowId": _required_lifecycle_string(
            target_workflow_id, "target_workflow_id"
        ),
        "targetRunId": _required_lifecycle_string(target_run_id, "target_run_id"),
        "remediationWorkflowId": _required_lifecycle_string(
            remediation_workflow_id, "remediation_workflow_id"
        ),
        "remediationRunId": _required_lifecycle_string(
            remediation_run_id, "remediation_run_id"
        ),
        "actionKind": _required_redacted_text(action_kind, "action_kind"),
        "decision": _validated_choice(
            decision, REMEDIATION_REPAIR_DECISIONS, "decision"
        ),
        "artifactRefs": _artifact_refs_mapping(artifact_refs),
        "timestamp": _timestamp_string(timestamp),
    }
    if safe_metadata := _safe_policy_mapping(metadata):
        payload["metadata"] = safe_metadata
    return payload

def build_remediation_repair_decision(
    *,
    target_workflow_id: str,
    pinned_run_id: str,
    decision: str,
    decision_reason: str,
    repair_outcome: str,
    current_run_id: str | None = None,
    candidate_action_kind: str | None = None,
    candidate_reason: str | None = None,
    fresh_target_health_ref: str | None = None,
    authority_decision_ref: str | None = None,
    guard_decision_ref: str | None = None,
    action_request_ref: str | None = None,
    action_result_ref: str | None = None,
    verification_ref: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded target-level immediate repair decision."""

    normalized_decision = _validated_choice(
        decision, REMEDIATION_REPAIR_DECISIONS, "decision"
    )
    normalized_outcome = _validated_choice(
        repair_outcome, REMEDIATION_REPAIR_OUTCOMES, "repair_outcome"
    )
    if normalized_decision == "attempted" and not (
        action_request_ref and action_result_ref and verification_ref
    ):
        raise ValueError(
            "attempted repair requires action_request_ref, action_result_ref, "
            "and verification_ref"
        )
    if normalized_decision != "attempted" and normalized_outcome == "repaired":
        raise ValueError("repair_outcome repaired requires an attempted repair")

    pinned = _required_lifecycle_string(pinned_run_id, "pinned_run_id")
    current = _safe_identifier_string(current_run_id) or pinned
    payload: dict[str, Any] = {
        "schemaVersion": "v1",
        "target": {
            "workflowId": _required_lifecycle_string(
                target_workflow_id, "target_workflow_id"
            ),
            "pinnedRunId": pinned,
            "currentRunId": current,
            "targetRunChanged": current != pinned,
        },
        "decision": normalized_decision,
        "decisionReason": _required_redacted_text(
            decision_reason, "decision_reason"
        ),
        "artifactRefs": _repair_artifact_refs(
            fresh_target_health_ref=fresh_target_health_ref,
            authority_decision_ref=authority_decision_ref,
            guard_decision_ref=guard_decision_ref,
            action_request_ref=action_request_ref,
            action_result_ref=action_result_ref,
            verification_ref=verification_ref,
        ),
        "repairOutcome": normalized_outcome,
    }
    candidate = _repair_candidate_payload(
        action_kind=candidate_action_kind,
        reason=candidate_reason,
    )
    if candidate:
        payload["candidate"] = candidate
    if safe_metadata := _safe_policy_mapping(metadata):
        payload["metadata"] = safe_metadata
    return payload

def build_remediation_prevention_outcome(
    *,
    status: str,
    root_cause_category: str,
    summary: str,
    branch: str | None = None,
    commit: str | None = None,
    pull_request_url: str | None = None,
    findings_ref: str | None = None,
    blocked_reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded recurrence-prevention outcome."""

    normalized_status = _validated_choice(
        status, REMEDIATION_PREVENTION_STATUSES, "status"
    )
    safe_pr_url = _safe_public_url(pull_request_url)
    safe_findings_ref = _artifact_ref_string(findings_ref, "findings_ref")
    safe_blocked_reason = _redacted_optional_text(blocked_reason)
    if normalized_status == "reviewable_change_created" and not safe_pr_url:
        raise ValueError("pullRequestUrl is required for reviewable_change_created")
    if normalized_status == "findings_reported" and not (
        safe_findings_ref or _redacted_optional_text(summary)
    ):
        raise ValueError("findings_reported requires findingsRef or summary")
    if normalized_status == "no_reviewable_fix" and not _redacted_optional_text(
        summary
    ):
        raise ValueError("no_reviewable_fix requires a summary reason")
    if normalized_status == "policy_blocked" and not safe_blocked_reason:
        raise ValueError("blockedReason is required for policy_blocked")

    payload: dict[str, Any] = {
        "schemaVersion": "v1",
        "status": normalized_status,
        "rootCauseCategory": _required_redacted_text(
            root_cause_category, "root_cause_category"
        ),
        "summary": _required_redacted_text(summary, "summary"),
    }
    if safe_branch := _redacted_optional_text(branch):
        payload["branch"] = safe_branch
    if safe_commit := _redacted_optional_text(commit):
        payload["commit"] = safe_commit
    if safe_pr_url:
        payload["pullRequestUrl"] = safe_pr_url
    if safe_findings_ref:
        payload["findingsRef"] = safe_findings_ref
    if safe_blocked_reason:
        payload["blockedReason"] = safe_blocked_reason
    if safe_metadata := _safe_policy_mapping(metadata):
        payload["metadata"] = safe_metadata
    return payload

def build_remediation_decision_log(
    *,
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a bounded remediation decision log artifact payload."""

    safe_entries: list[dict[str, Any]] = []
    for raw_entry in entries[:100]:
        if not isinstance(raw_entry, Mapping):
            continue
        entry: dict[str, Any] = {
            "timestamp": _timestamp_string(raw_entry.get("timestamp")),
            "phase": normalize_remediation_phase(raw_entry.get("phase")),
            "decisionType": _required_redacted_text(
                raw_entry.get("decisionType"), "decisionType"
            ),
            "decision": _required_redacted_text(
                raw_entry.get("decision"), "decision"
            ),
            "reason": _required_redacted_text(raw_entry.get("reason"), "reason"),
            "actor": _redacted_optional_text(raw_entry.get("actor")),
            "actionKind": _redacted_optional_text(raw_entry.get("actionKind")),
            "targetWorkflowId": _required_lifecycle_string(
                raw_entry.get("targetWorkflowId"), "targetWorkflowId"
            ),
            "targetRunId": _required_lifecycle_string(
                raw_entry.get("targetRunId"), "targetRunId"
            ),
            "artifactRefs": _artifact_refs_mapping(raw_entry.get("artifactRefs")),
            "metadata": _safe_policy_mapping(raw_entry.get("metadata")) or {},
        }
        safe_entries.append(
            {
                key: value
                for key, value in entry.items()
                if value not in (None, {}, [])
            }
        )
    if not safe_entries:
        raise ValueError("entries must include at least one decision log entry")
    return {"schemaVersion": "v1", "entries": safe_entries}

def build_remediation_final_summary(
    *,
    summary: Mapping[str, Any],
    repair: Mapping[str, Any],
    prevention: Mapping[str, Any],
    decision_log_ref: str | None = None,
    final_audit_ref: str | None = None,
    lock_release: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach repair/prevention lifecycle output to a remediation summary."""

    if not isinstance(summary, Mapping):
        raise ValueError("summary must be an object")
    _validate_repair_payload(repair)
    _validate_prevention_payload(prevention)
    normalized_lock_release = _validated_choice(
        lock_release, REMEDIATION_LOCK_RELEASE_STATUSES, "lock_release"
    )
    payload = _safe_lifecycle_payload(summary)
    payload["repair"] = _safe_lifecycle_payload(repair)
    payload["prevention"] = _safe_lifecycle_payload(prevention)
    if ref := _artifact_ref_string(decision_log_ref, "decision_log_ref"):
        payload["decisionLogRef"] = ref
    if ref := _artifact_ref_string(final_audit_ref, "final_audit_ref"):
        payload["finalAuditRef"] = ref
    payload["lockRelease"] = normalized_lock_release
    if safe_metadata := _safe_policy_mapping(metadata):
        payload["metadata"] = safe_metadata
    return payload

def build_corrected_instruction_retry_provenance(
    *,
    original_input_ref: str,
    remediation_context_ref: str,
    corrected_instructions_ref: str,
    retry_action_kind: str,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record corrected-instruction retry provenance without mutating input."""

    payload = {
        "schemaVersion": "v1",
        "retryActionKind": _required_redacted_text(
            retry_action_kind, "retry_action_kind"
        ),
        "originalInputRef": _required_artifact_ref_string(
            original_input_ref, "original_input_ref"
        ),
        "remediationContextRef": _required_artifact_ref_string(
            remediation_context_ref, "remediation_context_ref"
        ),
        "correctedInstructionsRef": _required_artifact_ref_string(
            corrected_instructions_ref, "corrected_instructions_ref"
        ),
        "reason": _required_redacted_text(reason, "reason"),
        "metadata": _safe_policy_mapping(metadata) or {},
        "originalInputMutation": False,
    }
    return {key: value for key, value in payload.items() if value not in ({}, None)}

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
    source_kind = kind
    if isinstance(raw_ref, Mapping):
        source_kind = kind or _safe_optional_string(raw_ref.get("kind"))
        ref = _string_or_none(raw_ref.get("artifact_id") or raw_ref.get("artifactId"))
    else:
        ref = _string_or_none(raw_ref)
    if not ref or not ref.startswith("art_"):
        return None
    payload: dict[str, str] = {"artifact_id": ref}
    if source_kind:
        payload["kind"] = source_kind
    return payload

def _artifact_ref_string(value: Any, field_name: str) -> str | None:
    text = _string_or_none(value)
    if text is None:
        return None
    if not text.startswith("art_"):
        raise ValueError(f"{field_name} must be an artifact ref")
    return text

def _required_artifact_ref_string(value: Any, field_name: str) -> str:
    text = _artifact_ref_string(value, field_name)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text

def _artifact_refs_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    refs: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _string_or_none(raw_key)
        if not key or _is_secret_like_key(key):
            continue
        ref = _artifact_ref_string(raw_value, key)
        if ref:
            refs[key] = ref
    return refs

def _repair_artifact_refs(
    *,
    fresh_target_health_ref: str | None,
    authority_decision_ref: str | None,
    guard_decision_ref: str | None,
    action_request_ref: str | None,
    action_result_ref: str | None,
    verification_ref: str | None,
) -> dict[str, str]:
    candidates = {
        "freshTargetHealth": fresh_target_health_ref,
        "authorityDecision": authority_decision_ref,
        "guardDecision": guard_decision_ref,
        "actionRequest": action_request_ref,
        "actionResult": action_result_ref,
        "verification": verification_ref,
    }
    return {
        key: ref
        for key, value in candidates.items()
        if (ref := _artifact_ref_string(value, key)) is not None
    }

def _repair_candidate_payload(
    *,
    action_kind: str | None,
    reason: str | None,
) -> dict[str, str]:
    payload: dict[str, str] = {}
    if safe_action := _redacted_optional_text(action_kind):
        payload["actionKind"] = safe_action
    if safe_reason := _redacted_optional_text(reason):
        payload["reason"] = safe_reason
    return payload

def _validated_choice(value: Any, allowed: frozenset[str], field_name: str) -> str:
    normalized = _string_or_none(value)
    if normalized not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")
    return normalized

def _required_lifecycle_string(value: Any, field_name: str) -> str:
    normalized = _string_or_none(value)
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if not _is_identifier_field(field_name) and _is_unsafe_context_string(normalized):
        raise ValueError(f"{field_name} is unsafe")
    return normalized

def _safe_identifier_string(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if not normalized:
        return None
    return normalized

def _required_redacted_text(value: Any, field_name: str) -> str:
    normalized = _redacted_optional_text(value)
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized

def _redacted_optional_text(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if not normalized:
        return None
    redacted = redact_sensitive_text(normalized)
    if redacted is None:
        return None
    redacted = redacted.strip()
    if not redacted or _is_unsafe_context_string(redacted):
        return None
    return redacted

def _safe_public_url(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if not normalized:
        return None
    redacted = redact_sensitive_text(normalized)
    if redacted is None:
        return None
    redacted = redacted.strip()
    lowered = redacted.lower()
    if not lowered.startswith(("http://", "https://")):
        return None
    if any(
        part in lowered and "[redacted]" not in lowered
        for part in ("token=", "signature=", "credential=", "password=")
    ):
        return None
    return redacted

def _validate_repair_payload(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("repair must be an object")
    _validated_choice(
        value.get("decision"), REMEDIATION_REPAIR_DECISIONS, "repair.decision"
    )
    _validated_choice(
        value.get("repairOutcome"),
        REMEDIATION_REPAIR_OUTCOMES,
        "repair.repairOutcome",
    )

def _validate_prevention_payload(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("prevention must be an object")
    _validated_choice(
        value.get("status"), REMEDIATION_PREVENTION_STATUSES, "prevention.status"
    )

def _artifact_ref_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str | None]] = set()
    for item in value[:50]:
        ref = _artifact_ref_payload(item, kind=None)
        if ref is None:
            continue
        key = (ref.get("artifact_id") or "", ref.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs

def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]

def _match_step_evidence(
    selector: Mapping[str, Any],
    evidence_steps: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    selector_task_run_id = _string_or_none(selector.get("taskRunId"))
    selector_logical_step_id = _string_or_none(selector.get("logicalStepId"))
    selector_attempt = _positive_int_or_none(selector.get("attempt"))
    if (
        not selector_task_run_id
        and not selector_logical_step_id
        and selector_attempt is None
    ):
        return None
    for item in evidence_steps:
        task_run_id = _string_or_none(item.get("taskRunId"))
        if selector_task_run_id and task_run_id != selector_task_run_id:
            continue
        logical_step_id = _string_or_none(item.get("logicalStepId"))
        if selector_logical_step_id and logical_step_id != selector_logical_step_id:
            continue
        attempt = _positive_int_or_none(item.get("attempt"))
        if selector_attempt is not None and attempt != selector_attempt:
            continue
        return item
    return None

def _has_task_run_evidence(item: Mapping[str, Any], field_name: str) -> bool:
    value = item.get(field_name)
    if field_name == "continuityRefs":
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ) and any(isinstance(ref, Mapping) for ref in value)
    return isinstance(value, Mapping)

def _durable_fallback_classes(task_runs: Sequence[Mapping[str, Any]]) -> list[str]:
    classes: list[str] = []
    for class_name, field_name in (
        ("merged_logs", "mergedLogsRef"),
        ("stdout", "stdoutRef"),
        ("stderr", "stderrRef"),
        ("diagnostics", "diagnosticsRef"),
    ):
        if any(_has_task_run_evidence(item, field_name) for item in task_runs):
            classes.append(class_name)
    return classes

def _live_follow_denied_by_policy(evidence_policy: Mapping[str, Any]) -> bool:
    for key in ("allowLiveFollow", "liveFollowAllowed", "includeLiveFollow"):
        if evidence_policy.get(key) is False:
            return True
    return False

def _task_run_supports_live_follow(
    *,
    task_run_id: str | None,
    target_evidence: Mapping[str, Any],
) -> bool:
    live_follow = target_evidence.get("liveFollow")
    if isinstance(live_follow, Mapping) and live_follow.get("supported") is True:
        return True
    if not task_run_id:
        return False
    for item in _mapping_list(target_evidence.get("taskRuns")):
        if _string_or_none(item.get("taskRunId")) == task_run_id:
            return bool(
                item.get("liveFollowSupported") is True
                or item.get("supportsLiveFollow") is True
            )
    return False

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
    if not isinstance(value, Mapping):
        return {}
    sanitized = _safe_policy_mapping(value) or {}
    if "pullRequestUrl" in value:
        if safe_pr_url := _safe_public_url(value.get("pullRequestUrl")):
            sanitized["pullRequestUrl"] = safe_pr_url
        else:
            sanitized.pop("pullRequestUrl", None)
    return sanitized

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
        or ("token=" in normalized and "[redacted]" not in normalized)
        or ("password=" in normalized and "[redacted]" not in normalized)
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
