from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from moonmind.workflows.temporal.agent_result_payloads import (
    compact_published_agent_run_result_payload,
)
from moonmind.workflows.terminal_evidence import evaluate_terminal_evidence


def _contract(execution_ref: str = "step:1") -> dict[str, str]:
    return {
        "contractId": "batch_workflows_fanout.v1",
        "relativePath": "artifacts/batch-workflows-result.json",
        "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
        "executionRef": execution_ref,
    }


def _auto_publish_contract(skill_id: str = "fix-comments") -> dict[str, str]:
    return {
        "contractId": "auto_publish_terminal.v1",
        "relativePath": "artifacts/publish_result.json",
        "expectedSchemaVersion": "moonmind.publish.auto.v1",
        "executionRef": "step:auto",
        "skillId": skill_id,
    }


def _write_auto_publish_result(
    workspace: Path,
    *,
    skill_id: str = "fix-comments",
    status: str = "verified",
    execution_ref: str = "step:auto",
) -> None:
    path = workspace / "artifacts/publish_result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.publish.auto.v1",
                "mode": "auto",
                "owner": "agent",
                "skillId": skill_id,
                "executionRef": execution_ref,
                "status": status,
                "action": "push" if status == "verified" else "none",
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "feature",
                "localHead": "abc123",
                "remoteBranchHead": "abc123",
                "remoteVerified": status == "verified",
                "pushed": status == "verified",
                "merged": False,
                "prUrl": None,
                "blockedReason": "publish_unavailable" if status == "blocked" else None,
                "verificationCommands": (
                    ["git ls-remote origin refs/heads/feature"]
                    if status == "verified"
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )


def test_auto_publish_terminal_requires_valid_skill_owned_evidence(
    tmp_path: Path,
) -> None:
    missing = evaluate_terminal_evidence(
        _auto_publish_contract(), workspace_path=str(tmp_path)
    )
    assert missing.failure_code == "INCOMPLETE_TERMINAL_CONTRACT"
    assert missing.metadata["terminalContractRetryable"] is True

    _write_auto_publish_result(tmp_path)
    complete = evaluate_terminal_evidence(
        _auto_publish_contract(), workspace_path=str(tmp_path)
    )
    assert complete.satisfied is True
    assert complete.metadata["autoPublishSkillId"] == "fix-comments"

    stale = evaluate_terminal_evidence(
        _auto_publish_contract("fix-ci"), workspace_path=str(tmp_path)
    )
    assert stale.failure_code == "STALE_TERMINAL_EVIDENCE"
    assert stale.metadata["terminalContractRetryable"] is True

    _write_auto_publish_result(tmp_path, execution_ref="step:old")
    wrong_execution = evaluate_terminal_evidence(
        _auto_publish_contract(), workspace_path=str(tmp_path)
    )
    assert wrong_execution.failure_code == "STALE_TERMINAL_EVIDENCE"
    assert wrong_execution.metadata["terminalContractRetryable"] is True


def test_auto_publish_terminal_preserves_explicit_blocked_outcome(
    tmp_path: Path,
) -> None:
    _write_auto_publish_result(tmp_path, status="blocked")
    blocked = evaluate_terminal_evidence(
        _auto_publish_contract(), workspace_path=str(tmp_path)
    )
    assert blocked.failure_code == "AUTO_PUBLISH_BLOCKED"
    assert blocked.metadata["terminalContractRetryable"] is False


def test_auto_publish_evidence_ref_survives_workflow_history_compaction() -> None:
    compacted = compact_published_agent_run_result_payload(
        {
            "summary": "done",
            "metadata": {
                "publishEvidence": "art-publish-evidence",
                "oversizedAuxiliaryObservation": "x" * 200_000,
                "providerFailure": {"detail": "y" * 200_000},
            },
        }
    )

    assert compacted["metadata"]["publishEvidence"] == "art-publish-evidence"


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


