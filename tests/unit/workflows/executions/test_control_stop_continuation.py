from copy import deepcopy

import pytest
from pydantic import ValidationError

from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationBudgetGrant,
    ControlStopContinuationContract,
    ControlStopContinuationError,
)


def _payload() -> dict:
    return {
        "targetKind": "control_stop",
        "phase": "continue_to_remediation",
        "sourceOutcomeKind": "workflow_gate",
        "sourceWorkflowId": "source-workflow",
        "sourceRunId": "source-run",
        "ownerType": "user",
        "ownerId": "user-123",
        "controlStopId": "verify:control-stop:6",
        "semanticVerdict": "ADDITIONAL_WORK_NEEDED",
        "stopReason": "remediation_budget_exhausted",
        "gateResultRef": "artifact://gate/6",
        "gateResultDigest": "gate-digest",
        "remainingWorkRef": "artifact://remaining/6",
        "remainingWorkDigest": "remaining-digest",
        "workspaceHeadRef": "artifact://workspace/C6",
        "workspaceHeadDigest": f"sha256:{'a' * 64}",
        "workspaceBaseCommit": "abc123",
        "workspaceManifestRef": "artifact://workspace/C6/manifest",
        "workspaceManifestDigest": f"sha256:{'b' * 64}",
        "repository": "MoonLadderStudios/MoonMind",
        "checkpointKind": "worktree_archive",
        "checkpointBoundary": "after_gate",
        "terminalHead": True,
        "taskInputSnapshotRef": "artifact://input",
        "taskInputSnapshotDigest": "input-digest",
        "planRef": "artifact://plan",
        "planDigest": "plan-digest",
        "lane": {
            "runtime": "external/omnigent",
            "product": "codex-native",
            "executionProfileId": "codex-default",
            "providerProfileId": "codex-oauth-primary",
            "providerProfileGeneration": "generation-4",
            "hostProfile": "omnigent-host-codex",
            "launchPolicyRef": "artifact://launch-policy",
            "launchPolicyDigest": "launch-policy-digest",
            "capabilitySnapshotRef": "artifact://capability",
            "capabilitySnapshotDigest": "capability-digest",
            "effectiveLaunchSnapshotRef": "artifact://effective-launch",
            "effectiveLaunchSnapshotDigest": "effective-launch-digest",
            "checkpointBoundarySupport": {
                "after_gate": ["continue_to_remediation"]
            },
        },
        "preservedSteps": [
            {
                "sourceStepExecutionId": "source:implement:1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "terminalDisposition": "accepted",
                "outputRefs": {"result": "artifact://implementation"},
                "checkpointRef": "artifact://workspace/C5",
                "dependencyOutputSignatures": {},
            },
            {
                "sourceStepExecutionId": "source:verify:6",
                "logicalStepId": "verify",
                "executionOrdinal": 6,
                "terminalDisposition": "accepted_control_result",
                "outputRefs": {"gate": "artifact://gate/6"},
                "checkpointRef": "artifact://workspace/C6",
                "dependencyOutputSignatures": {"implement": "signature"},
                "semanticVerdict": "ADDITIONAL_WORK_NEEDED",
            },
        ],
        "sideEffects": [
            {
                "operation": "github.issue.update",
                "evidenceRef": "artifact://effect/github",
                "disposition": "already_performed",
            }
        ],
        "sourceBudget": {
            "maxAttempts": 6,
            "consumedAttempts": 6,
            "exhaustedDimension": "remediation_attempts",
        },
        "continuationBudget": {
            "grantId": "grant-1",
            "maxAttempts": 2,
            "maxConsecutiveNoProgressAttempts": 1,
        },
        "deploymentGeneration": "control-stop-2026-07",
        "deploymentPromoted": True,
        "restoreCapabilitySetVersion": "runtime-execution-capabilities-v3",
        "restoreCapabilityDigest": "capability-digest",
        "idempotencyKey": "operator-request-1",
    }


def test_valid_control_stop_starts_with_remediation_on_recovered_candidate() -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())

    entry = contract.workflow_entry()

    assert entry["candidateState"] == "recovered_candidate"
    assert entry["nextSemanticOperation"] == "remediation"
    assert entry["nextAttemptOrdinal"] == 7
    assert entry["runtime"] == "external/omnigent"
    assert entry["providerProfileId"] == "codex-oauth-primary"
    assert ":workspace" in entry["destinationWorkspaceId"]


