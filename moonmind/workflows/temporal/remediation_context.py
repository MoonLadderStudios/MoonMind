"""Remediation context artifact builder.

This module keeps remediation evidence packaging at the service/activity boundary so
workflow history carries refs and compact metadata instead of raw logs or artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text
from moonmind.workflows.temporal.artifacts import ExecutionRef, TemporalArtifactService

REMEDIATION_CONTEXT_LINK_TYPE = "remediation.context"
REMEDIATION_CONTEXT_ARTIFACT_NAME = "reports/remediation_context.json"
REMEDIATION_CONTEXT_SCHEMA_VERSION = "v1"
MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS = 20
# Failure evidence is embedded, not referenced: an Omnigent remediation agent
# reads the context from its workspace and has no MoonMind API to follow refs.
MAX_FAILURE_EVIDENCE_DOCUMENT_BYTES = 64 * 1024
MAX_FAILURE_EVIDENCE_READ_BYTES = 1024 * 1024
MAX_FAILURE_EVIDENCE_TEXT_CHARS = 4000
MAX_FAILURE_EVIDENCE_STEPS = 60
FINISH_SUMMARY_EVIDENCE_KEYS = (
    "failure",
    "failureSummary",
    "finishOutcome",
    "lastStep",
    "operatorSummary",
    "publish",
    "recoveryManifest",
    "incidentReconstruction",
)
TARGET_EVIDENCE_CLASSES = (
    ("stdout", "stdoutRef"),
    ("stderr", "stderrRef"),
    ("merged_logs", "mergedLogsRef"),
    ("diagnostics", "diagnosticsRef"),
    ("provider_snapshot", "providerSnapshotRef"),
    ("continuity", "continuityRefs"),
)
OMNIGENT_EVIDENCE_INDEX_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("execution_and_steps", ("executionDetailRef", "stepExecutionDetailRefs", "failureTaxonomyRef")),
    ("bridge_events", ("bridgeEventPageRefs", "normalizedEventJournalRef", "rawEventJournalRef")),
    ("capture", ("initialSnapshotRef", "finalSnapshotRef", "captureManifestRef", "resourceManifestRef")),
    ("workspace", ("changedFilesRef", "workspaceFilesRef", "sessionFilesRef", "diffRef", "childSessionRefs")),
    ("host_and_session", ("hostRef", "sessionRef", "agentRef", "hostBindingRef", "endpointRef")),
    ("provider_and_leases", ("providerProfileRef", "providerLeaseRef", "hostLeaseRef", "credentialGenerationRef")),
    ("launch", ("executionProfileRef", "launchPolicyRef", "effectiveLaunchSnapshotRef")),
    ("checkpoint_and_recovery", ("checkpointManifestRefs", "externalStateRefs", "workspaceAuthorityRef", "recoveryDecisionRef")),
    ("checkpoint_branches", ("checkpointBranchRefs", "branchTurnRefs", "gitRef", "comparisonRef", "promotionRef")),
    ("lifecycle", ("incidentManifestRef", "recoveryManifestRef", "cleanupManifestRef", "janitorManifestRef", "terminalPublicationManifestRef")),
    ("policy", ("policySnapshotRef", "approvalSnapshotRef", "lockSnapshotRef", "capturePolicyRef", "retentionPolicyRef", "redactionPolicyRef")),
    ("prior_remediation", ("priorAttemptRefs", "cumulativeWorkspaceHeadRef", "verificationResultRefs", "preventionOutputRefs")),
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


# (workflow_id, run_id): the ledger of that exact run.
StepLedgerLoader = Callable[[str, str | None], Awaitable[Sequence[Mapping[str, Any]]]]


async def load_remediation_target(
    session: AsyncSession, workflow_id: str
) -> db_models.TemporalExecutionRecord | db_models.TemporalExecutionCanonicalRecord | None:
    """Load a remediation target: its API source row, else its projection.

    Temporal Schedules start runs without an API source row, so their
    Temporal-authoritative projection is the durable record of them. Source
    and projection rows are reconciled together, run id included.
    """

    source = await session.get(db_models.TemporalExecutionCanonicalRecord, workflow_id)
    if source is not None:
        return source
    return await session.get(db_models.TemporalExecutionRecord, workflow_id)


class RemediationContextBuilder:
    """Build bounded remediation context artifacts from persisted remediation links."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        artifact_service: TemporalArtifactService,
        step_ledger_loader: StepLedgerLoader | None = None,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service
        self._step_ledger_loader = step_ledger_loader

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

        target_record = await load_remediation_target(
            self._session, link.target_workflow_id
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
        failure_evidence = await self._failure_evidence(
            target_record, run_id=link.target_run_id, principal=principal
        )
        payload["failureEvidence"] = failure_evidence
        payload["boundedness"]["artifactContentsIncluded"] = bool(
            failure_evidence["documents"]
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

    async def _failure_evidence(
        self,
        record: Any,
        *,
        run_id: str | None,
        principal: str,
    ) -> dict[str, Any]:
        """Embed what the failed run recorded about its failure, bounded."""

        memo = record.memo if isinstance(record.memo, Mapping) else {}
        finish = getattr(record, "finish_summary_json", None)
        if not isinstance(finish, Mapping):
            finish = memo.get("finishSummary") or memo.get("finish_summary")
        finish = finish if isinstance(finish, Mapping) else {}

        evidence: dict[str, Any] = {
            key: _bounded_evidence_value(redact_sensitive_payload(finish[key]))
            for key in FINISH_SUMMARY_EVIDENCE_KEYS
            if finish.get(key) is not None
        }
        evidence["authoredWork"] = _authored_work(record)
        unavailable: list[dict[str, Any]] = []
        failure = finish.get("failure")
        steps, steps_reason = await self._failure_steps(
            record.workflow_id,
            run_id=run_id,
            failed_step_id=(
                _string_or_none(failure.get("stepId"))
                if isinstance(failure, Mapping)
                else None
            ),
        )
        evidence["steps"] = steps
        if steps_reason:
            unavailable.append({"kind": "step_ledger", "reason": steps_reason})
        documents: list[dict[str, Any]] = []
        for kind, artifact_id in _failure_document_refs(finish, memo):
            document, available = await self._evidence_document(
                kind,
                artifact_id,
                target_workflow_id=record.workflow_id,
                principal=principal,
            )
            (documents if available else unavailable).append(document)
        evidence["documents"] = documents
        evidence["unavailable"] = unavailable
        evidence["maxDocumentBytes"] = MAX_FAILURE_EVIDENCE_DOCUMENT_BYTES
        return evidence

    async def _failure_steps(
        self,
        workflow_id: str,
        *,
        run_id: str | None,
        failed_step_id: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if self._step_ledger_loader is None:
            return [], "step ledger is not readable here"
        try:
            rows = await self._step_ledger_loader(workflow_id, run_id)
        except Exception as exc:  # degraded evidence, not a failed remediation
            return [], f"step ledger query failed: {type(exc).__name__}"
        steps: list[dict[str, Any]] = []
        for row in _bounded_failure_ledger_rows(rows, failed_step_id):
            artifacts = row.get("artifacts")
            artifacts = artifacts if isinstance(artifacts, Mapping) else {}
            step = {
                "logicalStepId": _string_or_none(row.get("logicalStepId")),
                "order": row.get("order"),
                "title": _string_or_none(row.get("title")),
                "status": _string_or_none(row.get("status")),
                "executionOrdinal": row.get("executionOrdinal")
                or row.get("attempt"),
                "summary": _bounded_evidence_value(
                    redact_sensitive_text(_string_or_none(row.get("summary")))
                ),
                "lastError": _bounded_evidence_value(
                    redact_sensitive_text(_string_or_none(row.get("lastError")))
                ),
                "artifacts": {
                    key: artifacts[key]
                    for key in (
                        "outputSummary",
                        "runtimeDiagnostics",
                        "runtimeStderr",
                        "runtimeMergedLogs",
                    )
                    if _string_or_none(artifacts.get(key))
                },
            }
            steps.append(
                {key: value for key, value in step.items() if value not in (None, "", {})}
            )
        return steps, None

    async def _evidence_document(
        self,
        kind: str,
        artifact_id: str,
        *,
        target_workflow_id: str,
        principal: str,
    ) -> tuple[dict[str, Any], bool]:
        document: dict[str, Any] = {"kind": kind, "artifactRef": artifact_id}
        # The target's lifecycle manifests are owned by its workflow principal,
        # and its runtime evidence is linked to its execution.
        admitted = f"workflow:{target_workflow_id}"
        try:
            artifact, _links, _pinned, _policy = (
                await self._artifact_service.get_metadata(
                    artifact_id=artifact_id,
                    principal=principal,
                    admitted_principal=admitted,
                )
            )
            size = int(getattr(artifact, "size_bytes", 0) or 0)
            if size > MAX_FAILURE_EVIDENCE_READ_BYTES:
                return (
                    {**document, "reason": "too large to embed", "sizeBytes": size},
                    False,
                )
            _artifact, body = await self._artifact_service.read(
                artifact_id=artifact_id,
                principal=principal,
                admitted_principal=admitted,
            )
        except Exception as exc:  # degraded evidence, not a failed remediation
            return {**document, "reason": f"unreadable: {type(exc).__name__}"}, False
        text = body.decode("utf-8", errors="replace")
        if len(text) <= MAX_FAILURE_EVIDENCE_DOCUMENT_BYTES:
            try:
                document["content"] = redact_sensitive_payload(json.loads(text))
                return document, True
            except ValueError:
                pass
        document["contentText"] = redact_sensitive_text(
            text[:MAX_FAILURE_EVIDENCE_DOCUMENT_BYTES]
        )
        document["truncated"] = len(text) > MAX_FAILURE_EVIDENCE_DOCUMENT_BYTES
        return document, True

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
        agent_run_ids = self._normalize_agent_run_ids(target_mapping.get("agentRunIds"))
        target_evidence = self._target_evidence_payload(target_record)
        if not agent_run_ids:
            agent_run_ids = self._agent_run_ids_from_evidence(target_evidence)
        evidence_policy = self._normalize_evidence_policy(
            remediation.get("evidencePolicy")
            if isinstance(remediation, Mapping)
            else None
        )
        agent_runs = self._normalize_agent_run_evidence(
            agent_run_ids=agent_run_ids,
            target_evidence=target_evidence,
        )
        availability = self._evidence_availability(agent_runs=agent_runs)
        omnigent_evidence_index = self._omnigent_evidence_index(target_evidence)
        degraded_statuses = {
            "missing",
            "partial",
            "denied",
            "unavailable",
            "unsupported",
        }
        unavailable_classes = list(
            dict.fromkeys(
                item["class"]
                for item in (*availability, *omnigent_evidence_index)
                if item.get("status") in degraded_statuses
            )
        )

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
                "agentRuns": agent_runs,
                "availability": availability,
                "omnigentIndex": omnigent_evidence_index,
                "evidenceDegraded": bool(unavailable_classes),
                "unavailableEvidenceClasses": unavailable_classes,
                **self._diagnosis_hints_payload(target_evidence),
            },
            "policies": {
                "authorityMode": link.authority_mode,
                "evidencePolicy": evidence_policy,
            },
            "boundedness": {
                "maxAgentRunIds": MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS,
                "rawLogBodiesIncluded": False,
                "artifactContentsIncluded": False,
            },
        }

    @staticmethod
    def _remediation_payload(
        record: db_models.TemporalExecutionCanonicalRecord,
    ) -> Mapping[str, Any]:
        parameters = record.parameters if isinstance(record.parameters, Mapping) else {}
        workflow_payload = (
            parameters.get("workflow") if isinstance(parameters, Mapping) else {}
        )
        workflow_mapping = workflow_payload if isinstance(workflow_payload, Mapping) else {}
        remediation = workflow_mapping.get("remediation")
        return remediation if isinstance(remediation, Mapping) else {}

    @staticmethod
    def _target_evidence_payload(
        record: db_models.TemporalExecutionCanonicalRecord,
    ) -> Mapping[str, Any]:
        for source in (
            record.memo,
            record.parameters,
            getattr(record, "integration_state", None),
        ):
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
        for item in value[:MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS]:
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
            agent_run_id = _string_or_none(item.get("agentRunId") or item.get("agent_run_id"))
            if agent_run_id:
                selector["agentRunId"] = agent_run_id
            checkpoint_ref = _string_or_none(
                item.get("checkpointRef") or item.get("checkpoint_ref")
            )
            if checkpoint_ref:
                selector["checkpointRef"] = checkpoint_ref
            checkpoint_digest = _string_or_none(
                item.get("checkpointDigest") or item.get("checkpoint_digest")
            )
            if checkpoint_digest:
                selector["checkpointDigest"] = checkpoint_digest
            checkpoint_boundary = _string_or_none(
                item.get("checkpointBoundary") or item.get("checkpoint_boundary")
            )
            if checkpoint_boundary:
                selector["checkpointBoundary"] = checkpoint_boundary
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
    def _normalize_agent_run_ids(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        agent_run_ids: list[str] = []
        seen: set[str] = set()
        for item in value:
            agent_run_id = _string_or_none(item)
            if not agent_run_id or agent_run_id in seen:
                continue
            seen.add(agent_run_id)
            agent_run_ids.append(agent_run_id)
            if len(agent_run_ids) >= MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS:
                break
        return agent_run_ids

    @staticmethod
    def _agent_run_ids_from_evidence(target_evidence: Mapping[str, Any]) -> list[str]:
        agent_run_ids: list[str] = []
        seen: set[str] = set()
        for item in _mapping_list(target_evidence.get("agentRuns")):
            agent_run_id = _string_or_none(item.get("agentRunId"))
            if not agent_run_id or agent_run_id in seen:
                continue
            seen.add(agent_run_id)
            agent_run_ids.append(agent_run_id)
            if len(agent_run_ids) >= MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS:
                break
        return agent_run_ids

    @staticmethod
    def _normalize_agent_run_evidence(
        *,
        agent_run_ids: Sequence[str],
        target_evidence: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        source_by_id = {
            agent_run_id: item
            for item in _mapping_list(target_evidence.get("agentRuns"))
            if (agent_run_id := _string_or_none(item.get("agentRunId")))
        }
        agent_runs: list[dict[str, Any]] = []
        for agent_run_id in agent_run_ids[:MAX_REMEDIATION_CONTEXT_AGENT_RUN_IDS]:
            entry: dict[str, Any] = {"agentRunId": agent_run_id}
            source = source_by_id.get(agent_run_id, {})
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
            agent_runs.append(entry)
        return agent_runs

    @staticmethod
    def _evidence_availability(
        *,
        agent_runs: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        has_merged = any(isinstance(item.get("mergedLogsRef"), Mapping) for item in agent_runs)
        availability: list[dict[str, str]] = []
        for class_name, field_name in TARGET_EVIDENCE_CLASSES:
            available = any(_has_agent_run_evidence(item, field_name) for item in agent_runs)
            if available:
                availability.append({"class": class_name, "status": "available"})
            else:
                record = {"class": class_name, "status": "missing"}
                if has_merged:
                    record["fallback"] = "merged_logs"
                availability.append(record)
        return availability

    @staticmethod
    def _omnigent_evidence_index(
        target_evidence: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Project the full host-backed evidence ledger without embedding bodies."""

        generated_at = _safe_optional_string(
            target_evidence.get("observedAt") or target_evidence.get("generatedAt")
        )
        index: list[dict[str, Any]] = []
        for class_name, fields in OMNIGENT_EVIDENCE_INDEX_FIELDS:
            refs: list[dict[str, str]] = []
            seen: set[tuple[str, str | None]] = set()
            for field_name in fields:
                raw_value = target_evidence.get(field_name)
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                for value in values:
                    ref = _artifact_ref_payload(value, kind=field_name)
                    if ref is None:
                        continue
                    key = (ref.get("artifact_id") or ref.get("ref") or "", ref.get("kind"))
                    if key in seen:
                        continue
                    seen.add(key)
                    refs.append(ref)
            record: dict[str, Any] = {
                "class": class_name,
                "status": "available" if refs else "missing",
                "bounded": True,
                "contentsIncluded": False,
                "refs": refs,
            }
            if generated_at:
                record["observedAt"] = generated_at
                record["freshness"] = "source_reported"
            else:
                record["freshness"] = "unknown"
            if not refs:
                record["degradedReason"] = "historical evidence was not recorded"
            index.append(record)
        return index

    @staticmethod
    def _diagnosis_hints_payload(target_evidence: Mapping[str, Any]) -> dict[str, Any]:
        hints = _safe_string_list(target_evidence.get("diagnosisHints") or [])
        return {"diagnosisHints": hints} if hints else {}

    @staticmethod
    def _normalize_evidence_policy(value: Any) -> dict[str, Any]:
        return _safe_policy_mapping(value) or {}

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
            ("input", getattr(record, "input_ref", None)),
            ("plan", getattr(record, "plan_ref", None)),
            ("manifest", getattr(record, "manifest_ref", None)),
        ):
            ref = _artifact_ref_payload(raw_ref, kind=kind)
            if ref is not None:
                key = (ref.get("artifact_id") or ref.get("ref") or "", ref.get("kind"))
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)

        return refs


def _bounded_evidence_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(value) <= MAX_FAILURE_EVIDENCE_TEXT_CHARS:
            return value
        return value[:MAX_FAILURE_EVIDENCE_TEXT_CHARS] + "...[truncated]"
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_evidence_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, list | tuple):
        return [_bounded_evidence_value(item, depth=depth + 1) for item in value[:50]]
    return _bounded_evidence_value(str(value), depth=depth)


def _bounded_failure_ledger_rows(
    rows: Sequence[Any], failed_step_id: str | None
) -> list[Mapping[str, Any]]:
    """Bound the ledger while keeping the failed steps and their neighbors.

    Remediation needs the failed row's status, error and artifact refs most,
    and a long plan can fail far past any head slice. Remaining room goes to
    the earliest rows; ledger order is kept.
    """

    mapped = [row for row in rows if isinstance(row, Mapping)]
    if len(mapped) <= MAX_FAILURE_EVIDENCE_STEPS:
        return mapped
    failed = [
        index
        for index, row in enumerate(mapped)
        if str(row.get("status") or "").strip().lower() == "failed"
        or (failed_step_id and row.get("logicalStepId") == failed_step_id)
    ]
    neighbors = [
        index
        for center in failed
        for index in range(center - 2, center + 3)
        if 0 <= index < len(mapped)
    ]
    kept: dict[int, None] = {}
    for index in [*failed, *neighbors, *range(len(mapped))]:
        if len(kept) >= MAX_FAILURE_EVIDENCE_STEPS:
            break
        kept.setdefault(index, None)
    return [mapped[index] for index in sorted(kept)]


def _authored_work(record: Any) -> dict[str, Any]:
    """What the failed run was asked to do, so remediation keeps its intent."""

    parameters = record.parameters if isinstance(record.parameters, Mapping) else {}
    workflow = parameters.get("workflow") or parameters.get("task")
    workflow = workflow if isinstance(workflow, Mapping) else {}
    steps = workflow.get("steps") if isinstance(workflow.get("steps"), list) else []
    authored_steps = []
    for step in steps[:MAX_FAILURE_EVIDENCE_STEPS]:
        if not isinstance(step, Mapping):
            continue
        tool = step.get("tool") or step.get("skill")
        tool = tool if isinstance(tool, Mapping) else {}
        authored_steps.append(
            {
                key: value
                for key, value in {
                    "id": _string_or_none(step.get("id")),
                    "title": _string_or_none(step.get("title")),
                    "skill": _string_or_none(tool.get("name") or tool.get("id")),
                    "instructions": _bounded_evidence_value(
                        redact_sensitive_text(_string_or_none(step.get("instructions")))
                    ),
                }.items()
                if value
            }
        )
    work = {
        "repository": _bounded_evidence_value(
            redact_sensitive_payload(parameters.get("repository"))
        ),
        "instructions": _bounded_evidence_value(
            redact_sensitive_text(
                _string_or_none(
                    workflow.get("instructions") or parameters.get("instructions")
                )
            )
        ),
        "steps": authored_steps,
    }
    return {key: value for key, value in work.items() if value}


def _failure_document_refs(
    finish: Mapping[str, Any], memo: Mapping[str, Any]
) -> list[tuple[str, str]]:
    def nested(key: str, field: str) -> Any:
        value = finish.get(key)
        return value.get(field) if isinstance(value, Mapping) else None

    candidates = (
        ("recovery_manifest", nested("recoveryManifest", "manifestRef")),
        (
            "incident_reconstruction",
            nested("incidentReconstruction", "manifestRef")
            or memo.get("incident_reconstruction_ref"),
        ),
        (
            "failed_step_diagnostics",
            nested("failure", "diagnosticsRef") or nested("lastStep", "diagnosticsRef"),
        ),
    )
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, raw_ref in candidates:
        ref = _string_or_none(raw_ref)
        if not ref:
            continue
        artifact_id = ref.removeprefix("artifact://").removeprefix("artifact:")
        if artifact_id.startswith("art_") and artifact_id not in seen:
            seen.add(artifact_id)
            refs.append((kind, artifact_id))
    return refs


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
    selector_agent_run_id = _string_or_none(selector.get("agentRunId"))
    selector_logical_step_id = _string_or_none(selector.get("logicalStepId"))
    selector_attempt = _positive_int_or_none(selector.get("attempt"))
    if (
        not selector_agent_run_id
        and not selector_logical_step_id
        and selector_attempt is None
    ):
        return None
    for item in evidence_steps:
        agent_run_id = _string_or_none(item.get("agentRunId"))
        if selector_agent_run_id and agent_run_id != selector_agent_run_id:
            continue
        logical_step_id = _string_or_none(item.get("logicalStepId"))
        if selector_logical_step_id and logical_step_id != selector_logical_step_id:
            continue
        attempt = _positive_int_or_none(item.get("attempt"))
        if selector_attempt is not None and attempt != selector_attempt:
            continue
        return item
    return None


def _has_agent_run_evidence(item: Mapping[str, Any], field_name: str) -> bool:
    value = item.get(field_name)
    if field_name == "continuityRefs":
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ) and any(isinstance(ref, Mapping) for ref in value)
    return isinstance(value, Mapping)


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


def _safe_optional_string(value: Any) -> str | None:
    normalized = _string_or_none(value)
    if not normalized or _is_unsafe_context_string(normalized):
        return None
    return normalized


def _is_secret_like_key(key: str) -> bool:
    if key in SAFE_POLICY_KEYS:
        return False
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in SECRET_LIKE_POLICY_KEY_PARTS)


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