def test_batch_terminal_reads_spooled_result_with_workspace_targets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    _write(workspace, status="queued", requested=1, queued=[{"executionId": "child-1"}])
    spool.mkdir()
    (workspace / "artifacts/batch-workflows-result.json").replace(
        spool / "batch-workflows-result.json"
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
    _write_auto_publish_result(
        tmp_path, skill_id="pr-resolver", execution_ref="step-1"
    )
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
    _write_auto_publish_result(
        workspace, skill_id="pr-resolver", execution_ref="step-1"
    )
    (workspace / "artifacts/publish_result.json").replace(
        spool / "publish_result.json"
    )
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


def test_pr_resolver_terminal_classifies_review_request_as_continuation(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "mergeAutomationDisposition": "request_review",
                "executionRef": "step-1",
                "gatedContinuation": {
                    "schemaVersion": "gated-continuation/v2",
                    "gateType": "merge_automation",
                    "action": "request_review",
                    "provider": "codex",
                    "reason": "fresh_review_required_after_remediation",
                    "executionRef": "step-1",
                    "headSha": "abc1234",
                    "progressSignature": "abc1234||",
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
    assert result.metadata["gatedContinuation"]["action"] == "request_review"
    assert result.metadata["gatedContinuation"]["provider"] == "codex"
    assert result.metadata["terminalContractExecutionRef"] == "step-1"


@pytest.mark.parametrize(
    "continuation",
    [
        None,
        {
            "schemaVersion": "gated-continuation/v1",
            "gateType": "merge_automation",
            "action": "request_review",
            "provider": "codex",
            "reason": "fresh_review_required_after_remediation",
            "executionRef": "step-1",
            "headSha": "abc1234",
        },
        {
            "schemaVersion": "gated-continuation/v2",
            "gateType": "merge_automation",
            "action": "request_review",
            "provider": "",
            "reason": "fresh_review_required_after_remediation",
            "executionRef": "step-1",
            "headSha": "abc1234",
        },
    ],
)
def test_pr_resolver_terminal_rejects_invalid_review_request_continuation(
    tmp_path: Path,
    continuation: dict[str, object] | None,
) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True)
    payload: dict[str, object] = {
        "mergeAutomationDisposition": "request_review",
        "executionRef": "step-1",
    }
    if continuation is not None:
        payload["gatedContinuation"] = continuation
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    result = evaluate_terminal_evidence(
        {
            "contractId": "pr_resolver_terminal.v1",
            "relativePath": "var/pr_resolver/result.json",
            "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
            "executionRef": "step-1",
        },
        workspace_path=str(tmp_path),
    )

    assert result.outcome == "terminal_failure"
    assert result.failure_code == "MALFORMED_TERMINAL_EVIDENCE"


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


def test_pr_resolver_review_clean_requires_publish_evidence(tmp_path: Path) -> None:
    """fix_only success still has to prove its verified branch head."""

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
                "mergeAutomationDisposition": "review_clean",
                "executionRef": "step-1",
            }
        ),
        encoding="utf-8",
    )

    missing_publish = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))
    assert missing_publish.failure_code == "INCOMPLETE_TERMINAL_CONTRACT"

    publish_path = tmp_path / "artifacts/publish_result.json"
    publish_path.parent.mkdir(parents=True, exist_ok=True)
    publish_path.write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.publish.auto.v1",
                "mode": "auto",
                "owner": "agent",
                "skillId": "pr-resolver",
                "executionRef": "step-1",
                "status": "no_op_verified",
                "action": "none",
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "feature",
                "localHead": "abc123",
                "remoteBranchHead": "abc123",
                "remoteVerified": True,
                "pushed": False,
                "merged": False,
                "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
                "blockedReason": None,
                "verificationCommands": [
                    "git ls-remote origin refs/heads/feature",
                ],
            }
        ),
        encoding="utf-8",
    )

    evaluation = evaluate_terminal_evidence(contract, workspace_path=str(tmp_path))
    assert evaluation.satisfied
    assert evaluation.metadata["mergeAutomationDisposition"] == "review_clean"


def test_pr_resolver_rejects_an_unknown_terminal_disposition(tmp_path: Path) -> None:
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
                "mergeAutomationDisposition": "review_kinda_clean",
                "executionRef": "step-1",
            }
        ),
        encoding="utf-8",
    )

    assert (
        evaluate_terminal_evidence(
            contract, workspace_path=str(tmp_path)
        ).failure_code
        == "MALFORMED_TERMINAL_EVIDENCE"
    )


def _review_clean_contract() -> dict[str, str]:
    return {
        "contractId": "pr_resolver_terminal.v1",
        "relativePath": "var/pr_resolver/result.json",
        "expectedSchemaVersion": "moonmind.pr-resolver-result.v1",
        "executionRef": "step-1",
    }


