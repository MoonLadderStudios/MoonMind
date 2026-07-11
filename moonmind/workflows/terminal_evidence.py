"""Trusted registry for agent-owned terminal evidence contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class TerminalEvidenceEvaluation:
    satisfied: bool
    failure_code: str | None = None
    missing_evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def evaluate_terminal_evidence(
    contract: Mapping[str, Any], *, workspace_path: str
) -> TerminalEvidenceEvaluation:
    contract_id = str(contract.get("contractId") or contract.get("contract_id") or "")
    if contract_id != "batch_workflows_fanout.v1":
        return TerminalEvidenceEvaluation(False, "UNSUPPORTED_TERMINAL_CONTRACT")
    relative = str(contract.get("relativePath") or contract.get("relative_path") or "")
    relative_path = Path(relative)
    workspace = Path(workspace_path).resolve()
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE_PATH")
    evidence_path = (workspace / relative_path).resolve()
    try:
        evidence_path.relative_to(workspace)
    except ValueError:
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE_PATH")
    if not evidence_path.is_file():
        return TerminalEvidenceEvaluation(
            False, "INCOMPLETE_TERMINAL_CONTRACT", (relative,)
        )
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TerminalEvidenceEvaluation(False, "MALFORMED_TERMINAL_EVIDENCE")
    if not isinstance(payload, dict):
        return TerminalEvidenceEvaluation(False, "MALFORMED_TERMINAL_EVIDENCE")
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
    targets_relative = str(contract.get("targetsRelativePath") or "artifacts/batch-workflows-targets.json")
    targets_path = (workspace / targets_relative).resolve()
    try:
        targets_path.relative_to(workspace)
        expected_digest = hashlib.sha256(targets_path.read_bytes()).hexdigest()
    except (ValueError, OSError):
        return TerminalEvidenceEvaluation(False, "INVALID_TERMINAL_EVIDENCE")
    if payload.get("targetsSha256") != expected_digest:
        return TerminalEvidenceEvaluation(False, "STALE_TERMINAL_EVIDENCE")
    status = payload.get("status")
    queued = payload.get("queued")
    requested, created = payload.get("requested"), payload.get("created")
    skipped = payload.get("skipped")
    errors = payload.get("errors")
    metadata = {
        "terminalContractId": contract_id,
        "terminalContractEvidencePath": relative,
        "queuedChildCount": created if isinstance(created, int) else 0,
        "queuedChildren": queued if isinstance(queued, list) else [],
    }
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
        return TerminalEvidenceEvaluation(True, metadata=metadata)
    if (
        status == "queued" and requested > 0
        and created == requested and not errors and not skipped
        and all(isinstance(item, dict) and item.get("executionId") for item in queued)
    ):
        return TerminalEvidenceEvaluation(True, metadata=metadata)
    return TerminalEvidenceEvaluation(False, "INCOMPLETE_TERMINAL_CONTRACT", metadata=metadata)
