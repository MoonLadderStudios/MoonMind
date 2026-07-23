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
    backslash_unsafe = {**_contract(), "relativePath": "..\\result.json"}
    assert evaluate_terminal_evidence(backslash_unsafe, workspace_path=str(tmp_path)).failure_code == "INVALID_TERMINAL_EVIDENCE_PATH"


def test_batch_terminal_reads_result_and_targets_from_artifact_spool(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    _write(workspace, status="queued", requested=1, queued=[{"executionId": "child-1"}])
    spool.mkdir()
    (workspace / "artifacts/batch-workflows-result.json").replace(
        spool / "batch-workflows-result.json"
    )
    (workspace / "artifacts/batch-workflows-targets.json").replace(
        spool / "batch-workflows-targets.json"
    )
    result = evaluate_terminal_evidence(
        _contract(),
        workspace_path=str(workspace),
        artifact_spool_path=str(spool),
    )
    assert result.satisfied is True
    assert result.metadata["queuedChildren"] == [{"executionId": "child-1"}]


def test_batch_terminal_accepts_preflight_failure_without_target_list(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.batch-workflows-result.v1",
                "contractId": "batch_workflows_fanout.v1",
                "executionRef": "step:1",
                "targetsSha256": None,
                "status": "failed",
                "requested": 56,
                "created": 0,
                "queued": [],
                "skipped": [],
                "errors": [
                    {
                        "code": "BATCH_FANOUT_INPUT_INVALID",
                        "error": "Range contains 56 issues; maximum is 25.",
                    }
                ],
                "failure": {
                    "code": "BATCH_FANOUT_INPUT_INVALID",
                    "message": "Range contains 56 issues; maximum is 25.",
                },
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_terminal_evidence(
        _contract(),
        workspace_path=str(workspace),
        artifact_spool_path=str(spool),
    )

    assert result.satisfied is False
    assert result.failure_code == "BATCH_FANOUT_INPUT_INVALID"
    assert result.missing_evidence == ()
    assert result.metadata["terminalContractExecutionRef"] == "step:1"
    assert (
        result.metadata["terminalFailureMessage"]
        == "Range contains 56 issues; maximum is 25."
    )


def _dependabot_contract(execution_ref: str = "step:dependabot") -> dict[str, str]:
    return {
        "contractId": "batch_dependabot_resolver_fanout.v1",
        "relativePath": "artifacts/batch_dependabot_resolver_result.json",
        "expectedSchemaVersion": "moonmind.batch-dependabot-resolver-result.v1",
        "executionRef": execution_ref,
    }


def _write_dependabot_result(
    workspace: Path,
    *,
    status: str,
    requested: int,
    queued: list[dict],
    skipped: list[dict] | None = None,
    errors: list[dict] | None = None,
    would_queue: list[dict] | None = None,
    failure_code: str | None = None,
    dry_run: bool | None = None,
) -> None:
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "moonmind.batch-dependabot-resolver-result.v1",
        "contractId": "batch_dependabot_resolver_fanout.v1",
        "executionRef": "step:dependabot",
        "status": status,
        "failureCode": failure_code,
        "dryRun": status == "dry_run" if dry_run is None else dry_run,
        "requested": requested,
        "created": len(queued),
        "queued": queued,
        "wouldQueue": would_queue or [],
        "skipped": skipped or [],
        "errors": errors or [],
    }
    (artifacts / "batch_dependabot_resolver_result.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_batch_dependabot_terminal_accepts_queued_and_no_op_results(
    tmp_path: Path,
) -> None:
    queued = [{"pr": 1, "workflowId": "mm:child-1"}]
    skipped = [{"pr": 2, "reason": "not-dependabot-author"}]
    _write_dependabot_result(
        tmp_path,
        status="queued",
        requested=2,
        queued=queued,
        skipped=skipped,
    )
    queued_result = evaluate_terminal_evidence(
        _dependabot_contract(), workspace_path=str(tmp_path)
    )
    assert queued_result.satisfied is True
    assert queued_result.metadata["queuedChildren"] == queued

    _write_dependabot_result(
        tmp_path,
        status="no_op",
        requested=1,
        queued=[],
        skipped=skipped,
    )
    no_op_result = evaluate_terminal_evidence(
        _dependabot_contract(), workspace_path=str(tmp_path)
    )
    assert no_op_result.satisfied is True


def test_batch_dependabot_terminal_accepts_dry_run(tmp_path: Path) -> None:
    _write_dependabot_result(
        tmp_path,
        status="dry_run",
        requested=1,
        queued=[],
        would_queue=[{"pr": 1, "idempotencyKey": "key"}],
    )
    result = evaluate_terminal_evidence(
        _dependabot_contract(), workspace_path=str(tmp_path)
    )
    assert result.satisfied is True


def test_batch_dependabot_terminal_rejects_drift_stale_and_bad_accounting(
    tmp_path: Path,
) -> None:
    skipped = [{"pr": 1, "reason": "title-mismatch"}]
    _write_dependabot_result(
        tmp_path,
        status="failed",
        requested=1,
        queued=[],
        skipped=skipped,
        failure_code="DEPENDABOT_TITLE_CONTRACT_DRIFT",
        dry_run=True,
    )
    drift = evaluate_terminal_evidence(
        _dependabot_contract(), workspace_path=str(tmp_path)
    )
    assert drift.failure_code == "DEPENDABOT_TITLE_CONTRACT_DRIFT"

    stale = evaluate_terminal_evidence(
        _dependabot_contract("other-step"), workspace_path=str(tmp_path)
    )
    assert stale.failure_code == "STALE_TERMINAL_EVIDENCE"

    _write_dependabot_result(
        tmp_path,
        status="queued",
        requested=2,
        queued=[{"pr": 1, "workflowId": "mm:child-1"}],
    )
    invalid = evaluate_terminal_evidence(
        _dependabot_contract(), workspace_path=str(tmp_path)
    )
    assert invalid.failure_code == "INVALID_TERMINAL_EVIDENCE"


def test_pr_resolver_terminal_requires_result_and_publish_evidence(tmp_path: Path) -> None:
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step-1",
    }
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "mergeAutomationDisposition": "merged",
                "executionRef": "step-1",
            }
        ),
        encoding="utf-8",
    )
    missing_publish = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))
    assert missing_publish.failure_code == "INCOMPLETE_TERMINAL_CONTRACT"
    publish_path = tmp_path / "artifacts/publish_result.json"
    publish_path.parent.mkdir()
    publish_path.write_text("{}", encoding="utf-8")
    assert evaluate_terminal_evidence(contract, workspace_path=str(tmp_path)).satisfied