def test_duplicate_contract_has_one_deterministic_destination() -> None:
    first = ControlStopContinuationContract.model_validate(_payload())
    second = ControlStopContinuationContract.model_validate(deepcopy(_payload()))
    assert first.destination_workflow_id == second.destination_workflow_id

    changed = _payload()
    changed["idempotencyKey"] = "operator-request-2"
    assert (
        ControlStopContinuationContract.model_validate(changed).destination_workflow_id
        != first.destination_workflow_id
    )


def test_restore_request_uses_distinct_deterministic_destination() -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())

    request = contract.restore_request(destination_run_id="destination-run")

    assert request["source"]["workflowId"] == "source-workflow"
    assert request["recoveryIdentity"]["workflowId"] == contract.destination_workflow_id
    assert request["recoveryIdentity"]["runId"] == "destination-run"
    assert request["destination"]["agentRunId"] == contract.destination_workspace_id
    assert request["checkpoint"]["archiveDigest"] == f"sha256:{'a' * 64}"
    assert request["checkpoint"]["manifestDigest"] == f"sha256:{'b' * 64}"
    assert request["resumePhase"] == "continue_to_remediation"
    assert request["idempotencyKey"] == f"{contract.destination_workflow_id}:restore"


def test_attempt_requests_freeze_profile_and_have_distinct_retry_safe_identities() -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())
    locator = {
        "kind": "managed_runtime",
        "runtimeId": "codex_cli",
        "agentRunId": contract.destination_workspace_id,
        "relativePath": "repo",
    }

    remediation = contract.remediation_request(
        destination_run_id="destination-run",
        destination_workspace_locator=locator,
        attempt=7,
    )
    capture = contract.capture_request(
        destination_run_id="destination-run",
        destination_workspace_locator=locator,
        attempt=7,
    )
    verification = contract.verification_request(
        destination_run_id="destination-run",
        destination_workspace_locator=locator,
        attempt=7,
        workspace_head_ref="artifact://workspace/C7",
        remaining_work_ref="artifact://remaining/7",
    )

    assert remediation["executionProfileRef"] == "codex-oauth-primary"
    assert verification["executionProfileRef"] == "codex-oauth-primary"
    assert remediation["idempotencyKey"].endswith(":remediation:execution:7")
    assert capture["idempotencyKey"].endswith(":remediation:7:capture")
    assert verification["idempotencyKey"].endswith(":verification:execution:7")
    assert capture["workspaceLocator"] == locator


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("remainingWorkRef",), ""),
        (("terminalHead",), False),
        (("deploymentPromoted",), False),
        (("lane", "runtime"), "managed/codex"),
        (("lane", "providerProfileId"), ""),
        (("continuationBudget", "maxAttempts"), 0),
        (("checkpointBoundary",), "before_execution"),
    ],
)
def test_incomplete_or_unsupported_contract_fails_closed(path, value) -> None:
    payload = _payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        ControlStopContinuationContract.model_validate(payload)


def test_blocked_side_effect_denies_admission() -> None:
    payload = _payload()
    payload["sideEffects"][0]["disposition"] = "blocked"
    with pytest.raises(ValidationError, match="side effects block"):
        ControlStopContinuationContract.model_validate(payload)


def test_negative_verifier_is_preserved_without_failed_step() -> None:
    contract = ControlStopContinuationContract.model_validate(_payload())
    assert all(
        step.terminal_disposition != "failed" for step in contract.preserved_steps
    )
    assert contract.preserved_steps[-1].terminal_disposition == "accepted_control_result"


def test_continuation_budget_is_monotonic_and_bounded() -> None:
    grant = ContinuationBudgetGrant(
        grantId="grant-1",
        maxAttempts=2,
        maxConsecutiveNoProgressAttempts=1,
    )
    after_progress = grant.consume(progress=True)
    after_no_progress = after_progress.consume(progress=False)
    assert after_no_progress.consumed_attempts == 2
    assert after_no_progress.consecutive_no_progress_attempts == 1
    with pytest.raises(
        ControlStopContinuationError, match="continuation_attempt_budget_exhausted"
    ):
        after_no_progress.consume(progress=True)