def _write_review_clean_evidence(
    tmp_path: Path,
    *,
    result_overrides: dict[str, object] | None = None,
    publish_overrides: dict[str, object] | None = None,
) -> None:
    result_path = tmp_path / "var/pr_resolver/result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {
        "mergeAutomationDisposition": "review_clean",
        "executionRef": "step-1",
        "status": "review_clean",
        "merge_outcome": "skipped",
    }
    result.update(result_overrides or {})
    result_path.write_text(json.dumps(result), encoding="utf-8")

    publish_path = tmp_path / "artifacts/publish_result.json"
    publish_path.parent.mkdir(parents=True, exist_ok=True)
    publish: dict[str, object] = {
        "schemaVersion": "moonmind.publish.auto.v1",
        "mode": "auto",
        "owner": "agent",
        "skillId": "pr-resolver",
        "executionRef": "step-1",
        "status": "verified",
        "action": "push",
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "feature",
        "localHead": "abc123",
        "remoteBranchHead": "abc123",
        "remoteVerified": True,
        "pushed": True,
        "merged": False,
        "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
        "blockedReason": None,
        "verificationCommands": ["git ls-remote origin refs/heads/feature"],
    }
    publish.update(publish_overrides or {})
    publish_path.write_text(json.dumps(publish), encoding="utf-8")


def test_review_clean_rejects_publish_evidence_that_merged_the_pr(
    tmp_path: Path,
) -> None:
    """A fix_only run that merged must never close as a no-merge success.

    The generic auto-publish validator accepts `status=verified`, `action=merge`,
    `merged=true`, so without disposition-specific invariants an unauthorized
    irreversible merge would be reported as `review_clean`.
    """

    _write_review_clean_evidence(
        tmp_path,
        publish_overrides={
            "action": "merge",
            "merged": True,
            "verificationCommands": ["gh pr view <pr-url> --json state"],
        },
    )

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is False
    assert evaluation.failure_code == "UNAUTHORIZED_MERGE_EVIDENCE"


@pytest.mark.parametrize(
    "publish_overrides",
    [
        {"merged": True},
        {"action": "merge"},
    ],
)
def test_review_clean_requires_unmerged_publish_evidence(
    tmp_path: Path, publish_overrides: dict[str, object]
) -> None:
    _write_review_clean_evidence(tmp_path, publish_overrides=publish_overrides)

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is False
    assert evaluation.failure_code == "UNAUTHORIZED_MERGE_EVIDENCE"


def test_review_clean_still_requires_a_verified_remote_head(tmp_path: Path) -> None:
    """The remote-head invariant stays owned by the auto-publish parser."""

    _write_review_clean_evidence(
        tmp_path, publish_overrides={"remoteVerified": False}
    )

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is False
    assert evaluation.failure_code == "MALFORMED_TERMINAL_EVIDENCE"


@pytest.mark.parametrize(
    "result_overrides",
    [
        {"status": "merged"},
        {"merge_outcome": "merged"},
        {"merge_outcome": "auto_merge_enabled"},
    ],
)
def test_review_clean_rejects_a_resolver_result_that_claims_a_merge(
    tmp_path: Path, result_overrides: dict[str, object]
) -> None:
    _write_review_clean_evidence(tmp_path, result_overrides=result_overrides)

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is False
    assert evaluation.failure_code == "UNAUTHORIZED_MERGE_EVIDENCE"


def test_review_clean_accepts_a_remediated_push_without_a_merge(
    tmp_path: Path,
) -> None:
    """The ordinary fix_only terminal: remediation pushed, nothing merged."""

    _write_review_clean_evidence(tmp_path)

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is True
    assert evaluation.metadata["mergeAutomationDisposition"] == "review_clean"
    assert evaluation.metadata["autoPublishMerged"] is False


def test_merged_disposition_still_accepts_merge_publish_evidence(
    tmp_path: Path,
) -> None:
    """The no-merge invariants must not leak into the merged disposition."""

    _write_review_clean_evidence(
        tmp_path,
        result_overrides={
            "mergeAutomationDisposition": "merged",
            "status": "merged",
            "merge_outcome": "merged",
        },
        publish_overrides={"action": "merge", "merged": True},
    )

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is True
    assert evaluation.metadata["mergeAutomationDisposition"] == "merged"


def test_review_clean_success_keeps_the_resolver_contract_identity(
    tmp_path: Path,
) -> None:
    """Publication facts must not overwrite this contract's own identity."""

    _write_review_clean_evidence(tmp_path)

    evaluation = evaluate_terminal_evidence(
        _review_clean_contract(), workspace_path=str(tmp_path)
    )

    assert evaluation.satisfied is True
    assert evaluation.metadata["terminalContractId"] == "pr_resolver_terminal.v1"
    assert (
        evaluation.metadata["terminalContractEvidencePath"]
        == "var/pr_resolver/result.json"
    )
    assert evaluation.metadata["autoPublishAction"] == "push"