def test_pr_resolver_terminal_accepts_publish_evidence_from_spool(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    result_path = workspace / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "mergeAutomationDisposition": "already_merged",
                "executionRef": "step-1",
            }
        ),
        encoding="utf-8",
    )
    spool.mkdir()
    (spool / "publish_result.json").write_text("{}", encoding="utf-8")
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step-1",
    }

    result = evaluate_terminal_evidence(
        contract,
        workspace_path=str(workspace),
        artifact_spool_path=str(spool),
    )

    assert result.satisfied is True


@pytest.mark.parametrize(
    ("disposition", "failure_code"),
    [
        ("manual_review", "PR_RESOLVER_MANUAL_REVIEW"),
        ("failed", "PR_RESOLVER_FAILED"),
    ],
)
def test_pr_resolver_terminal_rejects_unsuccessful_dispositions(
    tmp_path: Path, disposition: str, failure_code: str
) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {"mergeAutomationDisposition": disposition, "executionRef": "step-1"}
        ),
        encoding="utf-8",
    )
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step-1",
    }

    result = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))

    assert result.satisfied is False
    assert result.failure_code == failure_code


def test_pr_resolver_terminal_classifies_reenter_gate_as_continuation(tmp_path: Path) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "mergeAutomationDisposition": "reenter_gate",
                "executionRef": "step-1",
                "gatedContinuation": {
                    "schemaVersion": "gated-continuation/v1",
                    "gateType": "merge_automation",
                    "action": "reenter_gate",
                    "reason": "codex_review_grace_wait",
                    "notBefore": "2026-07-12T05:05:49Z",
                },
            }
        ),
        encoding="utf-8",
    )
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step-1",
    }

    result = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))

    assert result.outcome == "continuation_requested"
    assert result.satisfied is False
    assert result.failure_code is None
    assert result.metadata["gatedContinuation"]["action"] == "reenter_gate"
    assert result.metadata["terminalContractExecutionRef"] == "step-1"


def test_pr_resolver_terminal_rejects_stale_execution(tmp_path: Path) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {"mergeAutomationDisposition": "merged", "executionRef": "old-step"}
        ),
        encoding="utf-8",
    )
    contract = {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "current-step",
    }

    result = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))

    assert result.satisfied is False
    assert result.failure_code == "STALE_TERMINAL_EVIDENCE"
