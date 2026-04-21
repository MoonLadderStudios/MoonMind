"""Remediation context artifact builder.

This module keeps remediation evidence packaging at the service/activity boundary so
workflow history carries refs and compact metadata instead of raw logs or artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactService,
)

REMEDIATION_CONTEXT_LINK_TYPE = "remediation.context"
REMEDIATION_CONTEXT_ARTIFACT_NAME = "reports/remediation_context.json"
REMEDIATION_CONTEXT_SCHEMA_VERSION = "v1"
MAX_REMEDIATION_CONTEXT_TAIL_LINES = 2000
MAX_REMEDIATION_CONTEXT_TASK_RUN_IDS = 20


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
        policy = dict(value) if isinstance(value, Mapping) else {}
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
        if not isinstance(value, Mapping):
            return None
        return dict(value)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return _string_or_none(value)


def _artifact_ref_payload(raw_ref: Any, *, kind: str | None) -> dict[str, str] | None:
    ref = _string_or_none(raw_ref)
    if not ref:
        return None
    payload: dict[str, str]
    if ref.startswith("art_"):
        payload = {"artifact_id": ref}
    else:
        payload = {"ref": ref}
    if kind:
        payload["kind"] = kind
    return payload


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
