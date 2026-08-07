"""Trusted registry for agent-owned terminal evidence contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from moonmind.workflows.temporal.publish_auto_evidence import (
    AutoPublishEvidenceError,
    parse_auto_publish_evidence,
)

TerminalEvidenceOutcome = Literal[
    "terminal_success", "continuation_requested", "terminal_failure"
]


@dataclass(frozen=True)
class TerminalEvidenceEvaluation:
    satisfied: bool
    failure_code: str | None = None
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    outcome: TerminalEvidenceOutcome = "terminal_failure"


@dataclass(frozen=True)
class TerminalEvidenceSource:
    """Resolved, workspace-confined source for terminal contract evidence."""

    relative_path: str
    path: Path | None = None
    failure_code: str | None = None
    missing_evidence: tuple[str, ...] = ()


def _failure(
    failure_code: str,
    missing_evidence: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> TerminalEvidenceEvaluation:
    return TerminalEvidenceEvaluation(
        False, failure_code, missing_evidence, metadata or {}
    )


def _success(metadata: dict[str, Any] | None = None) -> TerminalEvidenceEvaluation:
    return TerminalEvidenceEvaluation(
        True, metadata=metadata or {}, outcome="terminal_success"
    )


def _evaluate_auto_publish_evidence(
    payload: Mapping[str, Any],
    *,
    contract_id: str,
    relative_path: str,
    expected_skill_id: str,
) -> TerminalEvidenceEvaluation:
    """Validate agent-owned publication without reimplementing Skill semantics."""

    metadata: dict[str, Any] = {
        "terminalContractId": contract_id,
        "terminalContractEvidencePath": relative_path,
        "terminalContractRetryable": False,
    }
    try:
        evidence = parse_auto_publish_evidence(payload)
    except AutoPublishEvidenceError:
        metadata["terminalContractRetryable"] = True
        return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)

    metadata.update(
        {
            "autoPublishSkillId": evidence.skill_id,
            "autoPublishStatus": evidence.status,
            "autoPublishAction": evidence.action,
        }
    )
    if expected_skill_id and evidence.skill_id != expected_skill_id:
        metadata["terminalContractRetryable"] = True
        return _failure("STALE_TERMINAL_EVIDENCE", metadata=metadata)
    if evidence.status == "blocked":
        metadata["autoPublishBlockedReason"] = evidence.blocked_reason
        return _failure("AUTO_PUBLISH_BLOCKED", metadata=metadata)
    if evidence.status == "failed":
        metadata["autoPublishBlockedReason"] = evidence.blocked_reason
        return _failure("AUTO_PUBLISH_FAILED", metadata=metadata)
    return _success(metadata)


def resolve_terminal_evidence_source(
    contract: Mapping[str, Any],
    *,
    workspace_path: str,
    artifact_spool_path: str = "",
) -> TerminalEvidenceSource:
    """Resolve the declared evidence file without escaping trusted run roots."""

    relative = str(contract.get("relativePath") or contract.get("relative_path") or "")
    normalized_relative = relative.replace("\\", "/")
    relative_path = Path(normalized_relative)
    workspace = Path(workspace_path).resolve()
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        return TerminalEvidenceSource(
            relative_path=normalized_relative,
            failure_code="INVALID_TERMINAL_EVIDENCE_PATH",
        )
    evidence_root = workspace
    evidence_relative = relative_path
    if artifact_spool_path and relative_path.parts[:1] == ("artifacts",):
        evidence_root = Path(artifact_spool_path).resolve()
        evidence_relative = Path(*relative_path.parts[1:])
    evidence_path = (evidence_root / evidence_relative).resolve()
    try:
        evidence_path.relative_to(evidence_root)
    except ValueError:
        return TerminalEvidenceSource(
            relative_path=normalized_relative,
            failure_code="INVALID_TERMINAL_EVIDENCE_PATH",
        )
    if not evidence_path.is_file():
        return TerminalEvidenceSource(
            relative_path=normalized_relative,
            failure_code="INCOMPLETE_TERMINAL_CONTRACT",
            missing_evidence=(relative,),
        )
    return TerminalEvidenceSource(
        relative_path=normalized_relative,
        path=evidence_path,
    )


def _evaluate_batch_dependabot_resolver_evidence(
    payload: Mapping[str, Any],
    *,
    contract_id: str,
    relative_path: str,
) -> TerminalEvidenceEvaluation:
    """Validate the authoritative discovery and child-enqueue result."""

    requested = payload.get("requested")
    created = payload.get("created")
    queued = payload.get("queued")
    would_queue = payload.get("wouldQueue")
    skipped = payload.get("skipped")
    errors = payload.get("errors")
    metadata = {
        "terminalContractId": contract_id,
        "terminalContractEvidencePath": relative_path,
        "queuedChildCount": created if isinstance(created, int) else 0,
        "queuedChildren": queued if isinstance(queued, list) else [],
    }
    status = payload.get("status")
    if status == "running":
        return _failure("INCOMPLETE_TERMINAL_CONTRACT", metadata=metadata)
    accounted = (
        len(would_queue)
        if status == "dry_run" and isinstance(would_queue, list)
        else created
    )
    if (
        not isinstance(requested, int)
        or requested < 0
        or not isinstance(created, int)
        or created < 0
        or not isinstance(queued, list)
        or not isinstance(would_queue, list)
        or not isinstance(skipped, list)
        or not isinstance(errors, list)
        or len(queued) != created
        or not isinstance(accounted, int)
        or accounted + len(skipped) + len(errors) != requested
    ):
        return _failure("INVALID_TERMINAL_EVIDENCE", metadata=metadata)

    failure_code = str(payload.get("failureCode") or "").strip()
    if status == "partial_failure":
        return _failure(
            failure_code or "BATCH_FANOUT_PARTIAL_FAILURE", metadata=metadata
        )
    if status == "failed":
        return _failure(failure_code or "BATCH_FANOUT_FAILED", metadata=metadata)
    if status == "dry_run":
        if (
            payload.get("dryRun") is True
            and created == 0
            and not errors
            and len(would_queue) + len(skipped) == requested
        ):
            return _success(metadata)
        return _failure("INVALID_TERMINAL_EVIDENCE", metadata=metadata)
    if status == "no_op":
        if created == 0 and not errors and len(skipped) == requested:
            return _success(metadata)
        return _failure("INVALID_TERMINAL_EVIDENCE", metadata=metadata)
    if status == "queued":
        if (
            created > 0
            and not errors
            and created + len(skipped) == requested
            and all(
                isinstance(item, Mapping)
                and str(item.get("workflowId") or "").strip()
                for item in queued
            )
        ):
            return _success(metadata)
        return _failure("INVALID_TERMINAL_EVIDENCE", metadata=metadata)
    return _failure("INCOMPLETE_TERMINAL_CONTRACT", metadata=metadata)


def evaluate_terminal_evidence(
    contract: Mapping[str, Any], *, workspace_path: str, artifact_spool_path: str = ""
) -> TerminalEvidenceEvaluation:
    contract_id = str(contract.get("contractId") or contract.get("contract_id") or "")
    if contract_id not in {
        "auto_publish_terminal.v1",
        "batch_dependabot_resolver_fanout.v1",
        "batch_workflows_fanout.v1",
        "pr_resolver_terminal.v1",
    }:
        return _failure("UNSUPPORTED_TERMINAL_CONTRACT")
    relative = str(contract.get("relativePath") or contract.get("relative_path") or "")
    source = resolve_terminal_evidence_source(
        contract,
        workspace_path=workspace_path,
        artifact_spool_path=artifact_spool_path,
    )
    normalized_relative = source.relative_path
    if source.failure_code:
        metadata = None
        if contract_id == "auto_publish_terminal.v1":
            metadata = {"terminalContractRetryable": True}
        return _failure(
            source.failure_code,
            source.missing_evidence,
            metadata,
        )
    if source.path is None:
        return _failure("INCOMPLETE_TERMINAL_CONTRACT", (relative,))
    workspace = Path(workspace_path).resolve()
    try:
        payload = json.loads(source.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _failure(
            "MALFORMED_TERMINAL_EVIDENCE",
            metadata=(
                {"terminalContractRetryable": True}
                if contract_id == "auto_publish_terminal.v1"
                else None
            ),
        )
    if not isinstance(payload, dict):
        return _failure(
            "MALFORMED_TERMINAL_EVIDENCE",
            metadata=(
                {"terminalContractRetryable": True}
                if contract_id == "auto_publish_terminal.v1"
                else None
            ),
        )
    if contract_id == "auto_publish_terminal.v1":
        return _evaluate_auto_publish_evidence(
            payload,
            contract_id=contract_id,
            relative_path=normalized_relative,
            expected_skill_id=str(
                contract.get("skillId") or contract.get("skill_id") or ""
            ).strip(),
        )
    if contract_id == "pr_resolver_terminal.v1":
        disposition = str(payload.get("mergeAutomationDisposition") or "").strip()
        expected_execution = str(
            contract.get("executionRef") or contract.get("execution_ref") or ""
        ).strip()
        evidence_execution = str(payload.get("executionRef") or "").strip()
        metadata = {
            "terminalContractId": contract_id,
            "terminalContractEvidencePath": normalized_relative,
            "terminalContractExecutionRef": expected_execution,
            "mergeAutomationDisposition": disposition,
        }
        if not expected_execution or evidence_execution != expected_execution:
            return _failure("STALE_TERMINAL_EVIDENCE", metadata=metadata)
        if disposition not in {
            "merged",
            "already_merged",
            "reenter_gate",
            "manual_review",
            "failed",
        }:
            return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)
        if disposition in {"merged", "already_merged"}:
            publish_path = (workspace / "artifacts/publish_result.json").resolve()
            if artifact_spool_path and not publish_path.is_file():
                publish_path = (
                    Path(artifact_spool_path) / "publish_result.json"
                ).resolve()
            if not publish_path.is_file():
                return _failure(
                    "INCOMPLETE_TERMINAL_CONTRACT",
                    ("artifacts/publish_result.json",),
                    metadata,
                )
            try:
                publish_payload = json.loads(
                    publish_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return _failure(
                    "MALFORMED_TERMINAL_EVIDENCE", metadata=metadata
                )
            publish_evaluation = _evaluate_auto_publish_evidence(
                publish_payload,
                contract_id="auto_publish_terminal.v1",
                relative_path="artifacts/publish_result.json",
                expected_skill_id="pr-resolver",
            )
            if not publish_evaluation.satisfied:
                return _failure(
                    publish_evaluation.failure_code
                    or "INVALID_TERMINAL_EVIDENCE",
                    publish_evaluation.missing_evidence,
                    {**metadata, **publish_evaluation.metadata},
                )
            return _success(metadata)
        if disposition == "reenter_gate":
            continuation = payload.get("gatedContinuation")
            if isinstance(continuation, Mapping):
                if (
                    continuation.get("schemaVersion") != "gated-continuation/v1"
                    or continuation.get("gateType") != "merge_automation"
                    or continuation.get("action") != "reenter_gate"
                ):
                    return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)
                not_before = str(continuation.get("notBefore") or "").strip()
                retry_after = continuation.get("retryAfterSeconds")
                if not_before and retry_after is not None:
                    return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)
                if not_before:
                    try:
                        parsed = datetime.fromisoformat(
                            not_before.replace("Z", "+00:00")
                        )
                    except ValueError:
                        return _failure(
                            "MALFORMED_TERMINAL_EVIDENCE", metadata=metadata
                        )
                    if parsed.tzinfo is None:
                        return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)
                if retry_after is not None and (
                    isinstance(retry_after, bool)
                    or not isinstance(retry_after, int)
                    or retry_after < 1
                ):
                    return _failure("MALFORMED_TERMINAL_EVIDENCE", metadata=metadata)
                metadata["gatedContinuation"] = dict(continuation)
            metadata["terminalContractOutcome"] = "continuation_requested"
            return TerminalEvidenceEvaluation(
                False, metadata=metadata, outcome="continuation_requested"
            )
        failure_codes = {
            "manual_review": "PR_RESOLVER_MANUAL_REVIEW",
            "failed": "PR_RESOLVER_FAILED",
        }
        return _failure(failure_codes[disposition], metadata=metadata)
    expected_schema = str(
        contract.get("expectedSchemaVersion")
        or contract.get("expected_schema_version")
        or "moonmind.batch-workflows-result.v1"
    )
    expected_execution = str(
        contract.get("executionRef") or contract.get("execution_ref") or ""
    )
    if payload.get("schemaVersion") != expected_schema or payload.get("contractId") != contract_id:
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE")
    if not expected_execution or payload.get("executionRef") != expected_execution:
        return TerminalEvidenceEvaluation(False, "STALE_TERMINAL_EVIDENCE")
    if contract_id == "batch_dependabot_resolver_fanout.v1":
        return _evaluate_batch_dependabot_resolver_evidence(
            payload,
            contract_id=contract_id,
            relative_path=relative,
        )

    status = payload.get("status")
    queued = payload.get("queued")
    requested, created = payload.get("requested"), payload.get("created")
    skipped = payload.get("skipped")
    errors = payload.get("errors")
    failure = payload.get("failure")
    failure_payload = dict(failure) if isinstance(failure, Mapping) else {}
    failure_code = str(failure_payload.get("code") or "").strip()
    failure_message = str(failure_payload.get("message") or "").strip()
    metadata = {
        "terminalContractId": contract_id,
        "terminalContractEvidencePath": relative,
        "terminalContractExecutionRef": expected_execution,
        "queuedChildCount": created if isinstance(created, int) else 0,
        "queuedChildren": queued if isinstance(queued, list) else [],
    }
    if failure_code:
        metadata["terminalFailureCode"] = failure_code
    if failure_message:
        metadata["terminalFailureMessage"] = failure_message

    # Input validation can fail before a trustworthy target list exists. The
    # portable helper still binds that terminal failure to this execution and
    # guarantees that no child side effect occurred.
    if status == "failed" and failure_code == "BATCH_FANOUT_INPUT_INVALID":
        if (
            not isinstance(requested, int)
            or requested < 0
            or created != 0
            or queued != []
            or skipped != []
            or not isinstance(errors, list)
            or not errors
            or not failure_message
        ):
            return TerminalEvidenceEvaluation(
                False, "INVALID_TERMINAL_EVIDENCE", metadata=metadata
            )
        return _failure("BATCH_FANOUT_INPUT_INVALID", metadata=metadata)

    targets_relative = str(
        contract.get("targetsRelativePath")
        or "artifacts/batch-workflows-targets.json"
    ).replace("\\", "/")
    targets_relative_path = Path(targets_relative)
    if (
        not targets_relative
        or targets_relative_path.is_absolute()
        or ".." in targets_relative_path.parts
    ):
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE")
    targets_root = workspace
    targets_evidence_relative = targets_relative_path
    if artifact_spool_path and targets_relative_path.parts[:1] == ("artifacts",):
        spool_root = Path(artifact_spool_path).resolve()
        spool_relative = Path(*targets_relative_path.parts[1:])
        spool_targets_path = (spool_root / spool_relative).resolve()
        try:
            spool_targets_path.relative_to(spool_root)
        except ValueError:
            return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE")
        if spool_targets_path.is_file():
            targets_root = spool_root
            targets_evidence_relative = spool_relative
    targets_path = (targets_root / targets_evidence_relative).resolve()
    try:
        targets_path.relative_to(targets_root)
        expected_digest = hashlib.sha256(targets_path.read_bytes()).hexdigest()
    except (ValueError, OSError):
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE")
    if payload.get("targetsSha256") != expected_digest:
        return TerminalEvidenceEvaluation(False, "STALE_TERMINAL_EVIDENCE")
    if status == "running":
        return TerminalEvidenceEvaluation(
            False, "INCOMPLETE_TERMINAL_CONTRACT", metadata=metadata
        )
    if status == "partial_failure":
        return TerminalEvidenceEvaluation(False, "BATCH_FANOUT_PARTIAL_FAILURE", metadata=metadata)
    if status == "failed":
        return TerminalEvidenceEvaluation(False, "BATCH_FANOUT_FAILED", metadata=metadata)
    if (
        not isinstance(requested, int) or requested < 0
        or not isinstance(created, int) or created < 0
        or not isinstance(queued, list)
        or not isinstance(skipped, list)
        or not isinstance(errors, list)
        or len(queued) != created
        or created + len(skipped) + len(errors) < requested
    ):
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE", metadata=metadata)
    if status == "no_op" and requested == 0 and created == 0 and queued == []:
        return _success(metadata)
    if (
        status == "queued" and requested > 0
        and created == requested and not errors and not skipped
        and all(isinstance(item, dict) and item.get("executionId") for item in queued)
    ):
        return _success(metadata)
    return TerminalEvidenceEvaluation(False, "INCOMPLETE_TERMINAL_CONTRACT", metadata=metadata)
