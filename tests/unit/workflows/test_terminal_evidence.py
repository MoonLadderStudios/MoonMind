from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence


def _contract(execution_ref: str = "step:1") -> dict[str, str]:
    return {
        "contractId": "batch_workflows_fanout.v1",
        "relativePath": "artifacts/batch-workflows-result.json",
        "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
        "executionRef": execution_ref,
    }


def _write(workspace: Path, *, status: str, requested: int, queued: list[dict]) -> None:
    targets = workspace / "artifacts/batch-workflows-targets.json"
    targets.parent.mkdir(parents=True, exist_ok=True)
    targets.write_text("[]", encoding="utf-8")
    payload = {
        "schemaVersion": "moonmind.batch-workflows-result.v1",
        "contractId": "batch_workflows_fanout.v1",
        "executionRef": "step:1",
        "targetsSha256": hashlib.sha256(b"[]").hexdigest(),
        "status": status,
        "requested": requested,
        "created": len(queued),
        "queued": queued,
        "skipped": [],
        "errors": [],
    }
    (workspace / "artifacts/batch-workflows-result.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [("running", "INCOMPLETE_TERMINAL_CONTRACT"),
     ("partial_failure", "BATCH_FANOUT_PARTIAL_FAILURE"),
     ("failed", "BATCH_FANOUT_FAILED")],
)
def test_batch_terminal_failure_states(tmp_path: Path, status: str, code: str) -> None:
    _write(tmp_path, status=status, requested=1, queued=[])
    result = evaluate_terminal_evidence(_contract(), workspace_path=str(tmp_path))
    assert result.satisfied is False
    assert result.failure_code == code


def test_batch_terminal_accepts_identity_bound_queued_result(tmp_path: Path) -> None:
    _write(tmp_path, status="queued", requested=1, queued=[{"executionId": "child-1"}])
    assert evaluate_terminal_evidence(_contract(), workspace_path=str(tmp_path)).satisfied


def test_batch_terminal_accepts_explicit_no_op(tmp_path: Path) -> None:
    _write(tmp_path, status="no_op", requested=0, queued=[])
    assert evaluate_terminal_evidence(_contract(), workspace_path=str(tmp_path)).satisfied


def test_batch_terminal_rejects_missing_stale_and_traversal(tmp_path: Path) -> None:
    missing = evaluate_terminal_evidence(_contract(), workspace_path=str(tmp_path))
    assert missing.failure_code == "INCOMPLETE_TERMINAL_CONTRACT"
    _write(tmp_path, status="no_op", requested=0, queued=[])
    assert evaluate_terminal_evidence(_contract("other"), workspace_path=str(tmp_path)).failure_code == "STALE_TERMINAL_EVIDENCE"
    unsafe = {**_contract(), "relativePath": "../result.json"}
    assert evaluate_terminal_evidence(unsafe, workspace_path=str(tmp_path)).failure_code == "INVALID_TERMINAL_EVIDENCE_PATH"
